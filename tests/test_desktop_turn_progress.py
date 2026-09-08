from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication

from app.desktop import theme
from app.desktop.turn_progress import ShimmerLabel, TurnProgressLine
from app.desktop.widgets import CenteredColumn


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def test_turn_progress_line_is_visible_during_work_and_reports_completion(app):
    line = TurnProgressLine()
    host = CenteredColumn(line, 838)

    line.set_state("Thinking…", tone="working", active=True)
    app.processEvents()

    assert not line.isHidden()
    assert not host.isHidden()
    assert line.text() == "Thinking…"
    assert line.property("tone") == "working"
    assert isinstance(line.label, ShimmerLabel)
    assert line.label._shimmer_requested is True

    line.set_state("Completed · 17s", tone="done")
    app.processEvents()

    assert line.text() == "Completed · 17s"
    assert line.property("tone") == "done"
    assert line._pulse is None
    assert line.label._shimmer_requested is False


def test_live_status_uses_a_real_shimmer_animation_when_motion_is_enabled(app, monkeypatch):
    monkeypatch.setattr(theme, "motion_enabled", lambda: True)
    line = TurnProgressLine()
    host = CenteredColumn(line, 838)
    host.show()

    line.set_state("Processing…", tone="working", active=True)
    app.processEvents()

    assert line.label._shimmer is not None
    assert line.label._shimmer.loopCount() == -1
    assert line.label._shimmer.duration() == 1450

    line.set_state("Completed · 1s", tone="done")
    app.processEvents()
    assert line.label._shimmer is None


def test_turn_progress_line_collapses_completely_when_idle(app):
    line = TurnProgressLine()
    host = CenteredColumn(line, 838)

    line.set_state("Working · 3s", tone="working", active=True)
    line.set_state("")
    app.processEvents()

    assert line.isHidden()
    assert host.isHidden()
    assert line.text() == ""
    assert line._pulse is None
    assert line.label._shimmer_requested is False
