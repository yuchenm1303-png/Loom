from __future__ import annotations

import time
from pathlib import Path

from app.ai import ModelResponse, ToolChoice
from app.agent_runtime import DurableAgentRuntime, FileAgentSessionStore, PermissionMode, ToolRegistry
from app.app_server_thread_management import (
    ManagedStreamingLoomAppServerService,
    _sanitize_generated_title,
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


def _build_service(tmp_path: Path, responses):
    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = FileAgentSessionStore(home)
    platform = RecordingPlatform(responses)
    runtime = DurableAgentRuntime(
        platform=platform,
        store=store,
        tools=ToolRegistry(),
        default_permission_mode=PermissionMode.WORKSPACE,
        auto_drain_queue=False,
    )
    service = ManagedStreamingLoomAppServerService(
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
    raise AssertionError("timed out waiting for auto-title state")


def test_generated_title_sanitizer_removes_model_wrapping() -> None:
    assert _sanitize_generated_title('标题：“Loom 会话标题自动生成。”') == "Loom 会话标题自动生成"
    assert _sanitize_generated_title("```\nConversation title: Repair browser bridge!\n```") == "Repair browser bridge"
    assert _sanitize_generated_title("聊天") == ""


def test_generated_title_sanitizer_rejects_leaked_reasoning() -> None:
    assert _sanitize_generated_title("<think>Let me analyze this conversation to create a concise title") == ""
    assert _sanitize_generated_title("think>Let me analyze this conversation to create a concise title") == ""
    assert _sanitize_generated_title("Let me analyze this conversation to create a concise title") == ""
    assert _sanitize_generated_title("<think>internal notes</think>\n标题：Loom 标题修复") == "Loom 标题修复"


def test_first_completed_turn_generates_and_persists_title(tmp_path: Path) -> None:
    service, runtime, _store, platform, workspace = _build_service(
        tmp_path,
        [
            ModelResponse(text="已经把 Loom 的会话标题逻辑接好了。"),
            ModelResponse(text='标题：“Loom 自动生成会话标题”'),
        ],
    )
    notifications: list[tuple[str, dict]] = []
    service.subscribe_notifications(lambda method, params: notifications.append((method, params)))
    try:
        thread_id = service.thread_start(
            {"workspace": str(workspace), "permissionMode": "workspace"}
        )["thread"]["id"]

        # Before the first turn finishes, Loom still has the zero-cost first-message fallback.
        service.turn_start(
            {
                "threadId": thread_id,
                "input": "帮我给 Loom 聊天栏增加自动总结并生成简短标题的功能",
            }
        )

        record = _wait_until(
            lambda: (
                service.thread_read({"threadId": thread_id})["thread"]
                if service.thread_read({"threadId": thread_id})["thread"].get("titleSource") == "auto"
                else None
            )
        )
        assert record["title"] == "Loom 自动生成会话标题"
        assert record["customTitle"] is True
        assert record["titleSource"] == "auto"

        metadata = service.thread_library.read(thread_id)
        assert metadata["title"] == "Loom 自动生成会话标题"
        assert metadata["titleSource"] == "auto"
        assert metadata["autoTitleVersion"] == 1
        assert metadata["autoTitleAttempts"] == 1
        assert metadata["autoTitleGeneratedAt"]

        assert len(platform.requests) == 2
        title_request = platform.requests[-1]
        assert title_request.tool_choice is ToolChoice.NONE
        assert title_request.tools == ()
        assert title_request.max_output_tokens == 48
        assert title_request.temperature == 0.2
        title_prompt = str(title_request.messages[-1].content)
        assert "自动总结并生成简短标题" in title_prompt
        assert "已经把 Loom 的会话标题逻辑接好了" in title_prompt
        system_prompt = str(title_request.messages[0].content)
        assert "reasoning" in system_prompt
        assert "<think>" in system_prompt

        assert any(
            method == "thread/updated" and params.get("reason") == "auto_title"
            for method, params in notifications
        )
    finally:
        runtime.close()


def test_manual_rename_always_wins_and_skips_auto_title(tmp_path: Path) -> None:
    service, runtime, _store, platform, workspace = _build_service(
        tmp_path,
        [ModelResponse(text="normal assistant response")],
    )
    try:
        thread_id = service.thread_start({"workspace": str(workspace)})["thread"]["id"]
        renamed = service.thread_rename({"threadId": thread_id, "title": "我自己命名的会话"})[
            "thread"
        ]
        assert renamed["titleSource"] == "manual"

        service.turn_start({"threadId": thread_id, "input": "this turn must not overwrite my title"})
        _wait_until(lambda: thread_id not in service.runtime_status()["activeThreadIds"])
        time.sleep(0.05)

        record = service.thread_read({"threadId": thread_id})["thread"]
        assert record["title"] == "我自己命名的会话"
        assert record["titleSource"] == "manual"
        assert len(platform.requests) == 1
        assert service.thread_library.read(thread_id)["autoTitleDisabled"] is True
    finally:
        runtime.close()


def test_existing_bad_auto_title_is_hidden_and_can_regenerate(tmp_path: Path) -> None:
    service, runtime, _store, platform, workspace = _build_service(
        tmp_path,
        [
            ModelResponse(text="normal assistant response"),
            ModelResponse(text="Loom 标题清洗修复"),
        ],
    )
    try:
        thread_id = service.thread_start({"workspace": str(workspace)})["thread"]["id"]
        service.thread_library.write(
            thread_id,
            {
                "title": "think>Let me analyze this conversation to create a concise title",
                "titleSource": "auto",
                "autoTitleAttempts": 2,
            },
        )

        record = service.thread_read({"threadId": thread_id})["thread"]
        assert "think>" not in record["title"]
        assert record["customTitle"] is False
        assert record["titleSource"] == "fallback"

        service.turn_start({"threadId": thread_id, "input": "继续修复自动标题"})
        regenerated = _wait_until(
            lambda: (
                service.thread_read({"threadId": thread_id})["thread"]
                if service.thread_read({"threadId": thread_id})["thread"].get("titleSource") == "auto"
                else None
            )
        )
        assert regenerated["title"] == "Loom 标题清洗修复"
        metadata = service.thread_library.read(thread_id)
        assert metadata["autoTitleAttempts"] == 1
        assert metadata["autoTitleLastError"] == ""
    finally:
        runtime.close()
