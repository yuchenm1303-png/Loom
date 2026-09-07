from __future__ import annotations

from typing import Any

from PySide6.QtCore import QEvent, QObject, QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QGraphicsDropShadowEffect, QLineEdit, QPushButton, QWidget

from app import desktop_ui_v9 as v9


DesktopEventBridge = v9.DesktopEventBridge
ComposerTextEdit = v9.ComposerTextEdit
ThreadListItemWidget = v9.ThreadListItemWidget


class LoomDesktopWindow(v9.LoomDesktopWindow):
    """Desktop v10: one restrained interaction language across the native app.

    Motion rules:
    - micro feedback is fast and local (press/hover/focus)
    - navigation/content changes settle slightly slower than controls
    - no bounce/elastic easing and no decorative looping animation
    - reduced-motion keeps every state change immediate
    """

    MOTION_MICRO_MS = 80
    MOTION_FAST_MS = 120
    MOTION_BASE_MS = 160
    MOTION_CONTENT_MS = 190
    MOTION_REVEAL_MS = 225
    MOTION_PANEL_MS = 230

    # v5 consumes these constants from self, so its stable animation primitives
    # automatically inherit the same timing scale.
    PANEL_DURATION_MS = MOTION_PANEL_MS
    COMPOSER_DURATION_MS = MOTION_FAST_MS
    TAB_DURATION_MS = MOTION_BASE_MS
    PULSE_DURATION_MS = MOTION_BASE_MS

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self._v10_first_show_done = False
        self._last_activity_motion_count = 0
        self._thread_list_transition_pending = False
        self._focus_glows: dict[QWidget, QGraphicsDropShadowEffect] = {}
        super().__init__(*args, **kwargs)

    def _build_ui(self) -> None:
        super()._build_ui()

        # Keep focus, selected navigation and primary actions in the same muted
        # violet family instead of mixing unrelated glows.
        if hasattr(self, "_composer_focus_effect"):
            self._composer_focus_effect.setColor(QColor(108, 99, 226, 86))

        self._install_v10_interactions()

    def _install_button_motion(self) -> None:
        """Extend v5 button feedback to controls created by later UI layers."""
        super()._install_button_motion()
        for name in (
            "archive_view_button",
            "thread_actions_button",
            "refresh_button",
        ):
            button = getattr(self, name, None)
            if not isinstance(button, QPushButton) or button in self._button_glows:
                continue
            effect = QGraphicsDropShadowEffect(button)
            effect.setOffset(0, 1)
            effect.setColor(QColor(92, 86, 155, 72))
            effect.setBlurRadius(0)
            button.setGraphicsEffect(effect)
            button.installEventFilter(self)
            self._button_glows[button] = effect

    def _install_v10_interactions(self) -> None:
        # v5 installs button motion before v7 creates the conversation-library
        # controls. Calling it again here only picks up those late controls.
        self._install_button_motion()

        if isinstance(getattr(self, "thread_search", None), QLineEdit):
            effect = QGraphicsDropShadowEffect(self.thread_search)
            effect.setOffset(0, 0)
            effect.setColor(QColor(94, 88, 190, 78))
            effect.setBlurRadius(0)
            self.thread_search.setGraphicsEffect(effect)
            self.thread_search.installEventFilter(self)
            self._focus_glows[self.thread_search] = effect

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        effect = self._focus_glows.get(watched) if isinstance(watched, QWidget) else None
        if effect is not None:
            if event.type() == QEvent.Type.FocusIn:
                self._animate_shadow(
                    effect,
                    f"focus:{id(watched)}",
                    17.0,
                    duration=self.MOTION_BASE_MS,
                )
            elif event.type() == QEvent.Type.FocusOut:
                self._animate_shadow(
                    effect,
                    f"focus:{id(watched)}",
                    0.0,
                    duration=self.MOTION_BASE_MS,
                )
        return super().eventFilter(watched, event)

    def _animate_selected_thread(self, current: Any, _previous: Any) -> None:
        if not self._motion_enabled or current is None:
            return
        widget = self.thread_list.itemWidget(current)
        if widget is not None:
            self._pulse_widget(
                widget,
                "thread-selection",
                start=0.80,
                duration=self.MOTION_BASE_MS,
            )
        self._pulse_widget(
            self.thread_title_label,
            "thread-title-selection",
            start=0.72,
            duration=self.MOTION_BASE_MS,
        )

    def _animate_activity_tab(self, index: int) -> None:
        if not self._motion_enabled or index < 0:
            return
        page = self.activity_tabs.widget(index)
        if page is not None:
            self._fade_in_widget(
                page,
                f"runtime-tab:{index}",
                start=0.55,
                duration=self.MOTION_CONTENT_MS,
            )

    def _toggle_archive_view(self, checked: bool) -> None:
        if self._motion_enabled and self.thread_list.isVisible():
            self._thread_list_transition_pending = True
            self._pulse_widget(
                self.thread_list,
                "thread-library-view-out",
                start=0.72,
                duration=self.MOTION_FAST_MS,
            )
        super()._toggle_archive_view(checked)

    def _apply_thread_list(self, payload: dict[str, Any]) -> None:
        previous_count = self.thread_list.count() if hasattr(self, "thread_list") else 0
        super()._apply_thread_list(payload)
        current_count = self.thread_list.count()

        if not self._motion_enabled or not self.thread_list.isVisible():
            self._thread_list_transition_pending = False
            return

        if self._thread_list_transition_pending or current_count != previous_count:
            self._thread_list_transition_pending = False
            self._fade_in_widget(
                self.thread_list,
                "thread-library-view-in",
                start=0.58,
                duration=self.MOTION_CONTENT_MS,
            )
        else:
            self._pulse_widget(
                self.thread_section_label,
                "thread-count",
                start=0.72,
                duration=self.MOTION_FAST_MS,
            )

    def _apply_thread_filter(self, value: str | None = None) -> None:
        previous = self.thread_section_label.text() if hasattr(self, "thread_section_label") else ""
        super()._apply_thread_filter(value)
        if (
            self._motion_enabled
            and hasattr(self, "thread_section_label")
            and self.thread_section_label.text() != previous
        ):
            self._pulse_widget(
                self.thread_section_label,
                "thread-filter-count",
                start=0.78,
                duration=self.MOTION_FAST_MS,
            )

    def _apply_snapshot(self, snapshot: dict[str, Any]) -> None:
        previous_thread = self.current_thread_id
        super()._apply_snapshot(snapshot)
        changed_thread = bool(previous_thread and self.current_thread_id != previous_thread)
        if not self._motion_enabled or not changed_thread:
            return

        self._pulse_widget(
            self.thread_title_label,
            "snapshot-title",
            start=0.58,
            duration=self.MOTION_CONTENT_MS,
        )
        self._pulse_widget(
            self.workspace_label,
            "snapshot-workspace",
            start=0.68,
            duration=self.MOTION_BASE_MS,
        )
        content = self.transcript if self.transcript.isVisible() else self.empty_state
        self._fade_in_widget(
            content,
            "snapshot-content",
            start=0.64,
            duration=self.MOTION_REVEAL_MS,
        )
        if self.activity_view.isVisible():
            self._fade_in_widget(
                self.activity_view,
                "snapshot-runtime",
                start=0.72,
                duration=self.MOTION_CONTENT_MS,
            )

    def send_prompt(self) -> None:
        text = self.composer.toPlainText().strip()
        if not text or not self.current_thread_id:
            return
        super().send_prompt()
        if not self._motion_enabled:
            return
        self._pulse_widget(
            self.composer_state_label,
            "send-state",
            start=0.58,
            duration=self.MOTION_BASE_MS,
        )
        self._pulse_widget(
            self.transcript,
            "send-content",
            start=0.90,
            duration=self.MOTION_FAST_MS,
        )

    def _render_activity(self) -> None:
        count = len(self._activity_tail)
        previous = self._last_activity_motion_count
        super()._render_activity()
        self._last_activity_motion_count = count
        if self._motion_enabled and count > previous and self.activity_view.isVisible():
            self._pulse_widget(
                self.activity_view,
                "runtime-event",
                start=0.94,
                duration=self.MOTION_FAST_MS,
            )

    def _on_rpc_result(self, tag: str, payload: Any) -> None:
        super()._on_rpc_result(tag, payload)
        if not self._motion_enabled:
            return
        if tag.startswith("thread-rename:"):
            self._pulse_widget(
                self.thread_title_label,
                "rename-success",
                start=0.62,
                duration=self.MOTION_CONTENT_MS,
            )
        elif tag.startswith(("thread-archive:", "thread-delete:")):
            self._thread_list_transition_pending = True

    def _reveal_approval_card(self) -> None:
        if not self._motion_enabled:
            return super()._reveal_approval_card()

        from PySide6.QtCore import QEasingCurve, QVariantAnimation

        natural_height = max(88, self.approval_frame.sizeHint().height())
        self.approval_frame.setMaximumHeight(0)
        self._fade_in_widget(
            self.approval_frame,
            "approval",
            start=0.28,
            duration=self.MOTION_REVEAL_MS,
        )

        animation = QVariantAnimation(self)
        animation.setStartValue(0)
        animation.setEndValue(natural_height)
        animation.setDuration(self.MOTION_REVEAL_MS)
        animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        animation.valueChanged.connect(
            lambda value: self.approval_frame.setMaximumHeight(int(value))
        )
        animation.finished.connect(lambda: self.approval_frame.setMaximumHeight(16777215))
        self._remember_animation("approval-height", animation)

    def showEvent(self, event: Any) -> None:  # noqa: N802
        super().showEvent(event)
        if self._v10_first_show_done or not self._motion_enabled:
            return
        self._v10_first_show_done = True

        # A short three-step entrance teaches the three-pane hierarchy without
        # making startup feel like a splash screen.
        QTimer.singleShot(
            30,
            lambda: self._fade_in_widget(
                self.sidebar_panel,
                "startup-sidebar",
                start=0.72,
                duration=self.MOTION_REVEAL_MS,
            ),
        )
        QTimer.singleShot(65, self._reveal_workspace_header)
        QTimer.singleShot(
            95,
            lambda: self._fade_in_widget(
                self.activity_panel,
                "startup-runtime",
                start=0.76,
                duration=self.MOTION_REVEAL_MS,
            ),
        )

    def _reveal_workspace_header(self) -> None:
        header = self.findChild(QWidget, "workspaceHeader")
        if header is not None:
            self._fade_in_widget(
                header,
                "startup-header",
                start=0.62,
                duration=self.MOTION_REVEAL_MS,
            )


__all__ = [
    "ComposerTextEdit",
    "DesktopEventBridge",
    "LoomDesktopWindow",
    "ThreadListItemWidget",
]
