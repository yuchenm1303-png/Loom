"""``turn/start`` with attachments, end to end through the protocol."""

from __future__ import annotations

import base64
import time
from pathlib import Path

from app.ai import ImagePart, MessageRole, ModelResponse, ModelUsage
from app.agent_runtime import (
    AgentStatus,
    DurableAgentRuntime,
    FileAgentSessionStore,
    PermissionMode,
    ToolRegistry,
)
from app.app_server import LoomAppServerService, LoomRpcController, PROTOCOL_VERSION


PNG_1PX = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk"
    "+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
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


def _reply(text: str) -> ModelResponse:
    return ModelResponse(text=text, tool_calls=(), usage=ModelUsage(1, 1, 2))


def _build(tmp_path: Path, *, vision: bool = True):
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True)
    store = FileAgentSessionStore(tmp_path / "home")
    platform = RecordingPlatform([_reply("seen")])
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
        vision=vision,
    )
    return service, runtime, platform, workspace


def _wait_for_request(platform, *, timeout: float = 5.0):
    """Wait for the turn thread to actually reach the model."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if platform.requests:
            return platform.requests[0]
        time.sleep(0.01)
    raise AssertionError("the turn never reached the model")


def _wait_idle(runtime, session_id: str, *, timeout: float = 5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        session = runtime.get_session(session_id)
        if session.status in {AgentStatus.COMPLETED, AgentStatus.FAILED, AgentStatus.IDLE}:
            return session
        time.sleep(0.01)
    raise AssertionError("turn did not finish")


def _thread(service, workspace) -> str:
    return service.thread_start({"workspace": str(workspace)})["thread"]["id"]


def test_an_attached_image_reaches_the_model_as_an_image_part(tmp_path: Path) -> None:
    service, runtime, platform, workspace = _build(tmp_path)
    source = tmp_path / "shot.png"
    source.write_bytes(PNG_1PX)
    try:
        thread_id = _thread(service, workspace)
        result = service.turn_start(
            {
                "threadId": thread_id,
                "input": "what is in this screenshot",
                "attachments": [{"path": str(source), "name": "shot.png"}],
            }
        )
        request = _wait_for_request(platform)

        assert result["turn"]["attachments"][0]["kind"] == "image"
        user = [m for m in request.messages if m.role is MessageRole.USER][-1]
        assert user.uses_vision
        images = [p for p in user.content if isinstance(p, ImagePart)]
        assert images[0].image_url.startswith("data:image/png;base64,")
        # The staged copy is what makes the attachment outlive its source.
        assert (workspace / result["turn"]["attachments"][0]["path"]).is_file()
    finally:
        runtime.close()


def test_an_attached_file_reaches_the_model_as_a_path(tmp_path: Path) -> None:
    service, runtime, platform, workspace = _build(tmp_path)
    source = tmp_path / "rows.csv"
    source.write_text("a,b\n1,2\n", encoding="utf-8")
    try:
        thread_id = _thread(service, workspace)
        result = service.turn_start(
            {
                "threadId": thread_id,
                "input": "summarise this",
                "attachments": [{"path": str(source)}],
            }
        )
        request = _wait_for_request(platform)

        relative = result["turn"]["attachments"][0]["path"]
        user = [m for m in request.messages if m.role is MessageRole.USER][-1]
        assert isinstance(user.content, str)
        assert relative in user.content
        # The agent is told where the file is, not handed its contents.
        assert "a,b" not in user.content
    finally:
        runtime.close()


def test_an_attachment_alone_starts_a_turn_without_any_text(tmp_path: Path) -> None:
    service, runtime, platform, workspace = _build(tmp_path)
    source = tmp_path / "shot.png"
    source.write_bytes(PNG_1PX)
    try:
        thread_id = _thread(service, workspace)
        service.turn_start(
            {"threadId": thread_id, "attachments": [{"path": str(source)}]}
        )
        request = _wait_for_request(platform)

        user = [m for m in request.messages if m.role is MessageRole.USER][-1]
        assert any(isinstance(part, ImagePart) for part in user.content)
    finally:
        runtime.close()


def test_a_turn_with_neither_text_nor_attachments_is_refused(tmp_path: Path) -> None:
    service, runtime, _platform, workspace = _build(tmp_path)
    try:
        thread_id = _thread(service, workspace)
        controller = LoomRpcController(service)
        controller.handle(
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
        response = controller.handle(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "turn/start",
                "params": {"threadId": thread_id, "input": "  "},
            }
        )
        assert response["error"]["code"] == -32602
    finally:
        runtime.close()


def test_a_text_only_model_refuses_the_image_before_the_turn_starts(tmp_path: Path) -> None:
    service, runtime, platform, workspace = _build(tmp_path, vision=False)
    source = tmp_path / "shot.png"
    source.write_bytes(PNG_1PX)
    try:
        thread_id = _thread(service, workspace)
        controller = LoomRpcController(service)
        controller.handle(
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
        response = controller.handle(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "turn/start",
                "params": {
                    "threadId": thread_id,
                    "input": "look",
                    "attachments": [{"path": str(source)}],
                },
            }
        )

        assert "cannot read images" in response["error"]["message"]
        # Nothing ran: the failure is a rejected request, not a failed turn.
        assert platform.requests == []
        assert runtime.get_session(thread_id).status is not AgentStatus.RUNNING
    finally:
        runtime.close()


def test_the_handshake_reports_whether_images_are_accepted(tmp_path: Path) -> None:
    for vision in (True, False):
        service, runtime, _platform, _workspace = _build(tmp_path / f"v{vision}", vision=vision)
        try:
            attachments = service.runtime_status()["attachments"]
            assert attachments["images"] is vision
            # Files never depend on the model being able to see.
            assert attachments["files"] is True
        finally:
            runtime.close()


def test_an_image_is_not_echoed_back_to_clients_as_base64(tmp_path: Path) -> None:
    service, runtime, platform, workspace = _build(tmp_path)
    source = tmp_path / "shot.png"
    source.write_bytes(PNG_1PX)
    try:
        thread_id = _thread(service, workspace)
        service.turn_start(
            {"threadId": thread_id, "input": "look", "attachments": [{"path": str(source)}]}
        )
        _wait_for_request(platform)
        _wait_idle(runtime, thread_id)

        snapshot = service.thread_read({"threadId": thread_id})
        rendered = "".join(
            str(message.get("content") or "") for message in snapshot["messages"]
        )
        assert "base64" not in rendered
        assert "[1 image attached]" in rendered
    finally:
        runtime.close()
