from __future__ import annotations

import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtCore import QAbstractAnimation
from PySide6.QtWidgets import QAbstractItemView, QApplication, QListWidget

from app.desktop_ui_v4 import SmoothThreadScrollController


def _wait_for(app: QApplication, predicate, timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("Qt scroll animation did not settle")


def _scrollable_list() -> tuple[QApplication, QListWidget, SmoothThreadScrollController]:
    app = QApplication.instance() or QApplication([])
    view = QListWidget()
    view.resize(260, 220)
    for index in range(60):
        view.addItem(f"thread {index}")
    view.show()
    app.processEvents()
    controller = SmoothThreadScrollController(view)
    return app, view, controller


def test_thread_scroll_uses_pixel_motion_and_accumulates_wheel_targets() -> None:
    app, view, controller = _scrollable_list()
    try:
        bar = view.verticalScrollBar()
        assert view.verticalScrollMode() == QAbstractItemView.ScrollMode.ScrollPerPixel
        assert bar.maximum() > 0

        controller.scroll_by(74)
        first_target = controller.target_value
        assert first_target > 0
        controller.scroll_by(74)
        second_target = controller.target_value
        assert second_target > first_target

        _wait_for(app, lambda: controller.animation.state() == QAbstractAnimation.State.Stopped)
        assert bar.value() == second_target
    finally:
        view.close()
        app.processEvents()


def test_thread_scroll_precision_input_is_short_and_target_is_clamped() -> None:
    app, view, controller = _scrollable_list()
    try:
        bar = view.verticalScrollBar()
        controller.scroll_by(42, precise=True)
        assert controller.animation.duration() == controller.PRECISE_DURATION_MS
        assert 0 < controller.target_value <= bar.maximum()

        controller.cancel()
        controller.scroll_by(1_000_000)
        assert controller.target_value <= bar.maximum()
        assert controller.target_value > 0
    finally:
        view.close()
        app.processEvents()
