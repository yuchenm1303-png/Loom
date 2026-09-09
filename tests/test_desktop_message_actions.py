from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication

import app.desktop  # noqa: F401 - installs presentation hooks
from app.desktop.output_presentation import MessageWidget, TranscriptView
from app.desktop.state import TranscriptEntry


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def test_completed_assistant_message_gets_quiet_action_footer(app, monkeypatch):
    monkeypatch.setenv("LOOM_REDUCE_MOTION", "1")
    message = MessageWidget("assistant")
    message.set_streaming(True)
    message.set_text("Finished answer")

    assert message.message_actions.isHidden() is True

    message.set_streaming(False)

    footer = message.message_actions
    assert footer.isHidden() is False
    assert footer.copy_button.toolTip() == "Copy response"
    assert footer.dislike_button.toolTip() == "Not helpful"
    assert footer.open_button.toolTip() == "Open response"
    assert footer.time_label.text()
    message.close()


def test_user_message_never_gets_assistant_actions(app):
    message = MessageWidget("user")
    message.set_text("hello")

    assert message.message_actions is None
    message.close()


def test_copy_and_feedback_actions_are_functional(app, monkeypatch):
    monkeypatch.setenv("LOOM_REDUCE_MOTION", "1")
    message = MessageWidget("assistant")
    message.set_text("copy this exact response")
    message.set_streaming(False)

    footer = message.message_actions
    footer.copy_button.click()
    app.processEvents()
    assert QApplication.clipboard().text() == "copy this exact response"
    assert bool(footer.copy_button.property("success")) is True

    assert footer.dislike_button.isChecked() is False
    footer.dislike_button.click()
    assert footer.dislike_button.isChecked() is True
    footer.dislike_button.click()
    assert footer.dislike_button.isChecked() is False
    message.close()


def test_transcript_uses_durable_response_time(app, monkeypatch):
    monkeypatch.setenv("LOOM_REDUCE_MOTION", "1")
    view = TranscriptView()
    view.resize(720, 420)
    entry = TranscriptEntry(
        key="assistant:1",
        kind="assistant",
        text="done",
        streaming=False,
        item={
            "type": "assistant_message",
            "status": "completed",
            "updatedAt": "2026-09-09T10:49:00",
        },
    )

    view.render([entry])
    app.processEvents()

    message = view._widgets[entry.key]
    assert message.message_actions.isHidden() is False
    assert message.message_actions.time_label.text() == "10:49"
    view.close()
