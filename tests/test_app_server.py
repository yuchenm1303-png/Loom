from __future__ import annotations

import io
import json
import time
from pathlib import Path

from app.ai import AGENT_FAST_ROLE, MessageRole, ModelResponse, ModelUsage, ToolCall
from app.agent_runtime import (
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
from app.app_server import (
    JsonRpcStdioServer,
    LoomAppServerService,
    LoomRpcController,
    PROTOCOL_VERSION,
)


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
    raise AssertionError("timed out waiting for app-server state")


def _initialize(controller: LoomRpcController, request_id: int = 1):
    response = controller.handle(
        {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": "initialize",
            "params": {
                "protocolVersion": PROTOCOL_VERSION,
                "clientInfo": {"name": "pytest", "version": "1"},
            },
        }
    )
    assert response is not None
    assert response["result"]["protocolVersion"] == PROTOCOL_VERSION
    return response


def test_controller_requires_versioned_initialize(tmp_path: Path) -> None:
    service, runtime, _store, _platform, _workspace = _build_service(tmp_path, [])
    try:
        controller = LoomRpcController(service)
        before = controller.handle(
            {"jsonrpc": "2.0", "id": 1, "method": "runtime/status", "params": {}}
        )
        assert before["error"]["code"] == -32002

        wrong = controller.handle(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "initialize",
                "params": {"protocolVersion": 999, "clientInfo": {}},
            }
        )
        assert wrong["error"]["code"] == -32010
        assert wrong["error"]["data"]["supported"] == [PROTOCOL_VERSION]

        initialized = _initialize(controller, 3)
        assert initialized["result"]["capabilities"]["providerStreaming"] is False

        duplicate = controller.handle(
            {
                "jsonrpc": "2.0",
                "id": 4,
                "method": "initialize",
                "params": {"protocolVersion": PROTOCOL_VERSION, "clientInfo": {}},
            }
        )
        assert duplicate["error"]["code"] == -32003
    finally:
        runtime.close()


def test_thread_turn_notifications_and_durable_reconstruction(tmp_path: Path) -> None:
    service, runtime, store, _platform, workspace = _build_service(
        tmp_path,
        [ModelResponse(text="Hello from Loom", usage=ModelUsage(5, 3, 8))],
    )
    notifications: list[tuple[str, dict]] = []
    service.subscribe_notifications(lambda method, params: notifications.append((method, params)))
    try:
        controller = LoomRpcController(service)
        _initialize(controller)
        created = controller.handle(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "thread/start",
                "params": {
                    "workspace": str(workspace),
                    "permissionMode": "workspace",
                },
            }
        )
        thread_id = created["result"]["thread"]["id"]

        started = controller.handle(
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "turn/start",
                "params": {"threadId": thread_id, "input": "hello"},
            }
        )
        turn_id = started["result"]["turn"]["id"]
        assert started["result"]["turn"]["status"] == "starting"

        _wait_until(lambda: thread_id not in service.runtime_status()["activeThreadIds"])
        snapshot = service.thread_read({"threadId": thread_id})
        assert snapshot["thread"]["status"] == AgentStatus.COMPLETED.value
        assert snapshot["thread"]["currentTurnId"] == turn_id
        assert store.load(thread_id).current_turn_id == turn_id
        assert len(snapshot["turns"]) == 1
        turn = snapshot["turns"][0]
        assert turn["id"] == turn_id
        assert turn["status"] == "completed"
        assert turn["usage"]["totalTokens"] == 8
        assert [item["type"] for item in turn["items"]] == [
            "user_message",
            "assistant_message",
        ]
        assert turn["items"][-1]["text"] == "Hello from Loom"

        methods = [method for method, _params in notifications]
        assert "thread/started" in methods
        assert "turn/started" in methods
        assert "item/started" in methods
        assert "item/delta" in methods
        assert "item/completed" in methods
        assert "turn/completed" in methods
        assistant_deltas = [
            params
            for method, params in notifications
            if method == "item/delta" and params.get("delta", {}).get("text")
        ]
        assert assistant_deltas[-1]["delta"]["text"] == "Hello from Loom"
    finally:
        runtime.close()


def test_approval_response_runs_through_real_permission_boundary(tmp_path: Path) -> None:
    calls: list[str] = []

    def sensitive_handler(_context: ToolContext, arguments):
        calls.append(str(arguments["value"]))
        return ToolResult(ok=True, content="sensitive tool ran")

    sensitive = AgentTool(
        name="sensitive_test_tool",
        description="A sensitive test action.",
        input_schema={
            "type": "object",
            "properties": {"value": {"type": "string"}},
            "required": ["value"],
            "additionalProperties": False,
        },
        handler=sensitive_handler,
        effect=ToolEffect.SENSITIVE,
    )
    service, runtime, _store, _platform, workspace = _build_service(
        tmp_path,
        [
            ModelResponse(
                tool_calls=(
                    ToolCall(
                        call_id="call-sensitive",
                        name="sensitive_test_tool",
                        arguments={"value": "ok"},
                    ),
                )
            ),
            ModelResponse(text="approved and finished"),
        ],
        tools=(sensitive,),
        permission_mode=PermissionMode.APPROVAL,
    )
    notifications: list[tuple[str, dict]] = []
    service.subscribe_notifications(lambda method, params: notifications.append((method, params)))
    try:
        started_thread = service.thread_start(
            {"workspace": str(workspace), "permissionMode": "approval"}
        )
        thread_id = started_thread["thread"]["id"]
        service.turn_start({"threadId": thread_id, "input": "run the sensitive action"})

        pending = _wait_until(
            lambda: service.thread_read({"threadId": thread_id})["pendingApproval"]
        )
        assert pending["callId"] == "call-sensitive"
        assert calls == []
        assert any(method == "approval/requested" for method, _ in notifications)

        accepted = service.approval_respond(
            {"threadId": thread_id, "callId": "call-sensitive", "approved": True}
        )
        assert accepted["accepted"] is True
        _wait_until(lambda: thread_id not in service.runtime_status()["activeThreadIds"])
        snapshot = service.thread_read({"threadId": thread_id})
        assert calls == ["ok"]
        assert snapshot["thread"]["status"] == "completed"
        assert snapshot["finalText"] == "approved and finished"
        assert snapshot["pendingApproval"] is None
    finally:
        runtime.close()


def test_interrupt_cancels_waiting_approval(tmp_path: Path) -> None:
    def sensitive_handler(_context: ToolContext, _arguments):
        raise AssertionError("tool must not run before approval")

    sensitive = AgentTool(
        name="needs_approval",
        description="Needs approval.",
        input_schema={"type": "object", "properties": {}, "additionalProperties": False},
        handler=sensitive_handler,
        effect=ToolEffect.SENSITIVE,
    )
    service, runtime, _store, _platform, workspace = _build_service(
        tmp_path,
        [
            ModelResponse(
                tool_calls=(
                    ToolCall(call_id="call-stop", name="needs_approval", arguments={}),
                )
            )
        ],
        tools=(sensitive,),
        permission_mode=PermissionMode.APPROVAL,
    )
    try:
        thread_id = service.thread_start(
            {"workspace": str(workspace), "permissionMode": "approval"}
        )["thread"]["id"]
        turn_id = service.turn_start({"threadId": thread_id, "input": "wait for approval"})[
            "turn"
        ]["id"]
        _wait_until(lambda: service.thread_read({"threadId": thread_id})["pendingApproval"])

        result = service.turn_interrupt({"threadId": thread_id, "turnId": turn_id})
        assert result["requested"] is True
        snapshot = service.thread_read({"threadId": thread_id})
        assert snapshot["thread"]["status"] == "cancelled"
        assert snapshot["pendingApproval"] is None
        assert snapshot["turns"][-1]["status"] == "cancelled"
    finally:
        runtime.close()


def test_thread_fork_persists_provenance_and_canonical_history(tmp_path: Path) -> None:
    service, runtime, store, _platform, workspace = _build_service(
        tmp_path,
        [ModelResponse(text="source answer")],
    )
    try:
        source_id = service.thread_start(
            {"workspace": str(workspace), "permissionMode": "workspace"}
        )["thread"]["id"]
        service.turn_start({"threadId": source_id, "input": "source prompt"})
        _wait_until(lambda: source_id not in service.runtime_status()["activeThreadIds"])
        source = store.load(source_id)

        fork_result = service.thread_fork({"threadId": source_id})
        fork_id = fork_result["thread"]["id"]
        fork = store.load(fork_id)
        assert fork.forked_from_id == source_id
        assert fork_result["thread"]["forkedFromId"] == source_id
        assert fork.status is AgentStatus.IDLE
        assert fork.current_turn_id == ""
        assert fork.pending_approval is None
        assert [(m.role, m.content) for m in fork.messages] == [
            (m.role, m.content) for m in source.messages
        ]

        # Prove provenance is not only an in-memory protocol field.
        reloaded_store = FileAgentSessionStore(tmp_path / "home")
        assert reloaded_store.load(fork_id).forked_from_id == source_id
    finally:
        runtime.close()


def test_stdio_transport_emits_json_only_and_processes_requests(tmp_path: Path) -> None:
    service, runtime, _store, _platform, _workspace = _build_service(tmp_path, [])
    try:
        input_lines = "\n".join(
            [
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "initialize",
                        "params": {
                            "protocolVersion": PROTOCOL_VERSION,
                            "clientInfo": {"name": "stdio-test"},
                        },
                    }
                ),
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": 2,
                        "method": "runtime/status",
                        "params": {},
                    }
                ),
            ]
        ) + "\n"
        reader = io.StringIO(input_lines)
        writer = io.StringIO()
        server = JsonRpcStdioServer(service)
        assert server.serve(reader=reader, writer=writer) == 0

        raw_lines = [line for line in writer.getvalue().splitlines() if line.strip()]
        frames = [json.loads(line) for line in raw_lines]
        responses = {frame["id"]: frame for frame in frames if "id" in frame}
        assert responses[1]["result"]["protocolVersion"] == PROTOCOL_VERSION
        assert responses[2]["result"]["model"] == "test-model"
        assert all(line.lstrip().startswith("{") for line in raw_lines)
    finally:
        runtime.close()


def test_fork_field_is_backward_compatible_with_old_session_snapshot(tmp_path: Path) -> None:
    service, runtime, store, _platform, workspace = _build_service(tmp_path, [])
    try:
        thread_id = service.thread_start({"workspace": str(workspace)})["thread"]["id"]
        path = store.session_dir(thread_id) / "session.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload.pop("forked_from_id", None)
        path.write_text(json.dumps(payload), encoding="utf-8")

        session = store.load(thread_id)
        assert session.forked_from_id == ""
        assert session.workspace_dir == str(workspace.resolve())
        assert session.messages == []
    finally:
        runtime.close()
