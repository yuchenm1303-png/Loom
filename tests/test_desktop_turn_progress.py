from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication

from app.desktop.turn_progress import TurnProgressLine
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

    line.set_state("Completed · 17s", tone="done")
    app.processEvents()

    assert line.text() == "Completed · 17s"
    assert line.property("tone") == "done"
    assert line._pulse is None


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
