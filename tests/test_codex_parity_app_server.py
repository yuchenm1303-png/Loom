from __future__ import annotations

import time

from app.agent_runtime import (
    AgentStatus,
    AgentTool,
    DurableAgentRuntime,
    FileAgentSessionStore,
    PermissionMode,
    ToolEffect,
    ToolRegistry,
    ToolResult,
)
from app.ai import ModelResponse, ToolCall
from app.app_server import LoomAppServerService, LoomRpcController, PROTOCOL_VERSION


class ScriptedPlatform:
    def __init__(self, responses):
        self.responses = list(responses)

    def execute_chat(self, _profile_id, _request):
        if not self.responses:
            raise AssertionError("scripted platform ran out of responses")
        return self.responses.pop(0)


def _initialize(controller, request_id):
    response = controller.handle(
        {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": "initialize",
            "params": {
                "protocolVersion": PROTOCOL_VERSION,
                "clientInfo": {"name": f"parity-client-{request_id}", "version": "1"},
            },
        }
    )
    assert response["result"]["protocolVersion"] == PROTOCOL_VERSION


def _wait_until(predicate, timeout=3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.01)
    raise AssertionError("timed out waiting for app-server parity state")


def test_reconnected_controller_can_resolve_existing_approval(tmp_path):
    """A client reconnect must not orphan or retarget a durable approval."""
    executed = []

    def sensitive(_context, arguments):
        executed.append(arguments["value"])
        return ToolResult(ok=True, content="approved")

    tool = AgentTool(
        name="sensitive_change",
        description="Sensitive parity action.",
        input_schema={
            "type": "object",
            "properties": {"value": {"type": "string"}},
            "required": ["value"],
            "additionalProperties": False,
        },
        handler=sensitive,
        effect=ToolEffect.SENSITIVE,
        binding_key="sensitive-change-v1",
    )
    state = tmp_path / "state"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = FileAgentSessionStore(state)
    runtime = DurableAgentRuntime(
        platform=ScriptedPlatform(
            [
                ModelResponse(
                    tool_calls=(
                        ToolCall(
                            "approval-call",
                            "sensitive_change",
                            {"value": "once"},
                        ),
                    ),
                    finish_reason="tool_calls",
                ),
                ModelResponse(text="done", finish_reason="stop"),
            ]
        ),
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
    try:
        first = LoomRpcController(service)
        _initialize(first, 1)
        created = first.handle(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "thread/start",
                "params": {
                    "workspace": str(workspace),
                    "permissionMode": "approval",
                },
            }
        )
        thread_id = created["result"]["thread"]["id"]
        first.handle(
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "turn/start",
                "params": {"threadId": thread_id, "input": "change it"},
            }
        )
        pending = _wait_until(
            lambda: service.thread_read({"threadId": thread_id})["pendingApproval"]
        )
        assert pending["callId"] == "approval-call"
        assert pending["threadId"] == thread_id
        assert pending["turnId"]
        assert pending["requestId"]
        assert executed == []

        # Model a transport reconnect: protocol/controller state is new, while the
        # app-server service and durable runtime keep the pending approval.
        second = LoomRpcController(service)
        _initialize(second, 10)
        resolved = second.handle(
            {
                "jsonrpc": "2.0",
                "id": 11,
                "method": "approval/respond",
                "params": {
                    "threadId": pending["threadId"],
                    "turnId": pending["turnId"],
                    "requestId": pending["requestId"],
                    "callId": pending["callId"],
                    "decision": "accept",
                },
            }
        )
        assert resolved["result"]["accepted"] is True

        _wait_until(
            lambda: service.thread_read({"threadId": thread_id})["thread"]["status"]
            == AgentStatus.COMPLETED.value
        )
        snapshot = service.thread_read({"threadId": thread_id})
        assert snapshot["pendingApproval"] is None
        assert snapshot["finalText"] == "done"
        assert executed == ["once"]
    finally:
        runtime.close()