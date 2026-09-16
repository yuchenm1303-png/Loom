from __future__ import annotations

import threading
import time
from pathlib import Path

from app.ai import MessageRole, ModelResponse, ToolCall
from app.agent_runtime import (
    AgentEventKind,
    AgentTool,
    DurableAgentRuntime,
    FileAgentSessionStore,
    PermissionMode,
    ToolContext,
    ToolEffect,
    ToolRegistry,
    ToolResult,
)
from app.app_server import LoomAppServerService, LoomRpcController, PROTOCOL_VERSION


class RecordingPlatform:
    def __init__(self, responses) -> None:
        self.responses = list(responses)
        self.requests = []

    def execute_chat(self, _profile_id, request):
        self.requests.append(request)
        if not self.responses:
            raise AssertionError("scripted platform ran out of responses")
        return self.responses.pop(0)


def _build_service(
    tmp_path: Path,
    responses,
    *,
    tools=(),
    permission_mode: PermissionMode = PermissionMode.WORKSPACE,
):
    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = FileAgentSessionStore(home)
    platform = RecordingPlatform(responses)
    runtime = DurableAgentRuntime(
        platform=platform,
        store=store,
        tools=ToolRegistry(tuple(tools)),
        default_permission_mode=permission_mode,
        auto_drain_queue=False,
    )
    service = LoomAppServerService(
        runtime=runtime,
        store=store,
        model="test-model",
        default_workspace=workspace,
        default_permission_mode=permission_mode,
    )
    return service, runtime, store, platform, workspace


def _wait_until(predicate, *, timeout: float = 3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.01)
    raise AssertionError("timed out waiting for live steering state")


def test_initialize_advertises_same_turn_steering(tmp_path: Path) -> None:
    service, runtime, _store, _platform, _workspace = _build_service(tmp_path, [])
    try:
        controller = LoomRpcController(service)
        response = controller.handle(
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
        turns = response["result"]["capabilities"]["turns"]
        assert turns["steer"] is True
        assert turns["steering"]["sameTurn"] is True
        assert turns["steering"]["delivery"] == "safeBoundary"
        assert turns["steering"]["approvalSupersedesPending"] is True
    finally:
        runtime.close()


def test_steering_during_tool_execution_applies_after_safe_boundary_once(tmp_path: Path) -> None:
    entered = threading.Event()
    release = threading.Event()
    calls: list[str] = []

    def blocking_handler(_context: ToolContext, arguments):
        calls.append(str(arguments["value"]))
        entered.set()
        if not release.wait(2.0):
            raise AssertionError("test did not release blocking tool")
        return ToolResult(ok=True, content="tool finished normally")

    tool = AgentTool(
        name="blocking_read",
        description="A deterministic blocking read used by the live steering test.",
        input_schema={
            "type": "object",
            "properties": {"value": {"type": "string"}},
            "required": ["value"],
            "additionalProperties": False,
        },
        handler=blocking_handler,
        effect=ToolEffect.READ_ONLY,
    )
    service, runtime, store, platform, workspace = _build_service(
        tmp_path,
        [
            ModelResponse(
                tool_calls=(
                    ToolCall(call_id="call-block", name="blocking_read", arguments={"value": "once"}),
                )
            ),
            ModelResponse(text="followed the new direction"),
        ],
        tools=(tool,),
    )
    try:
        thread_id = service.thread_start({"workspace": str(workspace)})["thread"]["id"]
        turn_id = service.turn_start({"threadId": thread_id, "input": "start the work"})["turn"]["id"]
        assert entered.wait(2.0)

        first = service.turn_steer(
            {
                "threadId": thread_id,
                "turnId": turn_id,
                "input": "Do not continue with the old plan; inspect the safer path instead.",
                "clientInputId": "steer-safe-boundary-1",
            }
        )
        second = service.turn_steer(
            {
                "threadId": thread_id,
                "turnId": turn_id,
                "input": "Do not continue with the old plan; inspect the safer path instead.",
                "clientInputId": "steer-safe-boundary-1",
            }
        )
        assert first["accepted"] is True
        assert first["duplicate"] is False
        assert first["delivery"] == "next_safe_boundary"
        assert second["accepted"] is True
        assert second["duplicate"] is True

        release.set()
        snapshot = _wait_until(
            lambda: (
                value
                if (value := service.thread_read({"threadId": thread_id}))["thread"]["status"] == "completed"
                else None
            )
        )
        assert snapshot["finalText"] == "followed the new direction"
        assert calls == ["once"]
        assert len(platform.requests) == 2

        steering_events = [
            event
            for event in store.events(thread_id)
            if event.kind is AgentEventKind.USER_MESSAGE
            and event.data.get("source") == "steering"
        ]
        assert len(steering_events) == 1
        assert steering_events[0].data["input_id"] == "steer-safe-boundary-1"

        second_request_users = [
            message.content
            for message in platform.requests[1].messages
            if message.role is MessageRole.USER
        ]
        assert second_request_users.count(
            "Do not continue with the old plan; inspect the safer path instead."
        ) == 1
    finally:
        release.set()
        runtime.close()


def test_steering_while_waiting_for_approval_supersedes_unexecuted_calls(tmp_path: Path) -> None:
    calls: list[str] = []

    def sensitive_handler(_context: ToolContext, arguments):
        calls.append(str(arguments["value"]))
        return ToolResult(ok=True, content="sensitive action ran")

    sensitive = AgentTool(
        name="sensitive_write",
        description="A sensitive write that must never run after steering supersedes approval.",
        input_schema={
            "type": "object",
            "properties": {"value": {"type": "string"}},
            "required": ["value"],
            "additionalProperties": False,
        },
        handler=sensitive_handler,
        effect=ToolEffect.SENSITIVE,
    )
    service, runtime, store, platform, workspace = _build_service(
        tmp_path,
        [
            ModelResponse(
                tool_calls=(
                    ToolCall(
                        call_id="call-sensitive-steer",
                        name="sensitive_write",
                        arguments={"value": "must-not-run"},
                    ),
                )
            ),
            ModelResponse(text="replanned without the sensitive action"),
        ],
        tools=(sensitive,),
        permission_mode=PermissionMode.APPROVAL,
    )
    notifications: list[tuple[str, dict]] = []
    service.subscribe_notifications(lambda method, params: notifications.append((method, params)))
    try:
        thread_id = service.thread_start(
            {"workspace": str(workspace), "permissionMode": "approval"}
        )["thread"]["id"]
        turn_id = service.turn_start(
            {"threadId": thread_id, "input": "perform the sensitive action"}
        )["turn"]["id"]
        pending = _wait_until(
            lambda: service.thread_read({"threadId": thread_id})["pendingApproval"]
        )
        assert pending["callId"] == "call-sensitive-steer"
        assert calls == []

        receipt = service.turn_steer(
            {
                "threadId": thread_id,
                "turnId": turn_id,
                "input": "Do not do that. Use a non-destructive approach instead.",
                "clientInputId": "steer-approval-1",
            }
        )
        assert receipt["accepted"] is True
        assert receipt["delivery"] == "approval_superseded"
        assert receipt["applied"] is True

        snapshot = _wait_until(
            lambda: (
                value
                if (value := service.thread_read({"threadId": thread_id}))["thread"]["status"] == "completed"
                else None
            )
        )
        assert snapshot["pendingApproval"] is None
        assert snapshot["finalText"] == "replanned without the sensitive action"
        assert calls == []
        assert len(platform.requests) == 2

        events = store.events(thread_id)
        denied = [
            event
            for event in events
            if event.kind is AgentEventKind.TOOL_DENIED
            and event.data.get("call_id") == "call-sensitive-steer"
        ]
        assert denied
        assert denied[-1].data.get("steering") is True
        assert denied[-1].data.get("reason") == "superseded by new user guidance"
        assert any(
            event.kind is AgentEventKind.USER_MESSAGE
            and event.data.get("source") == "steering"
            and event.data.get("input_id") == "steer-approval-1"
            for event in events
        )
        assert any(
            method == "thread/updated" and params.get("thread", {}).get("status") == "running"
            for method, params in notifications
        )
    finally:
        runtime.close()
