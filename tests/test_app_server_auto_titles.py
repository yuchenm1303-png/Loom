from __future__ import annotations

import time
from pathlib import Path

from app.agent_runtime import DurableAgentRuntime, FileAgentSessionStore, PermissionMode, ToolRegistry
from app.ai import ModelResponse, ToolChoice
from app.app_server_thread_management import ManagedStreamingLoomAppServerService


class TitlePlatform:
    def __init__(self, title: str) -> None:
        self.title = title
        self.requests = []

    def execute_chat(self, _profile_id, request):
        self.requests.append(request)
        if request.tool_choice is ToolChoice.NONE:
            return ModelResponse(text=self.title)
        return ModelResponse(text="任务完成")


def _service(tmp_path: Path, title: str):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = FileAgentSessionStore(tmp_path / "home")
    platform = TitlePlatform(title)
    runtime = DurableAgentRuntime(
        platform=platform, store=store, tools=ToolRegistry(),
        default_permission_mode=PermissionMode.WORKSPACE, auto_drain_queue=False,
    )
    service = ManagedStreamingLoomAppServerService(
        runtime=runtime, store=store, model="test-model", default_workspace=workspace,
        default_permission_mode=PermissionMode.WORKSPACE,
    )
    return service, runtime, platform, workspace


def _wait(predicate):
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.01)
    raise AssertionError("timed out waiting for title")


def test_first_message_generates_plain_title_without_validation(tmp_path: Path) -> None:
    service, runtime, platform, workspace = _service(tmp_path, "  你好，我是模型。  ")
    try:
        thread_id = service.thread_start({"workspace": str(workspace)})["thread"]["id"]
        service.turn_start({"threadId": thread_id, "input": "你好，你是什么模型"})
        record = _wait(lambda: service.thread_read({"threadId": thread_id})["thread"] if service.thread_library.read(thread_id).get("titleSource") == "auto" else None)
        assert record["title"] == "你好，我是模型。"
        metadata = service.thread_library.read(thread_id)
        assert metadata["autoTitleAttempts"] == 1
        request = next(r for r in platform.requests if r.tool_choice is ToolChoice.NONE)
        assert request.session_id == thread_id
        assert "你好，你是什么模型" in request.messages[-1].content
        assert request.tools == ()
    finally:
        runtime.close()


def test_empty_response_fails_once_without_retry(tmp_path: Path) -> None:
    service, runtime, platform, workspace = _service(tmp_path, "   ")
    try:
        thread_id = service.thread_start({"workspace": str(workspace)})["thread"]["id"]
        service.turn_start({"threadId": thread_id, "input": "修复标题"})
        metadata = _wait(lambda: service.thread_library.read(thread_id) if service.thread_library.read(thread_id).get("autoTitleFallback") else None)
        assert metadata["autoTitleAttempts"] == 1
        service.thread_read({"threadId": thread_id})
        time.sleep(0.1)
        assert len([r for r in platform.requests if r.tool_choice is ToolChoice.NONE]) == 1
    finally:
        runtime.close()


def test_manual_title_is_never_overwritten(tmp_path: Path) -> None:
    service, runtime, platform, workspace = _service(tmp_path, "自动标题")
    try:
        thread_id = service.thread_start({"workspace": str(workspace)})["thread"]["id"]
        service.thread_rename({"threadId": thread_id, "title": "手动标题"})
        service.turn_start({"threadId": thread_id, "input": "新任务"})
        _wait(lambda: thread_id not in service.runtime_status()["activeThreadIds"])
        assert service.thread_read({"threadId": thread_id})["thread"]["title"] == "手动标题"
        assert not any(r.tool_choice is ToolChoice.NONE for r in platform.requests)
    finally:
        runtime.close()


def test_existing_auto_title_is_not_revalidated_or_regenerated(tmp_path: Path) -> None:
    service, runtime, platform, workspace = _service(tmp_path, "其他标题")
    try:
        thread_id = service.thread_start({"workspace": str(workspace)})["thread"]["id"]
        service.thread_library.write(thread_id, {"title": "你好，我是模型。", "titleSource": "auto"})
        assert service.thread_read({"threadId": thread_id})["thread"]["title"] == "你好，我是模型。"
        service.turn_start({"threadId": thread_id, "input": "后续任务"})
        _wait(lambda: thread_id not in service.runtime_status()["activeThreadIds"])
        assert not any(r.tool_choice is ToolChoice.NONE for r in platform.requests)
    finally:
        runtime.close()
