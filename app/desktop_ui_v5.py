from __future__ import annotations

import os
from typing import Any

from PySide6.QtCore import (
    QAbstractAnimation,
    QEasingCurve,
    QEvent,
    QObject,
    QPropertyAnimation,
    QTimer,
    QVariantAnimation,
)
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QGraphicsDropShadowEffect,
    QGraphicsOpacityEffect,
    QPushButton,
    QWidget,
)

from app import desktop_ui_v2 as v2
from app import desktop_ui_v4 as v4


DesktopEventBridge = v4.DesktopEventBridge
ComposerTextEdit = v4.ComposerTextEdit
ThreadListItemWidget = v4.ThreadListItemWidget


def _motion_enabled_from_env() -> bool:
    value = os.getenv("LOOM_REDUCE_MOTION", "").strip().casefold()
    return value not in {"1", "true", "yes", "on"}


class LoomDesktopWindow(v4.LoomDesktopWindow):
    """Desktop v5: a restrained motion layer over the stable native workspace UI."""

    PANEL_DURATION_MS = 185
    COMPOSER_DURATION_MS = 120
    TAB_DURATION_MS = 135
    PULSE_DURATION_MS = 150

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self._motion_enabled = _motion_enabled_from_env()
        self._motion_animations: dict[str, QAbstractAnimation] = {}
        self._composer_height_animation: QVariantAnimation | None = None
        self._button_glows: dict[QWidget, QGraphicsDropShadowEffect] = {}
        self._sidebar_motion_bounds = (0, 0)
        self._runtime_motion_bounds = (0, 0)
        self._sidebar_restore_width = 270
        self._runtime_restore_width = 300
        self._last_transcript_card_count = 0
        self._first_show_motion_done = False
        super().__init__(*args, **kwargs)

    def _build_ui(self) -> None:
        super()._build_ui()

        self._sidebar_motion_bounds = (
            self.sidebar_panel.minimumWidth(),
            self.sidebar_panel.maximumWidth(),
        )
        self._runtime_motion_bounds = (
            self.activity_panel.minimumWidth(),
            self.activity_panel.maximumWidth(),
        )
        self._sidebar_restore_width = max(
            self._sidebar_motion_bounds[0],
            min(self.sidebar_panel.width() or 270, self._sidebar_motion_bounds[1]),
        )
        self._runtime_restore_width = max(
            self._runtime_motion_bounds[0],
            min(self.activity_panel.width() or 300, self._runtime_motion_bounds[1]),
        )

        self.composer_frame = self.composer.parentWidget()
        self._composer_focus_effect = QGraphicsDropShadowEffect(self.composer_frame)
        self._composer_focus_effect.setColor(QColor(95, 87, 201, 100))
        self._composer_focus_effect.setOffset(0, 0)
        self._composer_focus_effect.setBlurRadius(0)
        self.composer_frame.setGraphicsEffect(self._composer_focus_effect)

        if self._motion_enabled:
            self.composer.installEventFilter(self)
            self.thread_list.currentItemChanged.connect(self._animate_selected_thread)
            self.activity_tabs.currentChanged.connect(self._animate_activity_tab)
            self.refresh_button.clicked.connect(
                lambda: self._pulse_widget(self.refresh_button, "refresh", start=0.48)
            )
            self._install_button_motion()

    def _install_button_motion(self) -> None:
        primary = {
            self.new_thread_button,
            self.send_button,
            self.allow_button,
        }
        buttons: list[QPushButton] = [
            self.new_thread_button,
            self.open_project_button,
            self.send_button,
            self.stop_button,
            self.allow_button,
            self.deny_button,
            self.sidebar_toggle_button,
            self.runtime_toggle_button,
        ]
        buttons.extend(self.findChildren(QPushButton, "promptSuggestion"))

        for button in buttons:
            if button in self._button_glows:
                continue
            effect = QGraphicsDropShadowEffect(button)
            effect.setOffset(0, 2)
            if button in primary:
                effect.setColor(QColor(100, 91, 210, 120))
            else:
                effect.setColor(QColor(89, 92, 132, 72))
            effect.setBlurRadius(0)
            button.setGraphicsEffect(effect)
            button.installEventFilter(self)
            self._button_glows[button] = effect

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802 - Qt override
        if watched is self.composer:
            if event.type() == QEvent.Type.FocusIn:
                self._animate_shadow(
                    self._composer_focus_effect,
                    "composer-focus",
                    20.0,
                    duration=165,
                )
            elif event.type() == QEvent.Type.FocusOut:
                self._animate_shadow(
                    self._composer_focus_effect,
                    "composer-focus",
                    0.0,
                    duration=150,
                )

        effect = self._button_glows.get(watched) if isinstance(watched, QWidget) else None
        if effect is not None:
            if event.type() == QEvent.Type.Enter:
                self._animate_shadow(effect, f"button:{id(watched)}", 15.0, duration=120)
            elif event.type() == QEvent.Type.Leave:
                self._animate_shadow(effect, f"button:{id(watched)}", 0.0, duration=145)
            elif event.type() == QEvent.Type.MouseButtonPress:
                self._animate_shadow(effect, f"button:{id(watched)}", 7.0, duration=70)
            elif event.type() == QEvent.Type.MouseButtonRelease:
                self._animate_shadow(effect, f"button:{id(watched)}", 15.0, duration=90)

        return super().eventFilter(watched, event)

    def _remember_animation(self, key: str, animation: QAbstractAnimation) -> None:
        previous = self._motion_animations.pop(key, None)
        if previous is not None:
            previous.stop()
        self._motion_animations[key] = animation

        def forget() -> None:
            if self._motion_animations.get(key) is animation:
                self._motion_animations.pop(key, None)

        animation.finished.connect(forget)
        animation.start()

    def _animate_shadow(
        self,
        effect: QGraphicsDropShadowEffect,
        key: str,
        target: float,
        *,
        duration: int,
    ) -> None:
        if not self._motion_enabled:
            effect.setBlurRadius(target)
            return
        animation = QPropertyAnimation(effect, b"blurRadius", self)
        animation.setStartValue(effect.blurRadius())
        animation.setEndValue(target)
        animation.setDuration(duration)
        animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._remember_animation(key, animation)

    def _pulse_widget(
        self,
        widget: QWidget,
        key: str,
        *,
        start: float = 0.68,
        duration: int | None = None,
    ) -> None:
        if not self._motion_enabled or not widget.isVisible():
            return
        existing = widget.graphicsEffect()
        if existing is not None and not isinstance(existing, QGraphicsOpacityEffect):
            return
        effect = existing if isinstance(existing, QGraphicsOpacityEffect) else QGraphicsOpacityEffect(widget)
        if existing is None:
            widget.setGraphicsEffect(effect)
        effect.setOpacity(start)
        animation = QPropertyAnimation(effect, b"opacity", self)
        animation.setStartValue(start)
        animation.setEndValue(1.0)
        animation.setDuration(duration or self.PULSE_DURATION_MS)
        animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._remember_animation(f"opacity:{key}", animation)

    def _fade_in_widget(
        self,
        widget: QWidget,
        key: str,
        *,
        start: float = 0.18,
        duration: int = 190,
    ) -> None:
        if not self._motion_enabled or not widget.isVisible():
            return
        existing = widget.graphicsEffect()
        if existing is not None and not isinstance(existing, QGraphicsOpacityEffect):
            return
        effect = existing if isinstance(existing, QGraphicsOpacityEffect) else QGraphicsOpacityEffect(widget)
        if existing is None:
            widget.setGraphicsEffect(effect)
        effect.setOpacity(start)
        animation = QPropertyAnimation(effect, b"opacity", self)
        animation.setStartValue(start)
        animation.setEndValue(1.0)
        animation.setDuration(duration)
        animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._remember_animation(f"fade:{key}", animation)

    def _resize_composer(self) -> None:
        blocks = max(1, self.composer.document().blockCount())
        target = max(56, min(142, 36 + blocks * 20))
        current = max(1, self.composer.height())

        # This method is dispatched while the base UI is still being built.
        # Before the window is visible, use the final size directly.
        if (
            not getattr(self, "_motion_enabled", False)
            or not self.isVisible()
            or abs(current - target) <= 1
        ):
            self.composer.setFixedHeight(target)
            return

        if self._composer_height_animation is not None:
            self._composer_height_animation.stop()

        animation = QVariantAnimation(self)
        animation.setStartValue(current)
        animation.setEndValue(target)
        animation.setDuration(self.COMPOSER_DURATION_MS)
        animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        animation.valueChanged.connect(lambda value: self.composer.setFixedHeight(int(value)))
        animation.finished.connect(lambda: self.composer.setFixedHeight(target))
        self._composer_height_animation = animation
        self._remember_animation("composer-height", animation)

    def toggle_sidebar(self) -> None:
        self._animate_panel(
            panel=self.sidebar_panel,
            index=0,
            show=not self._sidebar_visible,
            bounds=self._sidebar_motion_bounds,
            restore_width_attr="_sidebar_restore_width",
            visible_attr="_sidebar_visible",
            key="sidebar",
        )

    def toggle_runtime(self) -> None:
        self._animate_panel(
            panel=self.activity_panel,
            index=2,
            show=not self._runtime_visible,
            bounds=self._runtime_motion_bounds,
            restore_width_attr="_runtime_restore_width",
            visible_attr="_runtime_visible",
            key="runtime",
        )

    def _animate_panel(
        self,
        *,
        panel: QWidget,
        index: int,
        show: bool,
        bounds: tuple[int, int],
        restore_width_attr: str,
        visible_attr: str,
        key: str,
    ) -> None:
        minimum, maximum = bounds
        if not self._motion_enabled or not self.isVisible():
            setattr(self, visible_attr, show)
            panel.setVisible(show)
            panel.setMinimumWidth(minimum)
            panel.setMaximumWidth(maximum)
            self._sync_panel_motion_state()
            return

        previous = self._motion_animations.pop(f"panel:{key}", None)
        if previous is not None:
            previous.stop()

        sizes = self.main_splitter.sizes()
        if len(sizes) < 3:
            setattr(self, visible_attr, show)
            panel.setVisible(show)
            self._sync_panel_motion_state()
            return

        setattr(self, visible_attr, show)
        panel.setMinimumWidth(0)
        panel.setMaximumWidth(maximum)

        if show:
            panel.show()
            target = max(minimum, min(int(getattr(self, restore_width_attr)), maximum))
            start = max(0, sizes[index])
            if start == 0:
                staged = list(sizes)
                staged[index] = 1
                staged[1] = max(220, staged[1] - 1)
                self.main_splitter.setSizes(staged)
                sizes = self.main_splitter.sizes()
                start = max(0, sizes[index])
        else:
            start = max(panel.width(), sizes[index])
            setattr(self, restore_width_attr, max(minimum, min(start, maximum)))
            target = 0

        start_sizes = self.main_splitter.sizes()
        center_start = start_sizes[1]
        start_width = max(0, start_sizes[index]) if show else start

        animation = QVariantAnimation(self)
        animation.setStartValue(0.0)
        animation.setEndValue(1.0)
        animation.setDuration(self.PANEL_DURATION_MS)
        animation.setEasingCurve(QEasingCurve.Type.OutCubic)

        def update(progress: Any) -> None:
            fraction = float(progress)
            width = int(round(start_width + (target - start_width) * fraction))
            updated = list(start_sizes)
            updated[index] = max(0, width)
            updated[1] = max(220, int(round(center_start + start_width - width)))
            self.main_splitter.setSizes(updated)

        def finish() -> None:
            if not show:
                panel.hide()
            panel.setMinimumWidth(minimum)
            panel.setMaximumWidth(maximum)
            self._sync_panel_motion_state()

        animation.valueChanged.connect(update)
        animation.finished.connect(finish)
        self._remember_animation(f"panel:{key}", animation)
        self._sync_panel_motion_state()

    def _sync_panel_motion_state(self) -> None:
        self.sidebar_toggle_button.setProperty("active", self._sidebar_visible)
        self.runtime_toggle_button.setProperty("active", self._runtime_visible)
        v2._repolish(self.sidebar_toggle_button)
        v2._repolish(self.runtime_toggle_button)
        self._sync_panel_controls()

    def _animate_selected_thread(self, current: Any, _previous: Any) -> None:
        if not self._motion_enabled or current is None:
            return
        widget = self.thread_list.itemWidget(current)
        if widget is not None:
            self._pulse_widget(widget, "thread-selection", start=0.72, duration=145)

    def _animate_activity_tab(self, index: int) -> None:
        if not self._motion_enabled or index < 0:
            return
        page = self.activity_tabs.widget(index)
        if page is not None:
            self._fade_in_widget(page, f"runtime-tab:{index}", start=0.42, duration=self.TAB_DURATION_MS)

    def _set_status(self, status: str) -> None:
        previous = self.status_label.property("state") if hasattr(self, "status_label") else None
        super()._set_status(status)
        current = self.status_label.property("state")
        if current != previous:
            self._pulse_widget(self.status_label, "status", start=0.56, duration=135)
            if self.composer_state_label.isVisible():
                self._pulse_widget(self.composer_state_label, "composer-state", start=0.62, duration=130)

    def _update_sandbox_status(self, processes: list[dict[str, Any]]) -> None:
        previous = self.sandbox_label.text()
        super()._update_sandbox_status(processes)
        if self.sandbox_label.text() != previous:
            self._pulse_widget(self.sandbox_label, "sandbox", start=0.58, duration=145)

    def _apply_pending_approval(self, approval: Any) -> None:
        was_visible = self.approval_frame.isVisible()
        super()._apply_pending_approval(approval)
        if self.approval_frame.isVisible() and not was_visible:
            self._reveal_approval_card()

    def _reveal_approval_card(self) -> None:
        if not self._motion_enabled:
            return
        natural_height = max(88, self.approval_frame.sizeHint().height())
        self.approval_frame.setMaximumHeight(0)
        self._fade_in_widget(self.approval_frame, "approval", start=0.16, duration=185)

        animation = QVariantAnimation(self)
        animation.setStartValue(0)
        animation.setEndValue(natural_height)
        animation.setDuration(185)
        animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        animation.valueChanged.connect(
            lambda value: self.approval_frame.setMaximumHeight(int(value))
        )
        animation.finished.connect(lambda: self.approval_frame.setMaximumHeight(16777215))
        self._remember_animation("approval-height", animation)

    def _render_transcript(self) -> None:
        was_empty_visible = self.empty_state.isVisible() if hasattr(self, "empty_state") else False
        was_transcript_visible = self.transcript.isVisible() if hasattr(self, "transcript") else False
        card_count = (
            len(self._durable_messages)
            + int(bool(self._optimistic_user))
            + len(self._live_assistant)
        )
        previous_count = self._last_transcript_card_count

        super()._render_transcript()
        self._last_transcript_card_count = card_count

        if self.empty_state.isVisible() and not was_empty_visible:
            self._fade_in_widget(self.empty_state, "empty-state", start=0.12, duration=230)
        if self.transcript.isVisible() and not was_transcript_visible:
            self._fade_in_widget(self.transcript, "transcript", start=0.20, duration=175)
        elif self.transcript.isVisible() and card_count > previous_count:
            self._pulse_widget(self.transcript, "new-message", start=0.91, duration=105)

    def showEvent(self, event: Any) -> None:  # noqa: N802 - Qt override
        super().showEvent(event)
        if self._first_show_motion_done or not self._motion_enabled:
            return
        self._first_show_motion_done = True
        QTimer.singleShot(35, lambda: self._fade_in_widget(self.empty_state, "first-empty", start=0.10, duration=260))
        QTimer.singleShot(90, lambda: self._pulse_widget(self.connection_dot, "connection", start=0.35, duration=230))
