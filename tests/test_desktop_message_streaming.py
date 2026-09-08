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


def test_reduced_motion_keeps_stream_glyph_static(app, monkeypatch):
    monkeypatch.setenv("LOOM_REDUCE_MOTION", "1")
    glyph = StreamGlyph()

    glyph.set_active(True)

    assert not glyph._timer.isActive()
