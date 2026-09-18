from __future__ import annotations

import time
from pathlib import Path

import pytest

from app.ai import ModelResponse
from app.agent_runtime import (
    DurableAgentRuntime,
    FileAgentSessionStore,
    PermissionMode,
    ToolRegistry,
)
from app.app_server import LoomAppServerService


class RecordingPlatform:
    def __init__(self, responses) -> None:
        self.responses = list(responses)
        self.requests = []

    def execute_chat(self, _profile_id, request):
        self.requests.append(request)
        if not self.responses:
            raise AssertionError("scripted platform ran out of responses")
        return self.responses.pop(0)


def build_service(tmp_path: Path, responses):
    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    workspace.mkdir(exist_ok=True)
    store = FileAgentSessionStore(home)
    platform = RecordingPlatform(responses)
    runtime = DurableAgentRuntime(
        platform=platform,
        store=store,
        tools=ToolRegistry(),
        default_permission_mode=PermissionMode.APPROVAL,
        auto_drain_queue=False,
    )
    service = LoomAppServerService(
        runtime=runtime,
        store=store,
        model="test-model",
        default_workspace=workspace,
        default_permission_mode=PermissionMode.APPROVAL,
    )
    return service, runtime, store, platform, workspace


def wait_until(predicate, *, timeout=3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.01)
    raise AssertionError("timed out waiting for durable app-server state")


def test_thread_start_replays_same_thread_across_service_recreation(tmp_path: Path):
    service, runtime, store, _platform, workspace = build_service(tmp_path, [])
    try:
        first = service.thread_start(
            {
                "workspace": str(workspace),
                "permissionMode": "approval",
                "clientInputId": "thread-key-1",
            }
        )
        rejoined = LoomAppServerService(
            runtime=runtime,
            store=store,
            model="test-model",
            default_workspace=workspace,
            default_permission_mode=PermissionMode.APPROVAL,
        )
        second = rejoined.thread_start(
            {
                "workspace": str(workspace),
                "permissionMode": "approval",
                "clientInputId": "thread-key-1",
            }
        )

        assert first["thread"]["id"] == second["thread"]["id"]
        assert first["idempotentReplay"] is False
        assert second["idempotentReplay"] is True
        sessions = [
            item
            for item in store.root.iterdir()
            if (item / "session.json").is_file()
        ]
        assert len(sessions) == 1
    finally:
        runtime.close()


def test_thread_start_rejects_same_id_for_different_request(tmp_path: Path):
    service, runtime, _store, _platform, workspace = build_service(tmp_path, [])
    other = tmp_path / "other"
    other.mkdir()
    try:
        service.thread_start(
            {
                "workspace": str(workspace),
                "permissionMode": "approval",
                "clientInputId": "thread-key-conflict",
            }
        )
        with pytest.raises(ValueError, match="different request"):
            service.thread_start(
                {
                    "workspace": str(other),
                    "permissionMode": "approval",
                    "clientInputId": "thread-key-conflict",
                }
            )
    finally:
        runtime.close()


def test_turn_start_replays_same_turn_after_response_loss_and_service_restart(tmp_path: Path):
    service, runtime, store, platform, workspace = build_service(
        tmp_path,
        [ModelResponse(text="done once")],
    )
    try:
        thread_id = service.thread_start({"workspace": str(workspace)})["thread"]["id"]
        first = service.turn_start(
            {
                "threadId": thread_id,
                "input": "run once",
                "clientInputId": "turn-key-1",
            }
        )
        turn_id = first["turn"]["id"]
        wait_until(lambda: thread_id not in service.runtime_status()["activeThreadIds"])

        rejoined = LoomAppServerService(
            runtime=runtime,
            store=store,
            model="test-model",
            default_workspace=workspace,
            default_permission_mode=PermissionMode.APPROVAL,
        )
        second = rejoined.turn_start(
            {
                "threadId": thread_id,
                "input": "run once",
                "clientInputId": "turn-key-1",
            }
        )

        assert second["turn"]["id"] == turn_id
        assert second["idempotentReplay"] is True
        assert len(platform.requests) == 1
        snapshot = rejoined.thread_read({"threadId": thread_id})
        assert [turn["id"] for turn in snapshot["turns"]].count(turn_id) == 1
    finally:
        runtime.close()


def test_turn_start_rejects_same_id_for_different_input(tmp_path: Path):
    service, runtime, _store, _platform, workspace = build_service(
        tmp_path,
        [ModelResponse(text="first done")],
    )
    try:
        thread_id = service.thread_start({"workspace": str(workspace)})["thread"]["id"]
        service.turn_start(
            {
                "threadId": thread_id,
                "input": "first",
                "clientInputId": "turn-key-conflict",
            }
        )
        wait_until(lambda: thread_id not in service.runtime_status()["activeThreadIds"])

        with pytest.raises(ValueError, match="different request"):
            service.turn_start(
                {
                    "threadId": thread_id,
                    "input": "second",
                    "clientInputId": "turn-key-conflict",
                }
            )
    finally:
        runtime.close()


def test_crash_after_prepare_before_runtime_adoption_resumes_reserved_turn(tmp_path: Path):
    service, runtime, store, platform, workspace = build_service(
        tmp_path,
        [ModelResponse(text="resumed once")],
    )
    try:
        thread_id = service.thread_start({"workspace": str(workspace)})["thread"]["id"]

        def crash_before_launch(_session_id, _operation):
            raise KeyboardInterrupt("simulated process death after prepare")

        service._launch = crash_before_launch
        with pytest.raises(KeyboardInterrupt):
            service.turn_start(
                {
                    "threadId": thread_id,
                    "input": "resume me",
                    "clientInputId": "turn-key-prepared-crash",
                }
            )

        assert not any(
            event.kind.value == "turn_started"
            for event in store.events(thread_id)
        )
        entry = service.idempotency.get("turn/start", "turn-key-prepared-crash")
        assert entry is not None
        assert entry.state == "prepared"
        reserved_turn_id = entry.object_id

        rejoined = LoomAppServerService(
            runtime=runtime,
            store=store,
            model="test-model",
            default_workspace=workspace,
            default_permission_mode=PermissionMode.APPROVAL,
        )
        replay = rejoined.turn_start(
            {
                "threadId": thread_id,
                "input": "resume me",
                "clientInputId": "turn-key-prepared-crash",
            }
        )
        assert replay["turn"]["id"] == reserved_turn_id
        assert replay["idempotentReplay"] is True
        wait_until(lambda: thread_id not in rejoined.runtime_status()["activeThreadIds"])
        assert len(platform.requests) == 1
    finally:
        runtime.close()


def test_crash_after_durable_turn_started_does_not_reexecute_on_retry(tmp_path: Path):
    service, runtime, store, platform, workspace = build_service(
        tmp_path,
        [ModelResponse(text="adopted once")],
    )
    try:
        thread_id = service.thread_start({"workspace": str(workspace)})["thread"]["id"]

        def run_then_crash(_session_id, operation):
            operation()
            raise KeyboardInterrupt("simulated response loss after durable adoption")

        service._launch = run_then_crash
        with pytest.raises(KeyboardInterrupt):
            service.turn_start(
                {
                    "threadId": thread_id,
                    "input": "only once",
                    "clientInputId": "turn-key-adopted-crash",
                }
            )

        started = [
            event
            for event in store.events(thread_id)
            if event.kind.value == "turn_started"
        ]
        assert len(started) == 1
        adopted_turn_id = started[0].turn_id
        assert len(platform.requests) == 1

        rejoined = LoomAppServerService(
            runtime=runtime,
            store=store,
            model="test-model",
            default_workspace=workspace,
            default_permission_mode=PermissionMode.APPROVAL,
        )
        replay = rejoined.turn_start(
            {
                "threadId": thread_id,
                "input": "only once",
                "clientInputId": "turn-key-adopted-crash",
            }
        )

        assert replay["turn"]["id"] == adopted_turn_id
        assert replay["idempotentReplay"] is True
        assert len(platform.requests) == 1
        assert len(
            [
                event
                for event in store.events(thread_id)
                if event.kind.value == "turn_started"
            ]
        ) == 1
    finally:
        runtime.close()


def test_prepared_attachment_replay_uses_original_staged_copy(tmp_path: Path):
    service, runtime, store, platform, workspace = build_service(
        tmp_path,
        [ModelResponse(text="attachment done")],
    )
    source = tmp_path / "source.txt"
    source.write_text("original bytes", encoding="utf-8")
    try:
        thread_id = service.thread_start({"workspace": str(workspace)})["thread"]["id"]

        def crash_before_launch(_session_id, _operation):
            raise KeyboardInterrupt("simulated crash")

        service._launch = crash_before_launch
        with pytest.raises(KeyboardInterrupt):
            service.turn_start(
                {
                    "threadId": thread_id,
                    "input": "read attachment",
                    "attachments": [{"path": str(source), "name": "source.txt"}],
                    "clientInputId": "turn-key-attachment",
                }
            )

        entry = service.idempotency.get("turn/start", "turn-key-attachment")
        assert entry is not None and entry.payload is not None
        staged_record = entry.payload["staged"][0]
        staged_path = workspace / staged_record["path"]
        assert staged_path.read_text(encoding="utf-8") == "original bytes"

        source.write_text("changed after request", encoding="utf-8")

        rejoined = LoomAppServerService(
            runtime=runtime,
            store=store,
            model="test-model",
            default_workspace=workspace,
            default_permission_mode=PermissionMode.APPROVAL,
        )
        replay = rejoined.turn_start(
            {
                "threadId": thread_id,
                "input": "read attachment",
                "attachments": [{"path": str(source), "name": "source.txt"}],
                "clientInputId": "turn-key-attachment",
            }
        )
        assert replay["turn"]["attachments"][0]["path"] == staged_record["path"]
        wait_until(lambda: thread_id not in rejoined.runtime_status()["activeThreadIds"])
        assert len(platform.requests) == 1
        assert staged_path.read_text(encoding="utf-8") == "original bytes"
        assert len(list(staged_path.parent.iterdir())) == 1
    finally:
        runtime.close()
