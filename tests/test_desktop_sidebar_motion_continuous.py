from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QFrame, QLabel, QSplitter, QVBoxLayout
from PySide6.QtCore import Qt

from app.desktop import sidebar_motion_smooth as smooth


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def _splitter(*, with_layouts: bool = False):
    splitter = QSplitter(Qt.Orientation.Horizontal)
    left = QFrame()
    center = QFrame()
    right = QFrame()
    left.setMinimumWidth(0)
    right.setMinimumWidth(0)
    center.setMinimumWidth(120)

    if with_layouts:
        for widget, text in ((left, "left"), (center, "center"), (right, "right")):
            layout = QVBoxLayout(widget)
            # A child is enough to make the QLayout state meaningful without
            # turning this regression test into a performance benchmark.
            layout.addWidget(QLabel(text, widget))

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
    splitter, left, center, right = _splitter(with_layouts=True)
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
    assert left.layout().isEnabled() is False
    assert center.layout().isEnabled() is False

    closing.setCurrentTime(max(1, closing.duration() // 2))
    QApplication.processEvents()

    halfway = splitter.sizes()[0]
    assert left.isVisible()
    assert left.graphicsEffect() is None
    assert 0 < halfway < 280

    # Reverse immediately. The new animation must start from the actual halfway
    # splitter geometry and retain the same frozen layouts (no release spike).
    smooth._set_panel_visible(controller, "sidebar", left, True)
    opening = controller._panel_animations["sidebar"]
    assert abs(int(opening.startValue()) - splitter.sizes()[0]) <= 2
    assert int(opening.endValue()) == 280
    assert left.layout().isEnabled() is False
    assert center.layout().isEnabled() is False

    opening.setCurrentTime(opening.duration())
    QApplication.processEvents()
    assert left.isVisible()
    assert abs(splitter.sizes()[0] - 280) <= 3
    assert left.minimumWidth() == 262
    assert left.maximumWidth() == 350
    assert left.layout().isEnabled() is True
    assert center.layout().isEnabled() is True
    splitter.close()


def test_deep_layouts_reflow_only_after_endpoint(app, monkeypatch):
    monkeypatch.setattr(smooth.theme, "motion_enabled", lambda: True)
    splitter, left, center, right = _splitter(with_layouts=True)
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
    animation = controller._panel_animations["sidebar"]

    assert left.layout().isEnabled() is False
    assert center.layout().isEnabled() is False
    assert right.layout().isEnabled() is True

    animation.setCurrentTime(animation.duration() // 2)
    QApplication.processEvents()
    assert left.layout().isEnabled() is False
    assert center.layout().isEnabled() is False

    animation.setCurrentTime(animation.duration())
    QApplication.processEvents()
    assert left.layout().isEnabled() is True
    assert center.layout().isEnabled() is True
    assert right.layout().isEnabled() is True
    splitter.close()


def test_shared_conversation_layout_stays_frozen_until_both_sides_settle(app, monkeypatch):
    monkeypatch.setattr(smooth.theme, "motion_enabled", lambda: True)
    splitter, left, center, right = _splitter(with_layouts=True)
    left.setMinimumWidth(262)
    left.setMaximumWidth(350)
    right.setMinimumWidth(330)
    right.setMaximumWidth(430)
    splitter.setSizes([280, 360, 360])
    QApplication.processEvents()

    controller = SimpleNamespace(
        window=SimpleNamespace(main_splitter=splitter),
        _panel_constraints={"sidebar": (262, 350), "runtime": (330, 430)},
        _panel_widths={"sidebar": 280, "runtime": 360},
        _panel_animations={},
    )

    smooth._set_panel_visible(controller, "sidebar", left, False)
    smooth._set_panel_visible(controller, "runtime", right, False)
    left_animation = controller._panel_animations["sidebar"]
    right_animation = controller._panel_animations["runtime"]

    assert left.layout().isEnabled() is False
    assert center.layout().isEnabled() is False
    assert right.layout().isEnabled() is False

    left_animation.setCurrentTime(left_animation.duration())
    QApplication.processEvents()
    assert left.layout().isEnabled() is True
    # Runtime still owns one reference to the shared conversation layout.
    assert center.layout().isEnabled() is False
    assert right.layout().isEnabled() is False

    right_animation.setCurrentTime(right_animation.duration())
    QApplication.processEvents()
    assert center.layout().isEnabled() is True
    assert right.layout().isEnabled() is True
    splitter.close()
