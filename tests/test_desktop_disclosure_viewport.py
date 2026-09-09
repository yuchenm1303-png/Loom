from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtCore import QPoint
from PySide6.QtWidgets import QApplication

from app.desktop import TranscriptView
from app.desktop.state import TranscriptEntry
from app.desktop.transcript_disclosure import FlowActivityCard, FlowMessageWidget
from app.desktop.transcript_viewport import AnchoredTranscriptView


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def _tool_entry(key: str, *, content: str = "output\n" * 12) -> TranscriptEntry:
    return TranscriptEntry(
        key=key,
        kind="tool",
        item={
            "type": "tool_call",
            "toolName": "exec",
            "status": "completed",
            "arguments": {"argv": ["python", "-V"]},
            "content": content,
        },
    )


def test_public_transcript_uses_anchored_viewport_policy(app):
    assert issubclass(TranscriptView, AnchoredTranscriptView)


def test_manual_task_disclosure_parks_tail_before_geometry_changes(app):
    view = TranscriptView()
    view.resize(760, 260)
    view.show()
    entry = _tool_entry("tool:anchor")
    view.render([entry])
    app.processEvents()

    card = view._widgets[entry.key]
    assert isinstance(card, FlowActivityCard)

    view._follow_tail = True
    view._auto_scrolling = True
    card.toggle_button.pressed.emit()

    assert view._follow_tail is False
    assert view._auto_scrolling is False

    # The click handler parks the viewport before ``clicked`` starts the real
    # height animation, so rangeChanged cannot pull the whole transcript upward.
    before = card.toggle_button.mapTo(view.viewport(), QPoint(0, 0)).y()
    card.toggle_button.clicked.emit()
    app.processEvents()
    after = card.toggle_button.mapTo(view.viewport(), QPoint(0, 0)).y()
    assert after == pytest.approx(before, abs=1)
    view.close()


def test_manual_reasoning_disclosure_uses_same_anchor_policy(app):
    view = TranscriptView()
    view.resize(760, 260)
    view.show()
    entry = TranscriptEntry(
        key="assistant:anchor",
        kind="assistant",
        text="<think>Inspect the environment, then answer.</think>Done.",
    )
    view.render([entry])
    app.processEvents()

    message = view._widgets[entry.key]
    assert isinstance(message, FlowMessageWidget)
    reasoning = message.reasoning

    view._follow_tail = True
    view._auto_scrolling = True
    reasoning.toggle.pressed.emit()

    assert view._follow_tail is False
    assert view._auto_scrolling is False
    view.close()
