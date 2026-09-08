from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from app.desktop.message_presentation import MessageWidget, StreamGlyph


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def test_assistant_stream_state_moves_from_thinking_to_writing(app, monkeypatch):
    monkeypatch.setenv("LOOM_REDUCE_MOTION", "1")
    message = MessageWidget("assistant")

    message.set_streaming(True)
    assert not message.stream_status.isHidden()
    assert message.stream_status.mode == StreamGlyph.THINKING
    assert not message.stream_status.label.isHidden()

    message.set_text("Hello")
    assert message.stream_status.mode == StreamGlyph.WRITING
    assert message.stream_status.label.isHidden()

    message.set_streaming(False)
    assert message.stream_status.isHidden()


def test_user_message_never_shows_generation_chrome(app, monkeypatch):
    monkeypatch.setenv("LOOM_REDUCE_MOTION", "1")
    message = MessageWidget("user")

    message.set_streaming(True)

    assert message.stream_status.isHidden()
    assert message.stream_badge.isHidden()


def test_user_message_text_has_no_trailing_rich_text_gap(app, monkeypatch):
    monkeypatch.setenv("LOOM_REDUCE_MOTION", "1")
    message = MessageWidget("user")
    message.set_text("你好，可以帮我清一下电")

    margins = message.layout().contentsMargins()
    assert margins.top() == margins.bottom() == 10

    body = message._widgets[0]
    assert "p { margin:0; line-height:1.48; }" in body.text()
    assert body.alignment() & Qt.AlignmentFlag.AlignVCenter


def test_streaming_prose_updates_existing_rich_block_in_place(app, monkeypatch):
    monkeypatch.setenv("LOOM_REDUCE_MOTION", "1")
    message = MessageWidget("assistant")
    message.set_text("Hel")
    first_block = message._widgets[0]

    message.set_streaming(True)
    message.set_text("Hello, this is still the same paragraph.")

    assert message._widgets[0] is first_block
    assert "same paragraph" in message._widgets[0].text()


def test_streaming_code_updates_existing_code_block_in_place(app, monkeypatch):
    monkeypatch.setenv("LOOM_REDUCE_MOTION", "1")
    message = MessageWidget("assistant")
    message.set_text("```python\npri")
    first_block = message._widgets[0]

    message.set_streaming(True)
    message.set_text("```python\nprint('hello')")

    assert message._widgets[0] is first_block
    assert first_block.body.toPlainText() == "print('hello')"


def test_a_long_outgoing_message_wraps_instead_of_being_clipped(app, monkeypatch):
    monkeypatch.setenv("LOOM_REDUCE_MOTION", "1")
    message = MessageWidget("user")
    message.set_text(
        "Refactor the transcript so streaming feels calm, and check the tests still pass"
    )

    hint = message.sizeHint()
    # QLabel's own hint for wrapped rich text under-reports badly; the bubble has
    # to measure the document or a long sentence is cut off on one line.
    assert hint.width() > 300
    assert hint.width() <= MessageWidget.USER_MAX_WIDTH
    assert message.heightForWidth(hint.width()) >= hint.height()


def test_a_thinking_message_keeps_a_row_of_height_before_the_first_token(app, monkeypatch):
    monkeypatch.setenv("LOOM_REDUCE_MOTION", "1")
    message = MessageWidget("assistant")

    message.set_streaming(True)
    # With no body blocks the layout reports no height-for-width, and the
    # transcript used to collapse the whole message -- hiding the only thing on
    # screen between pressing Enter and the first token.
    assert message.minimumHeight() > 0
    assert message.heightForWidth(700) >= message.stream_status.sizeHint().height()

    message.set_text("First token")
    assert message.minimumHeight() == 0


def test_model_thinking_is_folded_away_instead_of_printed(app, monkeypatch):
    monkeypatch.setenv("LOOM_REDUCE_MOTION", "1")
    message = MessageWidget("assistant")

    message.set_streaming(True)
    message.set_text("<think>The user greeted me in Chinese.")
    # While it is still thinking there is nothing to answer with yet.
    assert message._text == ""
    assert message.reasoning.isHidden() is False
    assert message.reasoning.body.isHidden() is True
    assert message.reasoning.toggle.text().startswith("Thinking")

    message.set_text("<think>The user greeted me in Chinese.</think>你好！")
    message.set_streaming(False)

    # The reply reads as the reply; the raw tag never reaches the transcript.
    assert message._text == "你好！"
    assert "The user greeted me" in message.reasoning.body.text()
    assert message.reasoning.toggle.text().startswith(("Thought process", "Thought for"))

    # And it is one click away rather than gone.
    message.reasoning.toggle.click()
    assert message.reasoning.body.isHidden() is False


def test_a_message_without_thinking_shows_no_reasoning_row(app, monkeypatch):
    monkeypatch.setenv("LOOM_REDUCE_MOTION", "1")
    message = MessageWidget("assistant")

    message.set_text("Plain answer with no reasoning.")

    assert message.reasoning.isHidden() is True
    assert message._text == "Plain answer with no reasoning."


def test_reduced_motion_keeps_stream_glyph_static(app, monkeypatch):
    monkeypatch.setenv("LOOM_REDUCE_MOTION", "1")
    glyph = StreamGlyph()

    glyph.set_active(True)

    assert not glyph._timer.isActive()
