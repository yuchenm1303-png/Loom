"""Motion for Loom's desktop side panels and Runtime inspector.

The goal is deliberately restrained: panels glide rather than pop, Runtime
navigation carries one moving indicator, newly-arrived events settle into the
feed, and only the latest live event pulses.  Historical rows stay still so a
busy agent never turns the sidebar into a wall of motion.
"""

from __future__ import annotations

from typing import Any

from shiboken6 import isValid

from PySide6.QtCore import (
    QEasingCurve,
    QEvent,
    QObject,
    QParallelAnimationGroup,
    QPoint,
    QPropertyAnimation,
    QRect,
    QTimer,
    Qt,
)
from PySide6.QtWidgets import QFrame, QGraphicsOpacityEffect, QWidget

from app.desktop import theme


_WIDGETS_INSTALLED = False
_WINDOW_INSTALLED = False

_PANEL_DURATION_MS = 220
_TAB_INDICATOR_MS = 170
_TAB_FADE_MS = 125
_ROW_REVEAL_MS = 175
_LIVE_PULSE_MS = 920

_LIVE_EVENT_KINDS = {
    "turn_started",
    "model_requested",
    "tool_requested",
    "tool_started",
    "process_started",
    "tool_approval_required",
}


def _repolish(widget: QWidget) -> None:
    style = widget.style()
    style.unpolish(widget)
    style.polish(widget)
    widget.update()


def _reveal_row(row: QWidget) -> None:
    """Bring one new Runtime row in with a quiet fade + small upward settle.

    The first motion pass animated ``maximumHeight`` from 0 to natural, which
    forced the activity timeline to re-layout and re-position every row
    underneath the new one on every event arrival. That cascading layout is
    what makes the whole column appear to "bounce" when a new event arrives.

    This pass leaves the layout alone: the row is already at its final size
    and position from the first paint of ``render_events``. We only cross-fade
    opacity and slide the row 14 px from above to its target, so visually the
    row "lands" instead of "expanding". No size hint changes, no relayout.
    """
    if not theme.motion_enabled():
        return

    effect = QGraphicsOpacityEffect(row)
    row.setGraphicsEffect(effect)
    effect.setOpacity(0.0)

    def start() -> None:
        origin = row.pos()
        start_pos = QPoint(origin.x(), origin.y() - 14)
        row.move(start_pos)

        group = QParallelAnimationGroup(row)

        opacity = QPropertyAnimation(effect, b"opacity", group)
        opacity.setDuration(_ROW_REVEAL_MS)
        opacity.setStartValue(0.0)
        opacity.setEndValue(1.0)
        opacity.setEasingCurve(QEasingCurve.Type.OutCubic)
        group.addAnimation(opacity)

        slide = QPropertyAnimation(row, b"pos", group)
        slide.setDuration(_ROW_REVEAL_MS)
        slide.setStartValue(start_pos)
        slide.setEndValue(origin)
        slide.setEasingCurve(QEasingCurve.Type.OutCubic)
        group.addAnimation(slide)

        def finish() -> None:
            if row.graphicsEffect() is effect:
                row.setGraphicsEffect(None)
            row._loom_row_reveal = None  # type: ignore[attr-defined]

        group.finished.connect(finish)
        row._loom_row_reveal = group  # type: ignore[attr-defined]
        group.start()

    # Defer one event loop so the parent's layout has a chance to assign
    # the row its final geometry. Without this, ``row.pos()`` may still
    # report (0, 0) and the slide has nowhere to settle.
    QTimer.singleShot(0, start)


def _pulse_latest_icon(row: QWidget) -> None:
    """Pulse only the icon of the currently-live event, never historical rows."""
    if not theme.motion_enabled():
        return

    # Import lazily so this module can be installed while widgets is still being
    # assembled by app.desktop.__init__.
    from app.desktop import widgets

    icon = row.findChild(widgets.VectorIcon)
    if icon is None:
        return

    effect = QGraphicsOpacityEffect(icon)
    icon.setGraphicsEffect(effect)
    pulse = QPropertyAnimation(effect, b"opacity", icon)
    pulse.setDuration(_LIVE_PULSE_MS)
    pulse.setStartValue(0.46)
    pulse.setKeyValueAt(0.5, 1.0)
    pulse.setEndValue(0.46)
    pulse.setLoopCount(-1)
    pulse.setEasingCurve(QEasingCurve.Type.InOutSine)
    icon._loom_live_pulse = pulse  # type: ignore[attr-defined]
    pulse.start()


def install_widgets() -> None:
    """Add arrival/live motion to the Runtime event feed."""
    global _WIDGETS_INSTALLED
    if _WIDGETS_INSTALLED:
        return

    from app.desktop import widgets

    original_render_events = widgets.ActivityTimelineView.render_events

    def render_events(self: Any, events: list[tuple[str, str, str]]) -> None:
        previous = list(getattr(self, "_loom_motion_events", []))
        settled = bool(getattr(self, "_loom_motion_settled", False))
        append_only = (
            len(events) > len(previous)
            and events[: len(previous)] == previous
        )

        original_render_events(self, events)
        self._loom_motion_events = list(events)
        self._loom_motion_settled = True

        # Rehydrating an existing thread should remain instant.  Motion is only
        # for events that arrive after the panel is already settled.
        if not settled or not append_only or not events or not theme.motion_enabled():
            return

        item = self._layout.itemAt(len(events) - 1)
        row = item.widget() if item is not None else None
        if row is None:
            return

        _reveal_row(row)
        if events[-1][1] in _LIVE_EVENT_KINDS:
            _pulse_latest_icon(row)

        # The row grows after the original render's one-shot tail scroll.
        QTimer.singleShot(
            _ROW_REVEAL_MS + 20,
            lambda view=self: view.verticalScrollBar().setValue(
                view.verticalScrollBar().maximum()
            ),
        )

    widgets.ActivityTimelineView.render_events = render_events
    _WIDGETS_INSTALLED = True


class SidebarMotionController(QObject):
    """Own panel reveal and Runtime-tab motion for one desktop window."""

    def __init__(self, window: Any) -> None:
        super().__init__(window)
        self.window = window
        self._panel_animations: dict[str, QParallelAnimationGroup] = {}
        self._panel_widths: dict[str, int] = {
            "sidebar": max(288, window.sidebar_panel.width()),
            "runtime": max(372, window.activity_panel.width()),
        }
        self._panel_constraints: dict[str, tuple[int, int]] = {
            "sidebar": (
                window.sidebar_panel.minimumWidth(),
                window.sidebar_panel.maximumWidth(),
            ),
            "runtime": (
                window.activity_panel.minimumWidth(),
                window.activity_panel.maximumWidth(),
            ),
        }

        self._tab_bar = window.activity_tabs.tabBar()
        self._tab_bar.installEventFilter(self)
        # The moving indicator replaces the instantaneous stylesheet underline.
        self._tab_bar.setStyleSheet(
            "QTabBar::tab:selected { border-bottom: 2px solid transparent; }"
        )
        self._indicator = QFrame(self._tab_bar)
        self._indicator.setObjectName("runtimeTabIndicator")
        self._indicator.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._indicator.setFixedHeight(2)
        self._indicator.setStyleSheet(
            "QFrame#runtimeTabIndicator {"
            "background:#818cf8; border:none; border-radius:1px;"
            "}"
        )
        self._indicator.raise_()
        self._indicator_animation: QPropertyAnimation | None = None
        self._page_fade: QPropertyAnimation | None = None
        self._fading_page: QWidget | None = None

        window.activity_tabs.currentChanged.connect(self._on_tab_changed)
        QTimer.singleShot(0, self._snap_indicator)

        for button, active in (
            (window.sidebar_toggle_button, window._sidebar_visible),
            (window.runtime_toggle_button, window._runtime_visible),
        ):
            button.setProperty("active", bool(active))
            _repolish(button)

    # ---- panel show / hide -------------------------------------------

    def set_panel_visible(self, key: str, panel: QWidget, visible: bool) -> None:
        original_min, original_max = self._panel_constraints[key]

        running = self._panel_animations.pop(key, None)
        if running is not None:
            running.stop()

        if not theme.motion_enabled():
            panel.setVisible(visible)
            panel.setMinimumWidth(original_min)
            panel.setMaximumWidth(original_max)
            panel.setGraphicsEffect(None)
            return

        was_visible = panel.isVisible()
        current_width = max(0, panel.width() if was_visible else 0)
        if not visible and current_width > 0:
            self._panel_widths[key] = current_width

        target_width = min(
            max(original_min, self._panel_widths[key]),
            original_max,
        )

        panel.setGraphicsEffect(None)
        if visible and not was_visible:
            panel.setMinimumWidth(0)
            panel.setMaximumWidth(0)
            panel.show()
            current_width = 0
        else:
            panel.setMinimumWidth(0)
            panel.setMaximumWidth(max(0, current_width))

        effect = QGraphicsOpacityEffect(panel)
        panel.setGraphicsEffect(effect)
        effect.setOpacity(0.0 if visible and current_width == 0 else 1.0)

        group = QParallelAnimationGroup(panel)
        width = QPropertyAnimation(panel, b"maximumWidth", group)
        width.setDuration(_PANEL_DURATION_MS)
        width.setStartValue(current_width)
        width.setEndValue(target_width if visible else 0)
        width.setEasingCurve(QEasingCurve.Type.OutCubic)
        group.addAnimation(width)

        opacity = QPropertyAnimation(effect, b"opacity", group)
        opacity.setDuration(_PANEL_DURATION_MS - 40)
        opacity.setStartValue(effect.opacity())
        opacity.setEndValue(1.0 if visible else 0.0)
        opacity.setEasingCurve(QEasingCurve.Type.OutCubic)
        group.addAnimation(opacity)

        def finish() -> None:
            if not visible:
                panel.hide()
            panel.setMinimumWidth(original_min)
            panel.setMaximumWidth(original_max)
            if panel.graphicsEffect() is effect:
                panel.setGraphicsEffect(None)
            self._panel_animations.pop(key, None)

        group.finished.connect(finish)
        self._panel_animations[key] = group
        group.start()

    # ---- Runtime tabs -------------------------------------------------

    def _indicator_rect(self, index: int) -> QRect:
        if not isValid(self._tab_bar) or index < 0 or index >= self._tab_bar.count():
            return QRect()
        tab = self._tab_bar.tabRect(index)
        inset = min(12, max(7, tab.width() // 8))
        return QRect(
            tab.left() + inset,
            max(0, self._tab_bar.height() - 2),
            max(14, tab.width() - inset * 2),
            2,
        )

    def _snap_indicator(self) -> None:
        rect = self._indicator_rect(self.window.activity_tabs.currentIndex())
        if rect.isValid():
            self._indicator.setGeometry(rect)
            self._indicator.show()
            self._indicator.raise_()

    def _on_tab_changed(self, index: int) -> None:
        target = self._indicator_rect(index)
        if target.isValid():
            if self._indicator_animation is not None:
                self._indicator_animation.stop()
                self._indicator_animation.deleteLater()
            if not theme.motion_enabled() or not self._indicator.geometry().isValid():
                self._indicator.setGeometry(target)
            else:
                animation = QPropertyAnimation(self._indicator, b"geometry", self)
                animation.setDuration(_TAB_INDICATOR_MS)
                animation.setStartValue(self._indicator.geometry())
                animation.setEndValue(target)
                animation.setEasingCurve(QEasingCurve.Type.OutCubic)
                self._indicator_animation = animation
                animation.finished.connect(lambda: setattr(self, "_indicator_animation", None))
                animation.finished.connect(animation.deleteLater)
                animation.start()

        if self._page_fade is not None:
            self._page_fade.stop()
            self._page_fade.deleteLater()
            self._page_fade = None
        if self._fading_page is not None:
            self._fading_page.setGraphicsEffect(None)
            self._fading_page = None
        page = self.window.activity_tabs.widget(index)
        if page is None or not theme.motion_enabled():
            return
        self._fading_page = page
        page.setGraphicsEffect(None)
        effect = QGraphicsOpacityEffect(page)
        page.setGraphicsEffect(effect)
        fade = QPropertyAnimation(effect, b"opacity", self)
        fade.setDuration(_TAB_FADE_MS)
        fade.setStartValue(0.38)
        fade.setEndValue(1.0)
        fade.setEasingCurve(QEasingCurve.Type.OutCubic)

        def finish_page() -> None:
            if page.graphicsEffect() is effect:
                page.setGraphicsEffect(None)
            self._page_fade = None
            self._fading_page = None
            fade.deleteLater()

        fade.finished.connect(finish_page)
        self._page_fade = fade
        fade.start()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        if watched is getattr(self, "_tab_bar", None) and event.type() in {
            QEvent.Type.Resize,
            QEvent.Type.Show,
            QEvent.Type.StyleChange,
        }:
            QTimer.singleShot(0, self._snap_indicator)
        return super().eventFilter(watched, event)


def install_window(window_cls: type[Any]) -> None:
    """Attach motion to the existing desktop window without owning UI logic."""
    global _WINDOW_INSTALLED
    if _WINDOW_INSTALLED:
        return

    original_build_ui = window_cls._build_ui

    def _build_ui(self: Any) -> None:
        original_build_ui(self)
        self._sidebar_motion = SidebarMotionController(self)

    def toggle_sidebar(self: Any) -> None:
        self._sidebar_visible = not self._sidebar_visible
        controller = getattr(self, "_sidebar_motion", None)
        if controller is None:
            self.sidebar_panel.setVisible(self._sidebar_visible)
        else:
            controller.set_panel_visible("sidebar", self.sidebar_panel, self._sidebar_visible)
        self.sidebar_toggle_button.setProperty("active", self._sidebar_visible)
        _repolish(self.sidebar_toggle_button)

    def toggle_runtime(self: Any) -> None:
        self._runtime_visible = not self._runtime_visible
        controller = getattr(self, "_sidebar_motion", None)
        if controller is None:
            self.activity_panel.setVisible(self._runtime_visible)
        else:
            controller.set_panel_visible("runtime", self.activity_panel, self._runtime_visible)
        self.runtime_toggle_button.setProperty("active", self._runtime_visible)
        _repolish(self.runtime_toggle_button)

    window_cls._build_ui = _build_ui
    window_cls.toggle_sidebar = toggle_sidebar
    window_cls.toggle_runtime = toggle_runtime
    _WINDOW_INSTALLED = True


__all__ = [
    "SidebarMotionController",
    "install_widgets",
    "install_window",
]
