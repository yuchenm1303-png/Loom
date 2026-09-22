from __future__ import annotations

from pathlib import Path
from types import ModuleType, SimpleNamespace

from app.ai import AIMessage, MessageRole
from app.app_server_thread_management import ThreadLibraryStore

from app.thread_title_override import (
    _build_auto_title_request,
    _build_plain_auto_title_request,
    _auto_title_prompt,
    _metadata_display_title,
    _metadata_has_committed_title,
    _metadata_title_blocks_auto_title,
    _safe_initial_title_from_prompt,
    _sanitize_generated_title,
)


def test_thread_title_structured_and_plain_requests_preserve_session_id() -> None:
    session = SimpleNamespace(session_id="thread-session-123")

    structured, source_prompt = _build_auto_title_request(
        ModuleType("fake_title_module"),
        session,
        user_prompt="Fix the OpenCode session propagation bug.",
    )

    assert structured is not None
    assert source_prompt
    assert structured.chat.session_id == session.session_id

    plain = _build_plain_auto_title_request(structured)
    assert plain.session_id == session.session_id



def _fake_title_module() -> ModuleType:
    module = ModuleType("fake_title_module")
    module.MessageRole = MessageRole
    module._message_text = lambda message: str(message.content)
    return module


def test_provisional_title_never_clips_the_user_prompt() -> None:
    assert _safe_initial_title_from_prompt("你能看见这个图片吗 [1 image attached]") == "新对话"
    assert _safe_initial_title_from_prompt("Can you inspect this screenshot?") == "New conversation"


def test_title_prompt_uses_recent_context_and_strips_attachment_boilerplate() -> None:
    module = _fake_title_module()
    session = SimpleNamespace(
        session_id="thread-context-1",
        messages=(
            AIMessage(role=MessageRole.USER, content="右上角标签数字和文字重叠 [1 image attached]"),
            AIMessage(role=MessageRole.ASSISTANT, content="已经定位到 badge 的布局和宽度计算。"),
        ),
    )

    prompt, source_prompt = _auto_title_prompt(
        module,
        session,
        user_prompt="右上角标签数字和文字重叠 [1 image attached]",
    )

    assert source_prompt == "右上角标签数字和文字重叠"
    assert "[1 image attached]" not in prompt
    assert 'role="assistant"' in prompt
    assert "badge" in prompt


def test_legacy_fallback_is_retryable_and_never_displayed_as_raw_prompt() -> None:
    metadata = {
        "title": "你能看见这个图片吗 Attached image",
        "titleSource": "auto",
        "autoTitleFallback": True,
        "autoTitlePending": False,
        "autoTitleSourcePrompt": "你能看见这个图片吗 [1 image attached]",
        "autoTitleVersion": 4,
        "autoTitleAttempts": 2,
    }

    assert _metadata_title_blocks_auto_title(metadata) is False
    assert _metadata_display_title(metadata) == ("新对话", "fallback")

    valid = {
        "title": "修复标签文字重叠",
        "titleSource": "auto",
        "autoTitleFallback": False,
        "autoTitleSourcePrompt": "右上角标签数字和文字重叠",
    }
    assert _metadata_title_blocks_auto_title(valid) is True
    assert _metadata_display_title(valid) == ("修复标签文字重叠", "auto")


class _SessionDirStore:
    def __init__(self, root: Path) -> None:
        self.root = root

    def session_dir(self, session_id: str) -> Path:
        return self.root / session_id


def test_title_state_machine_retries_without_persisting_prompt_fallback(tmp_path: Path) -> None:
    session_id = "title-state"
    directory = tmp_path / session_id
    directory.mkdir()
    (directory / "session.json").write_text("{}", encoding="utf-8")
    store = ThreadLibraryStore(_SessionDirStore(tmp_path))

    assert store.mark_auto_title_pending(
        session_id,
        source_prompt="你能看见这个图片吗 [1 image attached]",
    )
    pending = store.read(session_id)
    assert pending["title"] == ""
    assert pending["titleSource"] == "pending"
    assert pending["autoTitlePending"] is True

    assert store.claim_auto_title_attempt(
        session_id,
        source_prompt="你能看见这个图片吗",
    )
    store.finish_auto_title_attempt(
        session_id,
        "temporary_failure",
        source_prompt="你能看见这个图片吗",
    )
    retry = store.read(session_id)
    assert retry["title"] == ""
    assert retry["autoTitlePending"] is True
    assert retry["autoTitleFallback"] is False
    assert retry["autoTitleAttempts"] == 1

    assert store.write_auto_title_if_untitled(
        session_id,
        "检查图片内容",
        source_prompt="你能看见这个图片吗",
    )
    complete = store.read(session_id)
    assert complete["title"] == "检查图片内容"
    assert complete["titleSource"] == "auto"
    assert complete["autoTitlePending"] is False


def test_title_version_upgrade_resets_exhausted_legacy_retry_budget(tmp_path: Path) -> None:
    session_id = "legacy-title-state"
    directory = tmp_path / session_id
    directory.mkdir()
    (directory / "session.json").write_text("{}", encoding="utf-8")
    store = ThreadLibraryStore(_SessionDirStore(tmp_path))
    store.write(
        session_id,
        {
            "title": "右上角的标签数字和文字重叠",
            "titleSource": "auto",
            "autoTitleFallback": True,
            "autoTitleVersion": 4,
            "autoTitleAttempts": 99,
        },
    )

    assert store.mark_auto_title_pending(
        session_id,
        source_prompt="右上角的标签数字和文字重叠，请优化一下",
    )
    upgraded = store.read(session_id)
    assert upgraded["title"] == ""
    assert upgraded["titleSource"] == "pending"
    assert upgraded["autoTitleAttempts"] == 0


def test_generated_title_rejects_prompt_clause_without_task_shape() -> None:
    prompt = "右上角的标签数字和文字重叠，请仔细检查并优化一下"
    assert _sanitize_generated_title(
        "右上角的标签数字和文字重叠",
        source_prompt=prompt,
    ) == ""
    assert _sanitize_generated_title(
        "右上角标签数字文字重叠",
        source_prompt=prompt,
    ) == ""
    assert _sanitize_generated_title(
        "标签数字文字重叠",
        source_prompt=prompt,
    ) == ""
    assert _sanitize_generated_title(
        "修复标签数字文字重叠",
        source_prompt=prompt,
    ) == "修复标签数字文字重叠"


def test_committed_auto_title_is_not_revalidated_by_new_quality_rules() -> None:
    source_prompt = "右上角的标签数字和文字重叠，请仔细检查并优化一下"
    assert _sanitize_generated_title(
        "右上角标签数字文字重叠",
        source_prompt=source_prompt,
    ) == ""

    metadata = {
        "title": "右上角标签数字文字重叠",
        "titleSource": "auto",
        "autoTitleFallback": False,
        "autoTitlePending": False,
        "autoTitleSourcePrompt": source_prompt,
        "autoTitleVersion": 5,
        "autoTitleGeneratedAt": "2026-09-21T08:00:00.000+00:00",
    }
    assert _metadata_has_committed_title(metadata) is True
    assert _metadata_title_blocks_auto_title(metadata) is True
    assert _metadata_display_title(metadata) == ("右上角标签数字文字重叠", "auto")
