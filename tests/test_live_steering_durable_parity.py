from __future__ import annotations

import time
from pathlib import Path

import pytest

from app.ai import ModelResponse, ModelUsage, ToolCall
from app.agent_runtime import (
    AgentTool,
    DurableAgentRuntime,
    FileAgentSessionStore,
    PermissionMode,
    ToolContext,
    ToolEffect,
    ToolRegistry,
    ToolResult,
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


def _wait_until(predicate, *, timeout: float = 3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.01)
    raise AssertionError("timed out waiting for durable steering state")


def _service(tmp_path: Path, responses, *, auto_drain_queue: bool):
    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = FileAgentSessionStore(home)
    platform = RecordingPlatform(responses)
    calls: list[str] = []

    def sensitive_handler(_context: ToolContext, arguments):
        calls.append(str(arguments["value"]))
        return ToolResult(ok=True, content="sensitive action ran")

    sensitive = AgentTool(
        name="sensitive_live_steer",
        description="Sensitive action used by steering parity tests.",
        input_schema={
            "type": "object",
            "properties": {"value": {"type": "string"}},
            "required": ["value"],
            "additionalProperties": False,
        },
        handler=sensitive_handler,
        effect=ToolEffect.SENSITIVE,
    )
    runtime = DurableAgentRuntime(
        platform=platform,
        store=store,
        tools=ToolRegistry((sensitive,)),
        default_permission_mode=PermissionMode.APPROVAL,
        auto_drain_queue=auto_drain_queue,
    )
    service = LoomAppServerService(
        runtime=runtime,
        store=store,
        model="test-model",
        default_workspace=workspace,
        default_permission_mode=PermissionMode.APPROVAL,
    )
    return service, runtime, store, platform, calls, workspace


def _start_waiting_turn(service: LoomAppServerService, workspace: Path):
    thread_id = service.thread_start(
        {"workspace": str(workspace), "permissionMode": "approval"}
    )["thread"]["id"]
    turn_id = service.turn_start(
        {"threadId": thread_id, "input": "perform the sensitive action"}
    )["turn"]["id"]
    _wait_until(lambda: service.thread_read({"threadId": thread_id})["pendingApproval"])
    return thread_id, turn_id


def test_approval_steering_preserves_durable_goal_usage_and_retry_receipt(tmp_path: Path) -> None:
    service, runtime, _store, _platform, calls, workspace = _service(
        tmp_path,
        [
            ModelResponse(
                tool_calls=(
                    ToolCall(
                        call_id="call-goal-steer",
                        name="sensitive_live_steer",
                        arguments={"value": "must-not-run"},
                    ),
                ),
                usage=ModelUsage(input_tokens=5, output_tokens=2, total_tokens=7),
            ),
            ModelResponse(
                text="replanned safely",
                usage=ModelUsage(input_tokens=4, output_tokens=3, total_tokens=7),
            ),
        ],
        auto_drain_queue=False,
    )
    try:
        thread_id = service.thread_start(
            {"workspace": str(workspace), "permissionMode": "approval"}
        )["thread"]["id"]
        runtime.set_goal(thread_id, "Finish safely", token_budget=100)
        turn_id = service.turn_start(
            {"threadId": thread_id, "input": "perform the sensitive action"}
        )["turn"]["id"]
        _wait_until(lambda: service.thread_read({"threadId": thread_id})["pendingApproval"])
        assert runtime.get_goal(thread_id).tokens_used == 7

        first = service.turn_steer(
            {
                "threadId": thread_id,
                "turnId": turn_id,
                "input": "Do not execute that action; choose a safe path.",
                "clientInputId": "durable-goal-steer-1",
            }
        )
        assert first["delivery"] == "approval_superseded"
        _wait_until(
            lambda: service.thread_read({"threadId": thread_id})["thread"]["status"] == "completed"
        )
        assert calls == []
        assert runtime.get_goal(thread_id).tokens_used == 14

        # Simulate a lost RPC response: retrying the exact same id after the turn
        # completed must report the already-applied steering rather than fail or
        # insert a second user message.
        retry = service.turn_steer(
            {
                "threadId": thread_id,
                "turnId": turn_id,
                "input": "Do not execute that action; choose a safe path.",
                "clientInputId": "durable-goal-steer-1",
            }
        )
        assert retry["accepted"] is True
        assert retry["duplicate"] is True
        assert retry["applied"] is True
        assert retry["delivery"] == "applied"

        with pytest.raises(ValueError, match="different steering input"):
            service.turn_steer(
                {
                    "threadId": thread_id,
                    "turnId": turn_id,
                    "input": "Different content must not reuse that id.",
                    "clientInputId": "durable-goal-steer-1",
                }
            )
    finally:
        runtime.close()


def test_approval_steering_keeps_auto_queue_drain_behavior(tmp_path: Path) -> None:
    service, runtime, _store, _platform, calls, workspace = _service(
        tmp_path,
        [
            ModelResponse(
                tool_calls=(
                    ToolCall(
                        call_id="call-queue-steer",
                        name="sensitive_live_steer",
                        arguments={"value": "must-not-run"},
                    ),
                )
            ),
            ModelResponse(text="steered turn complete"),
            ModelResponse(text="queued follow-up complete"),
        ],
        auto_drain_queue=True,
    )
    try:
        thread_id, turn_id = _start_waiting_turn(service, workspace)
        queued = runtime.enqueue_turn(thread_id, "queued follow-up")

        service.turn_steer(
            {
                "threadId": thread_id,
                "turnId": turn_id,
                "input": "Skip the sensitive action and continue safely.",
                "clientInputId": "durable-queue-steer-1",
            }
        )

        # Do not call thread/read while DurableAgentRuntime is between claiming a
        # queue item and durably adopting its turn id. get_session() intentionally
        # reconciles stale dispatches, so polling that baseline transition would
        # perturb the queue rather than observe steering itself.
        _wait_until(lambda: not service._is_active(thread_id))
        snapshot = service.thread_read({"threadId": thread_id})
        assert calls == []
        assert snapshot["finalText"] == "queued follow-up complete"
        assert snapshot["thread"]["status"] == "completed"
        assert snapshot["thread"]["currentTurnId"] != turn_id
        assert runtime.list_queued_turns(thread_id) == ()
        assert any(
            event.data.get("queue_id") == queued.queue_id
            for event in service.store.events(thread_id)
            if event.kind.value == "queue_dispatched"
        )
    finally:
        runtime.close()
