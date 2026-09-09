from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtCore import QPoint, Qt
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


def _viewport_y(view: TranscriptView, widget) -> int:
    return widget.mapTo(view.viewport(), QPoint(0, 0)).y()


def test_public_transcript_uses_anchored_viewport_policy(app):
    assert issubclass(TranscriptView, AnchoredTranscriptView)


def test_transcript_reserves_scrollbar_gutter_before_disclosure(app):
    view = TranscriptView()
    view.resize(760, 260)
    view.show()
    app.processEvents()

    assert view.verticalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAlwaysOn
    width_before = view.viewport().width()

    entry = _tool_entry("tool:gutter", content="wide output\n" * 40)
    view.render([entry])
    app.processEvents()
    card = view._widgets[entry.key]
    card.set_expanded(True, animate=False, user=True)
    app.processEvents()

    # Opening a panel may grow the scroll range, but it must not change the
    # viewport width and rewrap every message on screen.
    assert view.viewport().width() == width_before
    view.close()


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
    before = _viewport_y(view, card.toggle_button)
    card.toggle_button.clicked.emit()
    app.processEvents()
    after = _viewport_y(view, card.toggle_button)
    assert after == pytest.approx(before, abs=1)
    view.close()


def test_opening_disclosure_moves_only_rows_below_the_clicked_header(app):
    view = TranscriptView()
    view.resize(820, 520)
    view.show()
    entries = [
        _tool_entry("tool:above", content="above"),
        _tool_entry("tool:target", content="detail line\n" * 18),
        _tool_entry("tool:below", content="below"),
    ]
    view.render(entries)
    app.processEvents()

    above = view._widgets[entries[0].key]
    target = view._widgets[entries[1].key]
    below = view._widgets[entries[2].key]
    assert isinstance(target, FlowActivityCard)

    # Measure the target once at its natural size, then return to the collapsed
    # state so the intermediate frame below is deterministic.
    target.set_expanded(True, animate=False, user=True)
    app.processEvents()
    natural = target.reveal.target_height
    assert natural > 0
    target.set_expanded(False, animate=False, user=True)
    app.processEvents()

    above_before = _viewport_y(view, above)
    header_before = _viewport_y(view, target.toggle_button)
    below_before = _viewport_y(view, below)

    target.toggle_button.pressed.emit()
    target._expanded = True
    target.reveal._expanded = True
    target.reveal.refresh_target()
    target.reveal._set_progress(0.5)
    app.processEvents()

    # This is the visual contract: the click/header and everything above it stay
    # pinned. Only content after the disclosure moves down with the growing body.
    assert _viewport_y(view, above) == pytest.approx(above_before, abs=1)
    assert _viewport_y(view, target.toggle_button) == pytest.approx(header_before, abs=1)
    assert _viewport_y(view, below) > below_before
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


def test_collapsing_reasoning_near_tail_never_moves_thought_header(app):
    view = TranscriptView()
    view.resize(760, 260)
    view.show()

    entries = [
        TranscriptEntry(
            key="assistant:before",
            kind="assistant",
            text="Earlier context.\n\n" * 8,
        ),
        TranscriptEntry(
            key="assistant:target",
            kind="assistant",
            text=(
                "<think>"
                "Inspect one.\nInspect two.\nInspect three.\nInspect four.\n"
                "Inspect five.\nInspect six."
                "</think>Done."
            ),
        ),
    ]
    view.render(entries)
    app.processEvents()

    message = view._widgets["assistant:target"]
    assert isinstance(message, FlowMessageWidget)
    reasoning = message.reasoning
    reasoning.set_expanded(True, animate=False)
    app.processEvents()

    bar = view.verticalScrollBar()
    bar.setValue(bar.maximum())
    app.processEvents()
    assert bar.maximum() > 0

    header_before = _viewport_y(view, reasoning.toggle)
    range_before = bar.maximum()
    canvas_height_before = view.canvas.height()

    # ``pressed`` is the exact point where the viewport policy gets control,
    # before the click toggles the reveal. The pre-collapse canvas height must be
    # retained so Qt cannot shorten the scroll range and clamp the scrollbar.
    reasoning.toggle.pressed.emit()
    assert view.canvas.minimumHeight() >= canvas_height_before

    reasoning._expanded = False
    reasoning.reveal._expanded = False
    reasoning.reveal._set_progress(0.5)
    app.processEvents()

    assert bar.maximum() == range_before
    assert _viewport_y(view, reasoning.toggle) == pytest.approx(header_before, abs=1)

    reasoning.reveal._set_progress(0.0)
    app.processEvents()
    assert bar.maximum() == range_before
    assert _viewport_y(view, reasoning.toggle) == pytest.approx(header_before, abs=1)

    reasoning.reveal.expandedChanged.emit(False)
    app.processEvents()
    assert _viewport_y(view, reasoning.toggle) == pytest.approx(header_before, abs=1)
    view.close()
