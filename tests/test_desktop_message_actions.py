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


def _entry(key: str, kind: str, text: str = "", *, streaming: bool = False) -> TranscriptEntry:
    item_type = {
        "user": "user_message",
        "assistant": "assistant_message",
        "tool": "tool_call",
    }.get(kind, kind)
    item = {
        "id": key,
        "type": item_type,
        "status": "running" if streaming else "completed",
    }
    if text:
        item["text"] = text
    if kind == "tool":
        item["toolName"] = "exec"
    return TranscriptEntry(
        key=key,
        kind=kind,
        text=text,
        streaming=streaming,
        item=item,
    )


def test_completed_item_does_not_get_footer_while_turn_is_still_active(app, monkeypatch):
    """item/completed is not the end of an agent turn when tools can follow."""
    monkeypatch.setenv("LOOM_REDUCE_MOTION", "1")
    view = TranscriptView()
    view.resize(720, 420)
    view.setProperty("turnActive", True)
    entries = [
        _entry("user:1", "user", "Clean my disk"),
        _entry("assistant:1", "assistant", "I will scan it first."),
        _entry("tool:1", "tool"),
    ]

    view.render(entries)
    app.processEvents()

    assert view._widgets["assistant:1"].message_actions.isHidden() is True
    view.close()


def test_only_last_assistant_in_completed_turn_gets_footer(app, monkeypatch):
    """Intermediate narration stays chrome-free even after the full turn ends."""
    monkeypatch.setenv("LOOM_REDUCE_MOTION", "1")
    view = TranscriptView()
    view.resize(720, 420)
    view.setProperty("turnActive", False)
    entries = [
        _entry("user:1", "user", "Clean my disk"),
        _entry("assistant:1", "assistant", "I will scan it first."),
        _entry("tool:1", "tool"),
        _entry("assistant:2", "assistant", "Cleanup finished."),
    ]

    view.render(entries)
    app.processEvents()

    assert view._widgets["assistant:1"].message_actions.isHidden() is True
    assert view._widgets["assistant:2"].message_actions.isHidden() is False
    view.close()


def test_previous_turn_footer_stays_while_new_turn_is_active(app, monkeypatch):
    monkeypatch.setenv("LOOM_REDUCE_MOTION", "1")
    view = TranscriptView()
    view.resize(720, 420)
    view.setProperty("turnActive", True)
    entries = [
        _entry("user:1", "user", "First question"),
        _entry("assistant:1", "assistant", "First final answer"),
        _entry("user:2", "user", "Second question"),
        _entry("assistant:2", "assistant", "Working on it"),
    ]

    view.render(entries)
    app.processEvents()

    assert view._widgets["assistant:1"].message_actions.isHidden() is False
    assert view._widgets["assistant:2"].message_actions.isHidden() is True
    view.close()
