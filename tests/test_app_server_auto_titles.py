from __future__ import annotations

import threading
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


def test_empty_response_retries_after_cooldown(tmp_path: Path) -> None:
    service, runtime, platform, workspace = _service(tmp_path, "   ")
    try:
        thread_id = service.thread_start({"workspace": str(workspace)})["thread"]["id"]
        service.turn_start({"threadId": thread_id, "input": "修复标题"})
        metadata = _wait(lambda: service.thread_library.read(thread_id) if service.thread_library.read(thread_id).get("autoTitleFallback") else None)
        assert metadata["autoTitleAttempts"] == 1
        assert metadata["autoTitleNextRetryAt"]
        service.thread_read({"threadId": thread_id})
        time.sleep(0.1)
        assert len([r for r in platform.requests if r.tool_choice is ToolChoice.NONE]) == 1

        # Simulate the clock passing the persisted cooldown. The user does not
        # need to send the same prompt again or recreate the conversation.
        platform.title = "修复标题生成"
        service.thread_library.write(thread_id, {"autoTitleNextRetryAt": "2000-01-01T00:00:00+00:00"})
        service.thread_read({"threadId": thread_id})
        metadata = _wait(lambda: service.thread_library.read(thread_id) if service.thread_library.read(thread_id).get("titleSource") == "auto" else None)
        assert metadata["title"] == "修复标题生成"
        assert metadata["autoTitleAttempts"] == 2
        assert metadata["autoTitleNextRetryAt"] == ""
        assert len([r for r in platform.requests if r.tool_choice is ToolChoice.NONE]) == 2
    finally:
        runtime.close()


def test_old_exhausted_title_is_backfilled_when_opened(tmp_path: Path) -> None:
    service, runtime, platform, workspace = _service(tmp_path, "修复会话标题")
    try:
        thread_id = service.thread_start({"workspace": str(workspace)})["thread"]["id"]
        service.thread_library.write(thread_id, {
            "title": "",
            "titleSource": "fallback",
            "autoTitleFallback": True,
            "autoTitleAttempts": 1,
            "autoTitleSourcePrompt": "修复会话标题",
            "autoTitleVersion": 10,
        })
        service.thread_read({"threadId": thread_id})
        metadata = _wait(lambda: service.thread_library.read(thread_id) if service.thread_library.read(thread_id).get("titleSource") == "auto" else None)
        assert metadata["title"] == "修复会话标题"
        assert metadata["autoTitleAttempts"] == 2
    finally:
        runtime.close()


def test_balance_error_can_recover_on_successful_later_turn(tmp_path: Path) -> None:
    service, runtime, platform, workspace = _service(tmp_path, "恢复后的标题")
    original_execute = platform.execute_chat
    failed = threading.Event()

    def insufficient_balance(profile_id, request):
        if request.tool_choice is ToolChoice.NONE:
            failed.set()
            raise RuntimeError("APIStatusError: Error code: 402 Insufficient Balance")
        return original_execute(profile_id, request)

    platform.execute_chat = insufficient_balance
    try:
        thread_id = service.thread_start({"workspace": str(workspace)})["thread"]["id"]
        service.turn_start({"threadId": thread_id, "input": "查看项目首页代码"})
        assert failed.wait(3)
        metadata = _wait(lambda: service.thread_library.read(thread_id) if service.thread_library.read(thread_id).get("autoTitleFallback") else None)
        assert metadata["autoTitleAttempts"] == 1
        assert metadata["autoTitleNextRetryAt"]
        assert metadata["titleSource"] == "fallback"

        # When a provider/model works again, turn completion can bypass the
        # previous balance error's long cooldown. It still uses first prompt.
        platform.execute_chat = original_execute
        service._schedule_auto_title(thread_id, force=True)
        metadata = _wait(lambda: service.thread_library.read(thread_id) if service.thread_library.read(thread_id).get("titleSource") == "auto" else None)
        assert metadata["title"] == "恢复后的标题"
        assert metadata["autoTitleAttempts"] == 2
        assert metadata["autoTitleSourcePrompt"] == "查看项目首页代码"
    finally:
        runtime.close()


def test_manual_rename_wins_race_with_retry(tmp_path: Path) -> None:
    service, runtime, platform, workspace = _service(tmp_path, "不应覆盖手动标题")
    started = threading.Event()
    release = threading.Event()
    original_execute = platform.execute_chat

    def blocked_title(profile_id, request):
        if request.tool_choice is ToolChoice.NONE:
            started.set()
            assert release.wait(3)
        return original_execute(profile_id, request)

    platform.execute_chat = blocked_title
    try:
        thread_id = service.thread_start({"workspace": str(workspace)})["thread"]["id"]
        service.thread_library.write(thread_id, {
            "title": "",
            "titleSource": "fallback",
            "autoTitleFallback": True,
            "autoTitleAttempts": 1,
            "autoTitleSourcePrompt": "修复网页标题",
        })
        service.thread_read({"threadId": thread_id})
        assert started.wait(3)
        service.thread_rename({"threadId": thread_id, "title": "我手动起的名字"})
        release.set()
        _wait(lambda: thread_id not in service._auto_title_inflight)
        metadata = service.thread_library.read(thread_id)
        assert metadata["title"] == "我手动起的名字"
        assert metadata["titleSource"] == "manual"
    finally:
        release.set()
        runtime.close()


def test_title_waits_for_session_model_preparation(tmp_path: Path) -> None:
    service, runtime, default_platform, workspace = _service(tmp_path, "错误的默认模型标题")
    selected_platform = TitlePlatform("询问模型身份")
    preparing = threading.Event()
    release = threading.Event()
    scheduled = threading.Event()
    original_schedule = service._schedule_auto_title

    def schedule(*args, **kwargs):
        scheduled.set()
        return original_schedule(*args, **kwargs)

    def prepare(session):
        # Account model preparation removes the old platform before resolving
        # the selected gateway model, leaving the global default in the gap.
        runtime.clear_session_model(session.session_id)
        preparing.set()
        assert release.wait(3)
        runtime.set_session_model(session.session_id, selected_platform)

    service._prepare_turn = prepare
    service._schedule_auto_title = schedule
    try:
        thread_id = service.thread_start({"workspace": str(workspace)})["thread"]["id"]
        service.turn_start({"threadId": thread_id, "input": "你是什么模型呢"})
        assert preparing.wait(3)
        assert not scheduled.is_set()
        assert not default_platform.requests
        release.set()
        metadata = _wait(lambda: service.thread_library.read(thread_id)
                         if service.thread_library.read(thread_id).get("titleSource") == "auto" else None)
        assert metadata["title"] == "询问模型身份"
        assert not default_platform.requests
        assert any(r.tool_choice is ToolChoice.NONE for r in selected_platform.requests)
    finally:
        release.set()
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
