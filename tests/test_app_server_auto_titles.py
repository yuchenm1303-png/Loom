from __future__ import annotations

import time
from pathlib import Path

from app.agent_runtime import (
    DurableAgentRuntime,
    FileAgentSessionStore,
    PermissionMode,
    ToolRegistry,
)
from app.ai import AIMessage, MessageRole, ModelResponse, ToolChoice
from app.app_server_thread_management import (
    ManagedStreamingLoomAppServerService,
    _sanitize_generated_title,
)
from app.thread_title_override import _AUTO_TITLE_VERSION as AUTO_TITLE_VERSION
from app.thread_title_override import (
    _clean_title_context,
    _metadata_display_title,
    _parse_auto_title_payload,
    _safe_initial_title_from_prompt,
)


def _is_title_request(request) -> bool:
    """Whether this call is the auto-title request rather than the turn."""
    text = " ".join(str(message.content) for message in request.messages).casefold()
    return "concise title" in text or "\"title\"" in text


class RecordingPlatform:
    """Scripted platform that separates normal turns from detached title calls.

    Mature auto-titling runs only after the active task reaches a terminal state,
    so cosmetic metadata never competes with the user's model request. Routing
    by request kind keeps the fixture resilient if scheduling details change.
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


def test_generated_title_rejects_truncated_structured_output() -> None:
    prompt = "能帮我清除一下这个应用吗，这个系统删不掉"
    for fragment in ("{", '{"title":', '{"title":"删除残留应用"', "[", "[]"):
        assert _parse_auto_title_payload(fragment, source_prompt=prompt) == ""

    assert _parse_auto_title_payload(
        '{"title":"删除残留应用"}',
        source_prompt=prompt,
    ) == "删除残留应用"


def test_title_context_removes_real_attachment_manifest() -> None:
    content = (
        "能帮我清除一下这个应用吗，这个系统删不掉\n\n"
        "Attached files (already saved in this workspace):\n"
        "- image.png — .loom-attachments/turn/image.png (image, shown above)"
    )
    assert _clean_title_context(content) == "能帮我清除一下这个应用吗，这个系统删不掉"


def test_provisional_title_never_clips_or_heuristically_rewrites_the_prompt() -> None:
    assert _safe_initial_title_from_prompt(
        "给管理员账号做一下余额的后门吧，不用充值直接自定义余额这些，我要拿来测试"
    ) == "新对话"
    assert _safe_initial_title_from_prompt(
        "请对 TermRelay 做一次完整的客户 API 调用链路安全性、调度和计费验收"
    ) == "新对话"
    assert _safe_initial_title_from_prompt("Please inspect the browser bridge") == "New conversation"


def test_legacy_generic_fallback_self_heals_from_source_prompt() -> None:
    title, source = _metadata_display_title(
        {
            "title": "整理对话主题",
            "titleSource": "auto",
            "autoTitleFallback": True,
            "autoTitleSourcePrompt": "给管理员账号做一下余额的后门吧，不用充值直接自定义余额这些",
        }
    )

    assert title == "新对话"
    assert source == "fallback"


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

        # While the task runs, Loom exposes only a neutral provisional label.
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
        assert len(title_request.messages) == 3
        assert title_request.messages[0].role is MessageRole.SYSTEM
        title_context = str(title_request.messages[1].content)
        assert "自动总结并生成简短标题" in title_context
        # The detached call runs after the first turn, so the assistant outcome
        # can disambiguate terse prompts without changing the canonical source.
        assert "已经把 Loom 的会话标题逻辑接好了" in title_context
        assert '{"title"' in str(title_request.messages[-1].content)
        assert not _is_title_request(platform.requests[0])
        assert _is_title_request(platform.requests[1])

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
        assert metadata["title"] == ""
        assert _metadata_display_title(metadata) == ("新对话", "pending")
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
                # Explicit fallback state remains retryable. A successfully
                # committed auto title is immutable after this fix.
                "autoTitleFallback": True,
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
        # Version-5 migration resets legacy fallback retry debt before the new
        # semantic generator gets one honest attempt.
        assert metadata["autoTitleAttempts"] == 1
        assert metadata["autoTitleLastError"] == ""
    finally:
        runtime.close()


def test_structural_debris_title_is_hidden_and_all_record_paths_agree(tmp_path: Path) -> None:
    service, runtime, store, _platform, workspace = _build_service(tmp_path, [])
    try:
        thread_id = service.thread_start({"workspace": str(workspace)})["thread"]["id"]
        session = store.load(thread_id)
        session.messages.append(
            AIMessage(
                role=MessageRole.USER,
                content=(
                    "能帮我清除一下这个应用吗，这个系统删不掉\n\n"
                    "Attached files (already saved in this workspace):\n"
                    "- image.png — .loom-attachments/turn/image.png (image, shown above)"
                ),
            )
        )
        store.save(session)
        service.thread_library.write(
            thread_id,
            {
                "title": "{",
                "titleSource": "auto",
                "autoTitleSourcePrompt": "能帮我清除一下这个应用吗，这个系统删不掉",
                "autoTitleVersion": AUTO_TITLE_VERSION - 1,
                "autoTitleAttempts": 3,
            },
        )

        list_record = service._record(session, active=False)
        managed_record = service._managed_record(session)
        read_record = service.thread_read({"threadId": thread_id})["thread"]
        assert list_record["title"] == "新对话"
        assert managed_record["title"] == "新对话"
        assert read_record["title"] == "新对话"
        assert list_record["titleSource"] == "fallback"
        assert list_record["customTitle"] is False
        assert "Attached files" not in list_record["title"]
    finally:
        runtime.close()


def test_truncated_json_title_retries_instead_of_committing_brace(tmp_path: Path) -> None:
    service, runtime, _store, platform, workspace = _build_service(
        tmp_path,
        [
            ModelResponse(text="normal assistant response"),
            ModelResponse(text='{"title":"删除残留应用"}'),
            ModelResponse(text="{"),
        ],
    )
    try:
        thread_id = service.thread_start({"workspace": str(workspace)})["thread"]["id"]
        service.turn_start(
            {
                "threadId": thread_id,
                "input": "能帮我清除一下这个应用吗，这个系统删不掉",
            }
        )

        metadata = _wait_until(
            lambda: (
                service.thread_library.read(thread_id)
                if service.thread_library.read(thread_id).get("titleSource") == "auto"
                else None
            ),
            timeout=4.0,
        )
        assert metadata["title"] == "删除残留应用"
        assert metadata["autoTitleAttempts"] == 2
        assert len([request for request in platform.requests if _is_title_request(request)]) == 2
    finally:
        runtime.close()


def test_failed_title_attempt_retries_automatically(tmp_path: Path) -> None:
    service, runtime, _store, platform, workspace = _build_service(
        tmp_path,
        [
            ModelResponse(text="normal assistant response"),
            # RecordingPlatform takes title samples from the end so the first
            # detached attempt receives the malformed/near-verbatim candidate.
            ModelResponse(text='{"title":"修复标签数字文字重叠"}'),
            ModelResponse(text='{"title":"右上角的标签数字和文字重叠"}'),
        ],
    )
    try:
        thread_id = service.thread_start({"workspace": str(workspace)})["thread"]["id"]
        service.turn_start(
            {
                "threadId": thread_id,
                "input": "右上角的标签数字和文字重叠，请仔细检查并优化一下",
            }
        )

        metadata = _wait_until(
            lambda: (
                service.thread_library.read(thread_id)
                if service.thread_library.read(thread_id).get("titleSource") == "auto"
                else None
            ),
            timeout=4.0,
        )
        assert metadata["title"] == "修复标签数字文字重叠"
        assert metadata["autoTitleAttempts"] == 2
        title_requests = [request for request in platform.requests if _is_title_request(request)]
        assert len(title_requests) == 2
    finally:
        runtime.close()


def test_follow_up_never_reopens_a_committed_auto_title(tmp_path: Path) -> None:
    service, runtime, _store, platform, workspace = _build_service(
        tmp_path,
        [
            ModelResponse(text="第一轮任务完成。"),
            ModelResponse(text="第二轮继续处理完成。"),
            ModelResponse(text='{"title":"修复标签布局"}'),
        ],
    )
    try:
        thread_id = service.thread_start({"workspace": str(workspace)})["thread"]["id"]

        service.turn_start(
            {
                "threadId": thread_id,
                "input": "右上角标签数字和文字重叠，请检查并优化布局",
            }
        )
        first_title = _wait_until(
            lambda: (
                service.thread_library.read(thread_id)
                if service.thread_library.read(thread_id).get("titleSource") == "auto"
                else None
            )
        )
        generated_at = first_title["autoTitleGeneratedAt"]
        attempts = first_title["autoTitleAttempts"]
        assert first_title["title"] == "修复标签布局"

        service.turn_start(
            {
                "threadId": thread_id,
                "input": "早上再继续调整一下昨天这个任务",
            }
        )
        _wait_until(lambda: thread_id not in service.runtime_status()["activeThreadIds"])
        time.sleep(0.1)

        record = service.thread_read({"threadId": thread_id})["thread"]
        metadata = service.thread_library.read(thread_id)
        assert record["title"] == "修复标签布局"
        assert record["titleSource"] == "auto"
        assert record["autoTitlePending"] is False
        assert metadata["title"] == "修复标签布局"
        assert metadata["autoTitleGeneratedAt"] == generated_at
        assert metadata["autoTitleAttempts"] == attempts
        assert len([request for request in platform.requests if _is_title_request(request)]) == 1
    finally:
        runtime.close()
