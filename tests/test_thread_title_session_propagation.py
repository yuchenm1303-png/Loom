from __future__ import annotations

from pathlib import Path
from types import ModuleType, SimpleNamespace

from app.ai import AIMessage, MessageRole, ToolChoice
from app.app_server_thread_management import ThreadLibraryStore
from app.thread_title_override import _auto_title_prompt, _build_auto_title_request, _metadata_display_title


class SessionDirStore:
    def __init__(self, root: Path) -> None:
        self.root = root

    def session_dir(self, session_id: str) -> Path:
        return self.root / session_id


def test_title_request_is_plain_chat_bound_to_session() -> None:
    session = SimpleNamespace(session_id="thread-123")
    request, prompt = _build_auto_title_request(ModuleType("fake"), session, user_prompt="修复平台筛选下拉框")
    assert prompt == "修复平台筛选下拉框"
    assert request.session_id == session.session_id
    assert request.tool_choice is ToolChoice.NONE
    assert request.reasoning is None
    assert "修复平台筛选下拉框" in request.messages[-1].content


def test_title_prompt_uses_only_first_request() -> None:
    module = ModuleType("fake")
    module.MessageRole = MessageRole
    module._message_text = lambda message: message.content
    session = SimpleNamespace(messages=(
        AIMessage(role=MessageRole.USER, content="右上角标签重叠 [1 image attached]"),
        AIMessage(role=MessageRole.ASSISTANT, content="已经定位布局"),
    ))
    prompt, source = _auto_title_prompt(module, session)
    assert source == "右上角标签重叠"
    assert "已经定位布局" not in prompt


def test_committed_title_is_displayed_as_saved() -> None:
    assert _metadata_display_title({"title": "你好，我是模型。", "titleSource": "auto"}) == ("你好，我是模型。", "auto")


def test_store_saves_nonempty_raw_title_and_rejects_second_attempt(tmp_path: Path) -> None:
    session_id = "title-state"
    directory = tmp_path / session_id
    directory.mkdir()
    (directory / "session.json").write_text("{}", encoding="utf-8")
    store = ThreadLibraryStore(SessionDirStore(tmp_path))
    assert store.mark_auto_title_pending(session_id, source_prompt="你好")
    assert store.claim_auto_title_attempt(session_id, source_prompt="你好")
    store.finish_auto_title_attempt(session_id, "empty", source_prompt="你好")
    assert store.read(session_id)["autoTitleFallback"] is True
    assert not store.mark_auto_title_pending(session_id, source_prompt="你好")
