from __future__ import annotations

import time
from pathlib import Path

from app.ai import ModelResponse, ToolChoice
from app.agent_runtime import DurableAgentRuntime, FileAgentSessionStore, PermissionMode, ToolRegistry
from app.app_server_thread_management import (
    ManagedStreamingLoomAppServerService,
    _sanitize_generated_title,
)
from app.thread_title_override import _AUTO_TITLE_VERSION as AUTO_TITLE_VERSION
from app.thread_title_override import _metadata_display_title, _safe_initial_title_from_prompt


def _is_title_request(request) -> bool:
    """Whether this call is the auto-title request rather than the turn."""
    text = " ".join(str(message.content) for message in request.messages).casefold()
    return "concise title" in text or "\"title\"" in text


class RecordingPlatform:
    """Scripted platform that routes by request kind, not by call order.

    Auto-titling fires as soon as the first prompt arrives, concurrently with
    the turn itself, so "first response is the reply, second is the title" is a
    race: whichever thread reached the platform first took the wrong one.

    The convention these tests already used is kept -- the *last* scripted
    response is the title, the rest belong to turns -- but it is now honoured by
    request kind rather than by arrival order.
    """

    def __init__(self, responses) -> None:
        self.responses = list(responses)
        self.requests = []

    def execute_chat(self, _profile_id, request):
        self.requests.append(request)
        if not self.responses:
            raise AssertionError("scripted platform ran out of responses")
        if _is_title_request(request) and len(self.responses) > 1:
            return self.responses.pop()
        return self.responses.pop(0)


class StructuredRejectingPlatform(RecordingPlatform):
    def __init__(self, responses) -> None:
        super().__init__(responses)
        self.structured_requests = []

    def execute_structured_chat(self, _profile_id, request):
        self.structured_requests.append(request)
        raise ValueError("profile lacks structured_output")


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


def test_initial_title_fallback_describes_the_actual_task() -> None:
    prompt = "给管理员账号做一下余额的后门吧，不用充值直接自定义余额这些，我要拿来测试"

    assert _safe_initial_title_from_prompt(prompt) == "添加管理员余额调账"
    assert _safe_initial_title_from_prompt(
        "请对 TermRelay 做一次完整的客户 API 调用链路安全性、调度和计费验收"
    ) == "验收 TermRelay 调度计费"
    assert _safe_initial_title_from_prompt("请设计一个新的库存同步机制，并补充测试") == "设计一个新的库存同步机制"


def test_legacy_generic_fallback_self_heals_from_source_prompt() -> None:
    title, source = _metadata_display_title(
        {
            "title": "整理对话主题",
            "titleSource": "auto",
            "autoTitleFallback": True,
            "autoTitleSourcePrompt": "给管理员账号做一下余额的后门吧，不用充值直接自定义余额这些",
        }
    )

    assert title == "添加管理员余额调账"
    assert source == "auto"


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
        # Read the constant rather than a literal: the auto-title schema is
        # versioned and this assertion is about it being *stamped*, not about
        # which revision happens to be current.
        assert metadata["autoTitleVersion"] == AUTO_TITLE_VERSION
        assert metadata["autoTitleAttempts"] == 1
        assert metadata["autoTitleGeneratedAt"]

        _wait_until(lambda: thread_id not in service.runtime_status()["activeThreadIds"])
        assert len(platform.requests) == 2
        # By predicate, not by position: the title request races the turn, so
        # "the last call" is not reliably the title call.
        title_request = next(r for r in platform.requests if _is_title_request(r))
        assert title_request.tool_choice is ToolChoice.NONE
        assert title_request.tools == ()
        assert title_request.max_output_tokens == 48
        assert title_request.temperature == 0.2
        assert title_request.session_id == thread_id
        title_prompt = str(title_request.messages[-1].content)
        assert "自动总结并生成简短标题" in title_prompt
        # Only the user's first message. Titles are generated as soon as the
        # prompt arrives, so there is no assistant reply to include yet -- and
        # waiting for one is what used to leave threads untitled for a whole
        # turn.
        assert "已经把 Loom 的会话标题逻辑接好了" not in title_prompt
        # The request is a single user message asking for strict JSON; there is
        # no system message any more. Keeping leaked reasoning out of a title is
        # now the sanitizer's job, covered by
        # test_generated_title_sanitizer_rejects_leaked_reasoning.
        assert len(title_request.messages) == 1
        assert '{"title"' in title_prompt

        assert any(
            method == "thread/updated" and params.get("reason") == "auto_title"
            for method, params in notifications
        )
    finally:
        runtime.close()


def test_auto_title_uses_the_threads_model_not_the_global_default(tmp_path: Path) -> None:
    service, runtime, _store, default_platform, workspace = _build_service(
        tmp_path,
        [],
    )
    thread_platform = RecordingPlatform(
        [
            ModelResponse(text="线程模型完成了任务。"),
            ModelResponse(text="线程模型生成标题"),
        ]
    )
    try:
        thread_id = service.thread_start({"workspace": str(workspace)})["thread"]["id"]
        runtime.set_session_model(thread_id, thread_platform)

        service.turn_start({"threadId": thread_id, "input": "验证线程级模型路由"})
        _wait_until(
            lambda: service.thread_read({"threadId": thread_id})["thread"].get("titleSource")
            == "auto"
        )
        _wait_until(lambda: thread_id not in service.runtime_status()["activeThreadIds"])

        assert default_platform.requests == []
        assert len(thread_platform.requests) == 2
        assert any(_is_title_request(request) for request in thread_platform.requests)
        assert all(request.session_id == thread_id for request in thread_platform.requests)
    finally:
        runtime.close()


def test_auto_title_prefers_plain_chat_over_structured_capability(tmp_path: Path) -> None:
    service, runtime, _store, _default_platform, workspace = _build_service(tmp_path, [])
    platform = StructuredRejectingPlatform(
        [
            ModelResponse(text="normal assistant response"),
            ModelResponse(text='{"title":"添加管理员余额调账"}'),
        ]
    )
    try:
        thread_id = service.thread_start({"workspace": str(workspace)})["thread"]["id"]
        runtime.set_session_model(thread_id, platform)

        service.turn_start(
            {
                "threadId": thread_id,
                "input": "给管理员账号增加一个可审计的余额调账入口",
            }
        )
        record = _wait_until(
            lambda: (
                service.thread_read({"threadId": thread_id})["thread"]
                if service.thread_read({"threadId": thread_id})["thread"].get("customTitle")
                else None
            )
        )

        assert record["title"] == "添加管理员余额调账"
        assert platform.structured_requests == []
    finally:
        runtime.close()


def test_stale_title_result_cannot_replace_a_newer_source_prompt(tmp_path: Path) -> None:
    service, runtime, _store, _platform, workspace = _build_service(tmp_path, [])
    try:
        thread_id = service.thread_start({"workspace": str(workspace)})["thread"]["id"]
        assert service.thread_library.mark_auto_title_pending(
            thread_id,
            source_prompt="first task",
        )
        service.thread_library.write(
            thread_id,
            {
                "autoTitleSourcePrompt": "newer task",
                "autoTitlePendingSourcePrompt": "newer task",
            },
        )

        assert service.thread_library.write_auto_title_if_untitled(
            thread_id,
            "Old generated title",
            source_prompt="first task",
        ) is False
        metadata = service.thread_library.read(thread_id)
        assert metadata["autoTitleSourcePrompt"] == "newer task"
        assert metadata["title"] != "Old generated title"
    finally:
        runtime.close()


def test_follow_up_cannot_replace_pending_title_source(tmp_path: Path) -> None:
    service, runtime, _store, _platform, workspace = _build_service(tmp_path, [])
    try:
        thread_id = service.thread_start({"workspace": str(workspace)})["thread"]["id"]
        first_prompt = "修复 token 统计一直显示的问题"
        assert service.thread_library.mark_auto_title_pending(
            thread_id,
            source_prompt=first_prompt,
        )

        # A later turn may arrive while generation is pending or being retried.
        # It must not silently redefine what the task is about.
        assert service.thread_library.mark_auto_title_pending(
            thread_id,
            source_prompt="继续",
        )

        metadata = service.thread_library.read(thread_id)
        assert metadata["autoTitleSourcePrompt"] == first_prompt
        assert metadata["autoTitlePendingSourcePrompt"] == first_prompt
        assert metadata["title"] == _safe_initial_title_from_prompt(first_prompt)
        assert service.thread_library.claim_auto_title_attempt(
            thread_id,
            source_prompt="继续",
        ) is False
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
                "autoTitleAttempts": 1,
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
        # Seeded at 1, and regenerating is itself an attempt. The counter caps
        # retries; it is not reset by success, so 2 is the honest value.
        assert metadata["autoTitleAttempts"] == 2
        assert metadata["autoTitleLastError"] == ""
    finally:
        runtime.close()
