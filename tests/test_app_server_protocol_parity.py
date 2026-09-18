from __future__ import annotations

import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.ai import AGENT_FAST_ROLE, ModelResponse, ToolCall
from app.agent_runtime import (
    AgentEvent,
    AgentEventKind,
    AgentStatus,
    AgentTool,
    DurableAgentRuntime,
    FileAgentSessionStore,
    PermissionMode,
    ToolContext,
    ToolEffect,
    ToolRegistry,
    ToolResult,
)
from app.app_server import LoomAppServerService, _turn_records
from app.app_server_protocol import approval_request_from_event


class _Platform:
    def __init__(self, responses) -> None:
        self.responses = list(responses)

    def execute_chat(self, _profile_id, _request):
        if not self.responses:
            raise AssertionError("scripted platform ran out of responses")
        return self.responses.pop(0)


def _service(tmp_path: Path, responses, *, tool: AgentTool):
    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = FileAgentSessionStore(home)
    runtime = DurableAgentRuntime(
        platform=_Platform(responses),
        store=store,
        tools=ToolRegistry((tool,)),
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
    return service, runtime, store, workspace


def _wait(predicate, *, timeout: float = 3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.01)
    raise AssertionError("timed out waiting for app-server state")


def _approval_tool(calls: list[str]) -> AgentTool:
    def handler(_context: ToolContext, arguments):
        calls.append(str(arguments.get("value") or ""))
        return ToolResult(ok=True, content="approved")

    return AgentTool(
        name="approval_tool",
        description="Requires user approval.",
        input_schema={
            "type": "object",
            "properties": {"value": {"type": "string"}},
            "required": ["value"],
            "additionalProperties": False,
        },
        handler=handler,
        effect=ToolEffect.SENSITIVE,
    )


def _start_waiting(service: LoomAppServerService, workspace: Path) -> tuple[str, str, dict]:
    thread_id = service.thread_start(
        {"workspace": str(workspace), "permissionMode": "approval"}
    )["thread"]["id"]
    turn_id = service.turn_start({"threadId": thread_id, "input": "run it"})["turn"]["id"]
    pending = _wait(lambda: service.thread_read({"threadId": thread_id})["pendingApproval"])
    return thread_id, turn_id, pending


def test_refresh_and_resume_keep_the_same_durable_approval_identity(tmp_path: Path) -> None:
    calls: list[str] = []
    service, runtime, store, workspace = _service(
        tmp_path,
        [
            ModelResponse(
                tool_calls=(ToolCall(call_id="call-1", name="approval_tool", arguments={"value": "ok"}),)
            ),
            ModelResponse(text="done"),
        ],
        tool=_approval_tool(calls),
    )
    try:
        thread_id, turn_id, first = _start_waiting(service, workspace)
        second = service.thread_read({"threadId": thread_id})["pendingApproval"]
        assert first == second
        assert first["requestId"]
        assert first["threadId"] == thread_id
        assert first["turnId"] == turn_id
        assert first["itemId"] == "tool:call-1"
        assert first["approvalItemId"] == "approval:call-1"
        assert first["approvalStage"] == "initial"
        assert "kind" not in first
        assert first["retryReason"] is None
        assert first["availableDecisions"] == ["accept", "decline"]

        # A newly constructed app-server adapter over the same durable runtime
        # reconstructs the exact same approval identity on resume/rejoin.
        rejoined = LoomAppServerService(
            runtime=runtime,
            store=store,
            model="test-model",
            default_workspace=workspace,
            default_permission_mode=PermissionMode.APPROVAL,
        ).thread_resume({"threadId": thread_id})
        assert rejoined["pendingApproval"] == first
        assert calls == []
    finally:
        runtime.close()


def test_stale_turn_and_request_ids_fail_closed_before_runtime_resume(tmp_path: Path) -> None:
    calls: list[str] = []
    service, runtime, _store, workspace = _service(
        tmp_path,
        [ModelResponse(tool_calls=(ToolCall(call_id="call-2", name="approval_tool", arguments={"value": "ok"}),))],
        tool=_approval_tool(calls),
    )
    resume_calls: list[tuple] = []
    original_resume = runtime.resume_approval

    def recording_resume(*args, **kwargs):
        resume_calls.append((args, kwargs))
        return original_resume(*args, **kwargs)

    runtime.resume_approval = recording_resume
    try:
        thread_id, turn_id, pending = _start_waiting(service, workspace)
        with pytest.raises(ValueError, match="turnId"):
            service.approval_respond(
                {
                    "threadId": thread_id,
                    "turnId": "stale-turn",
                    "requestId": pending["requestId"],
                    "callId": pending["callId"],
                    "decision": "accept",
                }
            )
        with pytest.raises(ValueError, match="requestId"):
            service.approval_respond(
                {
                    "threadId": thread_id,
                    "turnId": turn_id,
                    "requestId": "stale-request",
                    "callId": pending["callId"],
                    "decision": "accept",
                }
            )
        assert resume_calls == []
        assert calls == []
    finally:
        runtime.close()


def test_duplicate_approval_response_is_explicitly_rejected(tmp_path: Path) -> None:
    calls: list[str] = []
    service, runtime, _store, workspace = _service(
        tmp_path,
        [
            ModelResponse(tool_calls=(ToolCall(call_id="call-3", name="approval_tool", arguments={"value": "ok"}),)),
            ModelResponse(text="done"),
        ],
        tool=_approval_tool(calls),
    )
    entered = threading.Event()
    release = threading.Event()
    original_resume = runtime.resume_approval
    resume_count = 0

    def blocked_resume(*args, **kwargs):
        nonlocal resume_count
        resume_count += 1
        entered.set()
        assert release.wait(2.0)
        return original_resume(*args, **kwargs)

    runtime.resume_approval = blocked_resume
    try:
        thread_id, turn_id, pending = _start_waiting(service, workspace)
        response = {
            "threadId": thread_id,
            "turnId": turn_id,
            "requestId": pending["requestId"],
            "callId": pending["callId"],
            "decision": "accept",
        }
        assert service.approval_respond(response)["accepted"] is True
        assert entered.wait(1.0)
        with pytest.raises(RuntimeError, match="active app-server operation"):
            service.approval_respond(response)
        assert resume_count == 1
    finally:
        release.set()
        _wait(lambda: thread_id not in service.runtime_status()["activeThreadIds"])
        runtime.close()


def test_cancelled_turn_rejects_its_old_approval_response(tmp_path: Path) -> None:
    calls: list[str] = []
    service, runtime, _store, workspace = _service(
        tmp_path,
        [ModelResponse(tool_calls=(ToolCall(call_id="call-4", name="approval_tool", arguments={"value": "never"}),))],
        tool=_approval_tool(calls),
    )
    try:
        thread_id, turn_id, pending = _start_waiting(service, workspace)
        assert service.turn_interrupt({"threadId": thread_id, "turnId": turn_id})["requested"] is True
        with pytest.raises(RuntimeError, match="not waiting"):
            service.approval_respond(
                {
                    "threadId": thread_id,
                    "turnId": turn_id,
                    "requestId": pending["requestId"],
                    "callId": pending["callId"],
                    "decision": "accept",
                }
            )
        assert calls == []
    finally:
        runtime.close()


def test_interrupt_requires_the_active_turn_identity(tmp_path: Path) -> None:
    calls: list[str] = []
    service, runtime, _store, workspace = _service(
        tmp_path,
        [ModelResponse(tool_calls=(ToolCall(call_id="call-5", name="approval_tool", arguments={"value": "never"}),))],
        tool=_approval_tool(calls),
    )
    try:
        thread_id, turn_id, _pending = _start_waiting(service, workspace)
        with pytest.raises(ValueError, match="turnId must not be empty"):
            service.turn_interrupt({"threadId": thread_id})
        with pytest.raises(ValueError, match="current turn"):
            service.turn_interrupt({"threadId": thread_id, "turnId": "old-turn"})
        assert service.turn_interrupt({"threadId": thread_id, "turnId": turn_id})["requested"] is True
    finally:
        runtime.close()


def test_turn_records_use_terminal_step_identity_not_last_assistant_position() -> None:
    session = SimpleNamespace(current_turn_id="turn-1", status=AgentStatus.COMPLETED)
    common = {
        "session_id": "thread-1",
        "turn_id": "turn-1",
        "created_at": "2026-09-18T12:00:00+00:00",
    }
    events = (
        AgentEvent(
            event_id="evt-final",
            kind=AgentEventKind.MODEL_RESPONSE,
            data={
                "step_id": "step-final",
                "text": "Complete task summary",
                "finish_reason": "stop",
                "usage": {},
            },
            **common,
        ),
        AgentEvent(
            event_id="evt-late-commentary",
            kind=AgentEventKind.MODEL_RESPONSE,
            data={
                "step_id": "step-late",
                "text": "A later process fragment",
                "finish_reason": "stop",
                "usage": {},
            },
            **common,
        ),
        AgentEvent(
            event_id="evt-completed",
            kind=AgentEventKind.TURN_COMPLETED,
            data={
                "text": "Complete task summary",
                "final_step_id": "step-final",
            },
            **common,
        ),
    )

    turn = _turn_records(session, events)[0]
    assistants = [item for item in turn["items"] if item["type"] == "assistant_message"]

    assert [item["phase"] for item in assistants] == ["final_answer", "commentary"]
    assert turn["finalStepId"] == "step-final"
    assert turn["finalItemId"] == assistants[0]["id"]
    assert turn["finalItemId"] != assistants[-1]["id"]


def test_same_call_retry_reuses_tool_and_approval_item_ids() -> None:
    session = SimpleNamespace(current_turn_id="turn-1", status=AgentStatus.WAITING_APPROVAL)
    common = {
        "session_id": "thread-1",
        "turn_id": "turn-1",
        "created_at": "2026-09-13T12:00:00+00:00",
    }
    events = (
        AgentEvent(
            event_id="evt-requested",
            kind=AgentEventKind.TOOL_REQUESTED,
            data={"call_id": "call-retry", "tool": "approval_tool", "arguments": {"value": "x"}},
            **common,
        ),
        AgentEvent(
            event_id="evt-approval-1",
            kind=AgentEventKind.TOOL_APPROVAL_REQUIRED,
            data={"call_id": "call-retry", "tool": "approval_tool", "arguments": {"value": "x"}},
            **common,
        ),
        AgentEvent(
            event_id="evt-approval-2",
            kind=AgentEventKind.TOOL_APPROVAL_REQUIRED,
            data={
                "call_id": "call-retry",
                "tool": "approval_tool",
                "arguments": {"value": "x"},
                "approval_stage": "retry",
                "retry_reason": "sandbox denied",
            },
            **common,
        ),
    )
    items = _turn_records(session, events)[0]["items"]
    assert [item["id"] for item in items].count("tool:call-retry") == 1
    assert [item["id"] for item in items].count("approval:call-retry") == 1
    retry = approval_request_from_event(events[-1])
    assert retry["approvalStage"] == "retry"
    assert retry["retryReason"] == "sandbox denied"
    assert "kind" not in retry
