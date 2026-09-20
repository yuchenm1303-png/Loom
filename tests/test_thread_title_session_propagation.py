from __future__ import annotations

from types import ModuleType, SimpleNamespace

from app.thread_title_override import (
    _build_auto_title_request,
    _build_plain_auto_title_request,
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
