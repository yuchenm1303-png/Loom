from __future__ import annotations

import threading
import time
from pathlib import Path

from app.ai import MessageRole, ModelResponse, ModelUsage
from app.ai.execution_control import ModelCancelled, check_cancelled
from app.agent_runtime import AgentEventKind, DurableAgentRuntime, FileAgentSessionStore, PermissionMode, ToolRegistry
from app.app_server import LoomAppServerService, LoomRpcController, PROTOCOL_VERSION


class InterruptiblePlatform:
    def __init__(self) -> None:
        self.requests = []
        self.first_started = threading.Event()
        self.first_cancelled = threading.Event()
        self._lock = threading.Lock()
        self._calls = 0

    def execute_chat(self, _profile_id, request):
        with self._lock:
            self._calls += 1
            call = self._calls
            self.requests.append(request)
        if call == 1:
            self.first_started.set()
            try:
                while True:
                    check_cancelled()
                    time.sleep(0.01)
            except ModelCancelled:
                self.first_cancelled.set()
                raise
        if call == 2:
            return ModelResponse(text="continued with the new direction")
        raise AssertionError(f"unexpected model sample: {call}")


class SteeringAtCommitExecutor:
    """Inject guidance after a provider response exists but before TurnRunner commits it."""

    def __init__(self, runtime, session_id: str, turn_id: str) -> None:
        self.runtime = runtime
        self.session_id = session_id
        self.turn_id = turn_id
        self.requests = []
        self.calls = 0

    def execute(self, _platform, _profile_id, request, _token, *, steering_revision=None):
        self.calls += 1
        self.requests.append(request)
        if self.calls == 1:
            receipt = self.runtime.steer(
                self.session_id,
                "Use the replacement direction before committing that answer.",
                turn_id=self.turn_id,
                input_id="commit-race-steer-1",
            )
            assert receipt["accepted"] is True
            return ModelResponse(
                text="stale answer that must never become durable",
                usage=ModelUsage(input_tokens=3, output_tokens=2, total_tokens=5),
                response_id="stale-response",
            )
        if self.calls == 2:
            return ModelResponse(
                text="replacement answer",
                usage=ModelUsage(input_tokens=2, output_tokens=1, total_tokens=3),
                response_id="replacement-response",
            )
        raise AssertionError(f"unexpected model sample: {self.calls}")


def _service(tmp_path: Path):
    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = FileAgentSessionStore(home)
    platform = InterruptiblePlatform()
    runtime = DurableAgentRuntime(
        platform=platform,
        store=store,
        tools=ToolRegistry(()),
        default_permission_mode=PermissionMode.WORKSPACE,
        auto_drain_queue=False,
    )
    service = LoomAppServerService(
        runtime=runtime,
        store=store,
        model="test-model",
        default_workspace=workspace,
        default_permission_mode=PermissionMode.WORKSPACE,
    )
    return service, runtime, store, platform, workspace


def _wait_until(predicate, *, timeout: float = 3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.01)
    raise AssertionError("timed out waiting for phase-two live steering state")


def test_initialize_advertises_inflight_model_interruption(tmp_path: Path) -> None:
    service, runtime, _store, _platform, _workspace = _service(tmp_path)
    try:
        response = LoomRpcController(service).handle(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": PROTOCOL_VERSION,
                    "clientInfo": {"name": "pytest", "version": "1"},
                },
            }
        )
        assert response is not None
        steering = response["result"]["capabilities"]["turns"]["steering"]
        assert steering["interruptsInFlightModel"] is True
        assert steering["runningToolPolicy"] == "finish_then_replan"
    finally:
        runtime.close()


def test_steering_interrupts_only_the_inflight_model_and_resamples_same_turn(tmp_path: Path) -> None:
    service, runtime, store, platform, workspace = _service(tmp_path)
    try:
        thread_id = service.thread_start({"workspace": str(workspace)})["thread"]["id"]
        turn_id = service.turn_start(
            {"threadId": thread_id, "input": "Start with the original direction."}
        )["turn"]["id"]
        assert platform.first_started.wait(2.0)

        receipt = service.turn_steer(
            {
                "threadId": thread_id,
                "turnId": turn_id,
                "input": "Change direction now and answer using the safer approach.",
                "clientInputId": "phase-two-steer-1",
            }
        )
        assert receipt["accepted"] is True
        assert receipt["duplicate"] is False
        assert receipt["delivery"] == "model_replan_requested"
        assert platform.first_cancelled.wait(2.0)

        snapshot = _wait_until(
            lambda: (
                value
                if (value := service.thread_read({"threadId": thread_id}))["thread"]["status"] == "completed"
                else None
            )
        )
        assert snapshot["thread"]["currentTurnId"] == turn_id
        assert snapshot["finalText"] == "continued with the new direction"
        assert len(platform.requests) == 2

        second_request_users = [
            message.content
            for message in platform.requests[1].messages
            if message.role is MessageRole.USER
        ]
        assert second_request_users.count("Start with the original direction.") == 1
        assert second_request_users.count(
            "Change direction now and answer using the safer approach."
        ) == 1

        session = store.load(thread_id)
        assert session.model_steps == 1
        events = store.events(thread_id)
        assert not any(event.kind is AgentEventKind.TURN_CANCELLED for event in events)
        steering_events = [
            event
            for event in events
            if event.kind is AgentEventKind.USER_MESSAGE
            and event.data.get("source") == "steering"
            and event.data.get("input_id") == "phase-two-steer-1"
        ]
        assert len(steering_events) == 1
        assert any(
            event.kind is AgentEventKind.MODEL_RESPONSE_REJECTED
            and event.data.get("reason") == "superseded_by_steering"
            for event in events
        )
    finally:
        runtime.close()


def test_steering_wins_the_post_provider_precommit_race(tmp_path: Path) -> None:
    service, runtime, store, _platform, workspace = _service(tmp_path)
    try:
        thread_id = service.thread_start({"workspace": str(workspace)})["thread"]["id"]
        turn_id = "phase-two-commit-race-turn"
        executor = SteeringAtCommitExecutor(runtime, thread_id, turn_id)
        runtime.model_executor = executor

        result = runtime.start_turn(
            thread_id,
            "Begin with the original direction.",
            turn_id=turn_id,
        )
        assert result.status.value == "completed"
        assert result.turn_id == turn_id
        assert result.final_text == "replacement answer"
        assert executor.calls == 2

        second_request_users = [
            message.content
            for message in executor.requests[1].messages
            if message.role is MessageRole.USER
        ]
        assert second_request_users.count("Begin with the original direction.") == 1
        assert second_request_users.count(
            "Use the replacement direction before committing that answer."
        ) == 1

        session = store.load(thread_id)
        assert session.model_steps == 1
        assert session.usage.total_tokens == 8
        events = store.events(thread_id)
        durable_responses = [
            event
            for event in events
            if event.kind is AgentEventKind.MODEL_RESPONSE
        ]
        assert len(durable_responses) == 1
        assert durable_responses[0].data.get("response_id") == "replacement-response"
        assert all(
            event.data.get("response_id") != "stale-response"
            for event in durable_responses
        )
        stale_rejections = [
            event
            for event in events
            if event.kind is AgentEventKind.MODEL_RESPONSE_REJECTED
            and event.data.get("reason") == "superseded_by_steering"
            and event.data.get("response_id") == "stale-response"
        ]
        assert len(stale_rejections) == 1
        assert stale_rejections[0].data["usage"]["total_tokens"] == 5
        assert not any(event.kind is AgentEventKind.TURN_CANCELLED for event in events)
    finally:
        runtime.close()
