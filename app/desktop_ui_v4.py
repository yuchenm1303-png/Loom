from __future__ import annotations

from typing import Any

from PySide6.QtCore import (
    QAbstractAnimation,
    QEasingCurve,
    QEvent,
    QObject,
    QPropertyAnimation,
    Qt,
)
from PySide6.QtWidgets import QAbstractItemView, QListWidget

from app import desktop_ui_v3 as v3


DesktopEventBridge = v3.DesktopEventBridge
ComposerTextEdit = v3.ComposerTextEdit
ThreadListItemWidget = v3.ThreadListItemWidget


class SmoothThreadScrollController(QObject):
    """Pixel-based inertial wheel scrolling for the durable thread list.

    Mouse-wheel notches accumulate into one moving target so repeated input feels
    continuous instead of restarting one item at a time. Precision touchpad input
    uses a shorter easing window to avoid the heavy, delayed feeling that desktop
    "smooth scrolling" often introduces.
    """

    WHEEL_STEP_PX = 74
    WHEEL_DURATION_MS = 190
    PRECISE_DURATION_MS = 92

    def __init__(self, view: QListWidget) -> None:
        super().__init__(view)
        self.view = view
        self.scrollbar = view.verticalScrollBar()
        self._target = float(self.scrollbar.value())

        view.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        view.viewport().installEventFilter(self)

        self.animation = QPropertyAnimation(self.scrollbar, b"value", self)
        self.animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.animation.finished.connect(self._animation_finished)
        self.scrollbar.sliderPressed.connect(self.cancel)
        self.scrollbar.valueChanged.connect(self._sync_idle_target)

    @property
    def target_value(self) -> int:
        return int(round(self._target))

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802 - Qt override
        if watched is not self.view.viewport() or event.type() != QEvent.Type.Wheel:
            return super().eventFilter(watched, event)

        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            return False

        pixel_delta = event.pixelDelta()
        angle_delta = event.angleDelta()
        precise = not pixel_delta.isNull()
        vertical = pixel_delta.y() if precise else angle_delta.y()
        horizontal = pixel_delta.x() if precise else angle_delta.x()

        if not vertical or abs(horizontal) > abs(vertical):
            return False

        if precise:
            distance = -float(vertical)
        else:
            distance = -(float(vertical) / 120.0) * self.WHEEL_STEP_PX

        self.scroll_by(distance, precise=precise)
        event.accept()
        return True

    def scroll_by(self, distance: float, *, precise: bool = False) -> None:
        minimum = self.scrollbar.minimum()
        maximum = self.scrollbar.maximum()
        if maximum <= minimum:
            self._target = float(self.scrollbar.value())
            return

        current = float(self.scrollbar.value())
        if self.animation.state() == QAbstractAnimation.State.Running:
            base = self._target
        else:
            base = current

        # A single input event should never fling farther than roughly one viewport,
        # but repeated wheel input is intentionally accumulated into the target.
        max_single_jump = max(96.0, float(self.view.viewport().height()) * 0.9)
        distance = max(-max_single_jump, min(max_single_jump, distance))
        target = max(float(minimum), min(float(maximum), base + distance))
        self._target = target

        if round(target) == round(current):
            return

        self.animation.stop()
        travel = abs(target - current)
        if precise:
            duration = self.PRECISE_DURATION_MS
        else:
            duration = int(min(235, self.WHEEL_DURATION_MS + min(45.0, travel * 0.09)))

        self.animation.setDuration(duration)
        self.animation.setStartValue(int(round(current)))
        self.animation.setEndValue(int(round(target)))
        self.animation.start()

    def cancel(self) -> None:
        self.animation.stop()
        self._target = float(self.scrollbar.value())

    def _animation_finished(self) -> None:
        self._target = float(self.scrollbar.value())

    def _sync_idle_target(self, value: int) -> None:
        if self.animation.state() != QAbstractAnimation.State.Running:
            self._target = float(value)


class LoomDesktopWindow(v3.LoomDesktopWindow):
    """Desktop v4: retain v3 layout while refining thread-list motion."""

    def _build_ui(self) -> None:
        super()._build_ui()
        self.thread_scroll_controller = SmoothThreadScrollController(self.thread_list)

    def _apply_style(self) -> None:
        super()._apply_style()
        self.setStyleSheet(
            self.styleSheet()
            + """
            QListWidget#threadList {
                padding-right: 2px;
            }
            QListWidget#threadList QScrollBar:vertical {
                background: transparent;
                width: 7px;
                margin: 4px 1px 4px 1px;
            }
            QListWidget#threadList QScrollBar::handle:vertical {
                background: #272c37;
                min-height: 38px;
                border-radius: 3px;
            }
            QListWidget#threadList QScrollBar::handle:vertical:hover,
            QListWidget#threadList QScrollBar::handle:vertical:pressed {
                background: #424957;
            }
            QListWidget#threadList QScrollBar::add-line:vertical,
            QListWidget#threadList QScrollBar::sub-line:vertical {
                height: 0;
                background: transparent;
                border: none;
            }
            QListWidget#threadList QScrollBar::add-page:vertical,
            QListWidget#threadList QScrollBar::sub-page:vertical {
                background: transparent;
            }
            """
        )
