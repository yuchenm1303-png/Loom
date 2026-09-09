from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QFrame, QSplitter
from PySide6.QtCore import Qt

from app.desktop import sidebar_motion_smooth as smooth


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def _splitter():
    splitter = QSplitter(Qt.Orientation.Horizontal)
    left = QFrame()
    center = QFrame()
    right = QFrame()
    left.setMinimumWidth(0)
    right.setMinimumWidth(0)
    center.setMinimumWidth(120)
    splitter.addWidget(left)
    splitter.addWidget(center)
    splitter.addWidget(right)
    splitter.resize(1000, 500)
    splitter.setSizes([280, 360, 360])
    splitter.show()
    QApplication.processEvents()
    return splitter, left, center, right


def test_width_transfer_moves_only_panel_and_conversation(app):
    splitter, left, center, right = _splitter()
    before = splitter.sizes()

    actual = smooth._apply_splitter_width(splitter, 0, before[0] - 90)
    after = splitter.sizes()

    assert abs(actual - (before[0] - 90)) <= 2
    assert abs(after[1] - (before[1] + 90)) <= 3
    assert abs(after[2] - before[2]) <= 2
    splitter.close()


def test_runtime_width_transfer_does_not_nudge_left_sidebar(app):
    splitter, left, center, right = _splitter()
    before = splitter.sizes()

    smooth._apply_splitter_width(splitter, 2, before[2] - 80)
    after = splitter.sizes()

    assert abs(after[0] - before[0]) <= 2
    assert abs(after[1] - (before[1] + 80)) <= 3
    assert abs(after[2] - (before[2] - 80)) <= 2
    splitter.close()


def test_short_reversal_uses_less_time_than_a_full_open():
    full = smooth._transition_duration(opening=True, start=0, end=300, full_width=300)
    partial = smooth._transition_duration(opening=True, start=220, end=300, full_width=300)

    assert full == smooth._OPEN_DURATION_MS
    assert smooth._MIN_REVERSAL_MS <= partial < full


def test_panel_remains_painted_during_close_and_can_reverse(app, monkeypatch):
    monkeypatch.setattr(smooth.theme, "motion_enabled", lambda: True)
    splitter, left, center, right = _splitter()
    left.setMinimumWidth(262)
    left.setMaximumWidth(350)
    splitter.setSizes([280, 360, 360])
    QApplication.processEvents()

    controller = SimpleNamespace(
        window=SimpleNamespace(main_splitter=splitter),
        _panel_constraints={"sidebar": (262, 350)},
        _panel_widths={"sidebar": 280},
        _panel_animations={},
    )

    smooth._set_panel_visible(controller, "sidebar", left, False)
    closing = controller._panel_animations["sidebar"]
    closing.setCurrentTime(max(1, closing.duration() // 2))
    QApplication.processEvents()

    halfway = splitter.sizes()[0]
    assert left.isVisible()
    assert left.graphicsEffect() is None
    assert 0 < halfway < 280

    # Reverse immediately. The new animation must start from the actual halfway
    # splitter geometry instead of snapping back to 0 or the old open width.
    smooth._set_panel_visible(controller, "sidebar", left, True)
    opening = controller._panel_animations["sidebar"]
    assert abs(int(opening.startValue()) - splitter.sizes()[0]) <= 2
    assert int(opening.endValue()) == 280

    opening.setCurrentTime(opening.duration())
    QApplication.processEvents()
    assert left.isVisible()
    assert abs(splitter.sizes()[0] - 280) <= 3
    assert left.minimumWidth() == 262
    assert left.maximumWidth() == 350
    splitter.close()
