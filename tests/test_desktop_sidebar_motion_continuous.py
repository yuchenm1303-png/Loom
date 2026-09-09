from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtCore import QRect, Qt
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QLabel,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from app.desktop import sidebar_motion_smooth as smooth


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def _splitter():
    host = QWidget()
    layout = QVBoxLayout(host)
    layout.setContentsMargins(0, 0, 0, 0)

    splitter = QSplitter(Qt.Orientation.Horizontal, host)
    left = QFrame()
    center = QFrame()
    right = QFrame()

    left.setMinimumWidth(0)
    right.setMinimumWidth(0)
    center.setMinimumWidth(120)

    for widget, text in ((left, "left"), (center, "center"), (right, "right")):
        child_layout = QVBoxLayout(widget)
        child_layout.addWidget(QLabel(text, widget))

    splitter.addWidget(left)
    splitter.addWidget(center)
    splitter.addWidget(right)
    layout.addWidget(splitter)

    host.resize(1000, 500)
    splitter.setSizes([280, 360, 360])
    host.show()
    QApplication.processEvents()
    return host, splitter, left, center, right


def _controller(splitter, left, right):
    return SimpleNamespace(
        window=SimpleNamespace(main_splitter=splitter),
        _panel_constraints={"sidebar": (262, 350), "runtime": (330, 430)},
        _panel_widths={"sidebar": max(280, left.width()), "runtime": max(360, right.width())},
        _panel_animations={},
        _panel_snapshot_transition=None,
        _panel_snapshot_pending=None,
    )


def test_rect_interpolation_is_monotonic_and_exact():
    start = QRect(0, 0, 0, 500)
    end = QRect(0, 0, 280, 500)

    assert smooth._lerp_rect(start, end, 0.0) == start
    assert smooth._lerp_rect(start, end, 1.0) == end
    assert 130 <= smooth._lerp_rect(start, end, 0.5).width() <= 150


def test_reversal_duration_is_shorter_than_full_motion():
    full = smooth._transition_duration(
        opening=True,
        start_progress=0.0,
        end_progress=1.0,
    )
    partial = smooth._transition_duration(
        opening=True,
        start_progress=0.72,
        end_progress=1.0,
    )

    assert full == smooth._OPEN_DURATION_MS
    assert smooth._MIN_REVERSAL_MS <= partial < full


def test_real_splitter_commits_once_then_stays_fixed_during_animation(app, monkeypatch):
    monkeypatch.setattr(smooth.theme, "motion_enabled", lambda: True)
    host, splitter, left, center, right = _splitter()
    left.setMinimumWidth(262)
    left.setMaximumWidth(350)
    right.setMinimumWidth(330)
    right.setMaximumWidth(430)
    splitter.setSizes([280, 360, 360])
    app.processEvents()

    controller = _controller(splitter, left, right)
    before = splitter.sizes()

    smooth._set_panel_visible(controller, "sidebar", left, False)
    transition = controller._panel_snapshot_transition
    animation = controller._panel_animations["sidebar"]

    # The live tree is already at the destination and painting is suspended.
    settled_sizes = splitter.sizes()
    assert transition is not None
    assert left.isHidden()
    assert settled_sizes[0] == 0
    assert settled_sizes[1] > before[1]
    assert abs(settled_sizes[2] - before[2]) <= 3
    assert splitter.updatesEnabled() is False
    assert transition.overlay.isVisible()

    animation.setCurrentTime(max(1, animation.duration() // 2))
    app.processEvents()

    # Mid-animation frames only repaint the snapshot overlay. The heavyweight
    # splitter geometry must not move again.
    assert splitter.sizes() == settled_sizes

    animation.setCurrentTime(animation.duration())
    app.processEvents()
    assert splitter.updatesEnabled() is True
    assert controller._panel_snapshot_transition is None
    assert left.isHidden()
    host.close()


def test_same_side_reversal_reuses_snapshot_and_restores_origin(app, monkeypatch):
    monkeypatch.setattr(smooth.theme, "motion_enabled", lambda: True)
    host, splitter, left, center, right = _splitter()
    left.setMinimumWidth(262)
    left.setMaximumWidth(350)
    right.setMinimumWidth(330)
    right.setMaximumWidth(430)
    splitter.setSizes([280, 360, 360])
    app.processEvents()

    controller = _controller(splitter, left, right)
    origin_width = splitter.sizes()[0]

    smooth._set_panel_visible(controller, "sidebar", left, False)
    transition = controller._panel_snapshot_transition
    closing = controller._panel_animations["sidebar"]
    closing.setCurrentTime(max(1, closing.duration() // 2))
    app.processEvents()
    halfway_progress = transition.overlay.progress
    assert 0.0 < halfway_progress < 1.0

    smooth._set_panel_visible(controller, "sidebar", left, True)
    assert controller._panel_snapshot_transition is transition
    opening = controller._panel_animations["sidebar"]
    assert float(opening.startValue()) == pytest.approx(halfway_progress, abs=0.02)
    assert float(opening.endValue()) == pytest.approx(0.0)
    # The live splitter stays at the hidden endpoint until the snapshot has
    # visually travelled back to the origin.
    assert left.isHidden()

    opening.setCurrentTime(opening.duration())
    app.processEvents()

    assert left.isVisible()
    assert abs(splitter.sizes()[0] - origin_width) <= 3
    assert splitter.updatesEnabled() is True
    assert controller._panel_snapshot_transition is None
    host.close()


def test_opposite_side_request_queues_behind_single_compositor(app, monkeypatch):
    monkeypatch.setattr(smooth.theme, "motion_enabled", lambda: True)
    host, splitter, left, center, right = _splitter()
    left.setMinimumWidth(262)
    left.setMaximumWidth(350)
    right.setMinimumWidth(330)
    right.setMaximumWidth(430)
    splitter.setSizes([280, 360, 360])
    app.processEvents()

    controller = _controller(splitter, left, right)

    smooth._set_panel_visible(controller, "sidebar", left, False)
    left_transition = controller._panel_snapshot_transition
    smooth._set_panel_visible(controller, "runtime", right, False)

    assert controller._panel_snapshot_transition is left_transition
    assert controller._panel_snapshot_pending is not None
    assert controller._panel_snapshot_pending[0] == "runtime"

    left_animation = controller._panel_animations["sidebar"]
    left_animation.setCurrentTime(left_animation.duration())
    app.processEvents()

    runtime_transition = controller._panel_snapshot_transition
    assert runtime_transition is not None
    assert runtime_transition.key == "runtime"
    runtime_animation = controller._panel_animations["runtime"]
    runtime_animation.setCurrentTime(runtime_animation.duration())
    app.processEvents()

    assert left.isHidden()
    assert right.isHidden()
    assert splitter.updatesEnabled() is True
    host.close()


def test_reduced_motion_commits_immediately_without_overlay(app, monkeypatch):
    monkeypatch.setattr(smooth.theme, "motion_enabled", lambda: False)
    host, splitter, left, center, right = _splitter()
    left.setMinimumWidth(262)
    left.setMaximumWidth(350)
    right.setMinimumWidth(330)
    right.setMaximumWidth(430)
    splitter.setSizes([280, 360, 360])
    app.processEvents()

    controller = _controller(splitter, left, right)
    smooth._set_panel_visible(controller, "runtime", right, False)

    assert right.isHidden()
    assert controller._panel_snapshot_transition is None
    assert controller._panel_animations == {}
    assert splitter.updatesEnabled() is True
    host.close()
