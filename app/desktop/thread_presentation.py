"""Compact, animated presentation for the desktop conversation library.

The durable thread list behavior remains in ``widgets`` and ``window``. This
module only changes how one conversation row is presented so the sidebar reads
like a mature recent-conversations list instead of a stack of tall cards.

Motion is deliberately restrained:
- hover eases in/out instead of flashing;
- first appearance fades in quickly;
- selection settles into place with a soft surface + short accent rail;
- live states breathe slowly instead of blinking;
- reduced-motion mode disables all of the above.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

from PySide6.QtCore import (
    QEasingCurve,
    QEvent,
    QObject,
    QParallelAnimationGroup,
    QPropertyAnimation,
    QRect,
    QSize,
    Qt,
)
from PySide6.QtWidgets import (
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QWidget,
)

from app.desktop import format as fmt
from app.desktop import theme
from app.desktop import widgets as base


_ROW_HEIGHT = 40
_ATTENTION_STATES = {
    "running": "running",
    "starting": "running",
    "waiting_approval": "waiting_approval",
    "failed": "failed",
    "cancelled": "failed",
}

_ROW_QSS = """
QWidget#threadItemWidget {
    background: transparent;
    border: 1px solid transparent;
    border-radius: 9px;
}
QFrame#threadHoverSurface {
    background: #171a21;
    border: 1px solid #232832;
    border-radius: 9px;
}
QFrame#threadActiveSurface {
    background: #302b43;
    border: 1px solid #504565;
    border-radius: 9px;
}
QFrame#threadSelectionAccent {
    background: #756ce7;
    border: none;
    border-radius: 1px;
}
QLabel#threadItemTitle {
    background: transparent;
    color: #cbd0d9;
    font-size: 13px;
    font-weight: 520;
}
QLabel#threadItemTitle[active="true"] {
    color: #f4f5f7;
    font-weight: 640;
}
QLabel#threadDot {
    background: transparent;
    color: #7f8795;
    font-size: 8px;
}
QLabel#threadDot[state="running"] { color: #7fb2f5; }
QLabel#threadDot[state="waiting_approval"] { color: #e0b473; }
QLabel#threadDot[state="failed"] { color: #df8e98; }
"""


class _HoverCoordinator(QObject):
    """Animate row hover without stealing clicks from QListWidget."""

    def __init__(self, view: QListWidget) -> None:
        super().__init__(view)
        self.view = view
        self._hovered: QWidget | None = None
        viewport = view.viewport()
        viewport.setMouseTracking(True)
        viewport.installEventFilter(self)

    def _set_hovered(self, widget: QWidget | None) -> None:
        if widget is self._hovered:
            return
        if self._hovered is not None:
            try:
                self._hovered.set_hovered(False)  # type: ignore[attr-defined]
            except RuntimeError:
                pass
        self._hovered = widget
        if widget is not None:
            try:
                widget.set_hovered(True)  # type: ignore[attr-defined]
            except RuntimeError:
                self._hovered = None

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        from shiboken6 import isValid

        if not hasattr(self, "view") or not isValid(self.view):
            return False
        if watched is self.view.viewport():
            if event.type() == QEvent.Type.MouseMove:
                point = event.position().toPoint()  # type: ignore[attr-defined]
                item = self.view.itemAt(point)
                widget = self.view.itemWidget(item) if item is not None else None
                if widget is not None and hasattr(widget, "set_hovered"):
                    self._set_hovered(widget)
                else:
                    self._set_hovered(None)
            elif event.type() in {QEvent.Type.Leave, QEvent.Type.Hide}:
                self._set_hovered(None)
        return False


def _ensure_hover_coordinator(parent: QWidget | None) -> None:
    if not isinstance(parent, QListWidget):
        return
    if getattr(parent, "_loom_thread_hover_motion", None) is not None:
        return
    parent._loom_thread_hover_motion = _HoverCoordinator(parent)  # type: ignore[attr-defined]


class ThreadListItemWidget(QWidget):
    """A single compact conversation row with restrained product-grade motion."""

    # ``window`` rebuilds list widgets during refresh. Tracking identities here
    # prevents the same selected row from replaying its entrance/selection
    # animation every time Runtime activity refreshes the library.
    _seen_thread_ids: ClassVar[set[str]] = set()
    _active_thread_id: ClassVar[str] = ""

    def __init__(
        self,
        record: dict[str, Any],
        parent: QWidget | None = None,
        *,
        active_workspace: str | Path | None = None,
    ) -> None:
        super().__init__(parent)
        del active_workspace  # kept for the stable constructor contract

        self.setObjectName("threadItemWidget")
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setProperty("active", False)
        self.setStyleSheet(_ROW_QSS)

        self._thread_id = fmt.text(record.get("id")).strip()
        self._full_title = fmt.text(record.get("title")).strip() or "New conversation"
        self._active = False
        self._hovered = False
        self._entry_animation: QPropertyAnimation | None = None
        self._hover_animation: QPropertyAnimation | None = None
        self._selection_animation: QParallelAnimationGroup | None = None
        self._attention_animation: QPropertyAnimation | None = None

        # Hover surface lives behind the selected surface. The QListWidget keeps
        # owning pointer/click semantics; this layer only masks its abrupt hover
        # state with a 100ms visual ease.
        self.hover_surface = QFrame(self)
        self.hover_surface.setObjectName("threadHoverSurface")
        self.hover_surface.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._hover_effect = QGraphicsOpacityEffect(self.hover_surface)
        self._hover_effect.setOpacity(0.0)
        self.hover_surface.setGraphicsEffect(self._hover_effect)
        self.hover_surface.lower()

        # Animated selection surface. The list supplies immediate hit feedback;
        # this slightly richer layer fades/settles on top of it.
        self.active_surface = QFrame(self)
        self.active_surface.setObjectName("threadActiveSurface")
        self.active_surface.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._surface_effect = QGraphicsOpacityEffect(self.active_surface)
        self._surface_effect.setOpacity(0.0)
        self.active_surface.setGraphicsEffect(self._surface_effect)

        # Short accent rail: intentionally not full-height, so selection feels
        # precise rather than like a navigation sidebar from a dashboard.
        self.marker = QFrame(self)
        self.marker.setObjectName("threadSelectionAccent")
        self.marker.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._marker_effect = QGraphicsOpacityEffect(self.marker)
        self._marker_effect.setOpacity(0.0)
        self.marker.setGraphicsEffect(self._marker_effect)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(11, 0, 10, 0)
        layout.setSpacing(8)

        self.title_label = QLabel(self._full_title, self)
        self.title_label.setObjectName("threadItemTitle")
        self.title_label.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.title_label, 1, Qt.AlignmentFlag.AlignVCenter)

        state = "" if record.get("archived") else fmt.text(record.get("status"))
        attention = _ATTENTION_STATES.get(state, "")

        self.status_dot = QLabel("●", self)
        self.status_dot.setObjectName("threadDot")
        self.status_dot.setProperty("state", attention)
        self.status_dot.setVisible(bool(attention))
        layout.addWidget(self.status_dot, 0, Qt.AlignmentFlag.AlignVCenter)

        # Keep the metadata label as part of the public widget surface for tests
        # and callers, but move its information into the tooltip so rows stay on
        # one visual line.
        when = fmt.relative_time(record.get("updatedAt"))
        status_text = fmt.human_status(state) if attention else ""
        self.meta_label = QLabel(" · ".join(part for part in (when, status_text) if part), self)
        self.meta_label.setObjectName("threadItemMeta")
        self.meta_label.setProperty("state", attention)
        self.meta_label.setTextFormat(Qt.TextFormat.PlainText)
        self.meta_label.hide()

        detail = " · ".join(
            part
            for part in (
                when,
                fmt.short_path(record.get("workspace")) if record.get("workspace") else "",
                fmt.human_status(record.get("status")),
                f"{fmt.format_tokens((record.get('usage') or {}).get('totalTokens'))} tokens"
                if isinstance(record.get("usage"), dict)
                and (record.get("usage") or {}).get("totalTokens")
                else "",
            )
            if part
        )
        self.setToolTip(f"{self._full_title}\n{detail}" if detail else self._full_title)

        _ensure_hover_coordinator(parent)
        self._start_attention_motion(attention)

    # ------------------------------------------------------------------
    # motion
    # ------------------------------------------------------------------

    def _surface_rect(self) -> QRect:
        return self.rect().adjusted(1, 1, -1, -1)

    def _accent_rect(self, *, expanded: bool) -> QRect:
        height = 18 if expanded else 6
        return QRect(4, max(1, (self.height() - height) // 2), 2, height)

    def _set_selection_visual(self, active: bool) -> None:
        self.active_surface.setGeometry(self._surface_rect())
        self.marker.setGeometry(self._accent_rect(expanded=active))
        self._surface_effect.setOpacity(1.0 if active else 0.0)
        self._marker_effect.setOpacity(0.92 if active else 0.0)

    def _animate_hover(self, hovered: bool) -> None:
        if self._hover_animation is not None:
            self._hover_animation.stop()
            self._hover_animation = None

        target = 0.0 if self._active else (0.82 if hovered else 0.0)
        if not theme.motion_enabled() or not self.isVisible():
            self._hover_effect.setOpacity(target)
            return

        animation = QPropertyAnimation(self._hover_effect, b"opacity", self)
        animation.setDuration(105 if hovered else 85)
        animation.setStartValue(self._hover_effect.opacity())
        animation.setEndValue(target)
        animation.setEasingCurve(QEasingCurve.Type.OutCubic)

        def finish() -> None:
            self._hover_effect.setOpacity(target)
            self._hover_animation = None

        animation.finished.connect(finish)
        self._hover_animation = animation
        animation.start()

    def _animate_selection(self, active: bool) -> None:
        if self._selection_animation is not None:
            self._selection_animation.stop()
            self._selection_animation = None

        if not theme.motion_enabled() or not self.isVisible() or self.width() < 40:
            self._set_selection_visual(active)
            return

        group = QParallelAnimationGroup(self)
        duration = 170 if active else 120
        easing = QEasingCurve.Type.OutCubic

        surface_target = self._surface_rect()
        surface_inset = surface_target.adjusted(3, 2, -3, -2)
        surface_geometry = QPropertyAnimation(self.active_surface, b"geometry", group)
        surface_geometry.setDuration(duration)
        surface_geometry.setStartValue(
            self.active_surface.geometry()
            if self.active_surface.geometry().isValid()
            else (surface_inset if active else surface_target)
        )
        surface_geometry.setEndValue(surface_target if active else surface_inset)
        surface_geometry.setEasingCurve(easing)
        group.addAnimation(surface_geometry)

        surface_opacity = QPropertyAnimation(self._surface_effect, b"opacity", group)
        surface_opacity.setDuration(duration)
        surface_opacity.setStartValue(self._surface_effect.opacity())
        surface_opacity.setEndValue(1.0 if active else 0.0)
        surface_opacity.setEasingCurve(easing)
        group.addAnimation(surface_opacity)

        accent_geometry = QPropertyAnimation(self.marker, b"geometry", group)
        accent_geometry.setDuration(duration)
        accent_geometry.setStartValue(self.marker.geometry())
        accent_geometry.setEndValue(self._accent_rect(expanded=active))
        accent_geometry.setEasingCurve(easing)
        group.addAnimation(accent_geometry)

        accent_opacity = QPropertyAnimation(self._marker_effect, b"opacity", group)
        accent_opacity.setDuration(135 if active else 95)
        accent_opacity.setStartValue(self._marker_effect.opacity())
        accent_opacity.setEndValue(0.92 if active else 0.0)
        accent_opacity.setEasingCurve(QEasingCurve.Type.OutQuad)
        group.addAnimation(accent_opacity)

        def finish() -> None:
            self._set_selection_visual(active)
            self._selection_animation = None

        group.finished.connect(finish)
        self._selection_animation = group
        group.start()

    def _start_attention_motion(self, attention: str) -> None:
        if attention not in {"running", "waiting_approval"} or not theme.motion_enabled():
            return

        effect = QGraphicsOpacityEffect(self.status_dot)
        self.status_dot.setGraphicsEffect(effect)
        effect.setOpacity(1.0)

        animation = QPropertyAnimation(effect, b"opacity", self.status_dot)
        animation.setDuration(1450 if attention == "running" else 1850)
        animation.setStartValue(0.72)
        animation.setKeyValueAt(0.5, 1.0)
        animation.setEndValue(0.72)
        animation.setLoopCount(-1)
        animation.setEasingCurve(QEasingCurve.Type.InOutSine)
        animation.start()
        self._attention_animation = animation

    def _start_entry_reveal(self) -> None:
        if not self._thread_id or self._thread_id in self._seen_thread_ids:
            return
        self._seen_thread_ids.add(self._thread_id)
        if not theme.motion_enabled():
            return

        effect = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(effect)
        effect.setOpacity(0.72)

        animation = QPropertyAnimation(effect, b"opacity", self)
        animation.setDuration(165)
        animation.setStartValue(0.72)
        animation.setEndValue(1.0)
        animation.setEasingCurve(QEasingCurve.Type.OutCubic)

        def finish() -> None:
            self.setGraphicsEffect(None)
            self._entry_animation = None

        animation.finished.connect(finish)
        self._entry_animation = animation
        animation.start()

    # ------------------------------------------------------------------
    # public row contract
    # ------------------------------------------------------------------

    def set_hovered(self, hovered: bool) -> None:
        hovered = bool(hovered)
        if self._hovered == hovered:
            return
        self._hovered = hovered
        self._animate_hover(hovered)

    def set_active(self, active: bool) -> None:
        """Mark the row whose thread is currently open."""
        active = bool(active)
        if self._active == active:
            return
        self._active = active

        for widget in (self, self.marker, self.title_label):
            if widget.property("active") != active:
                widget.setProperty("active", active)
                base.repolish(widget)

        # Selected rows suppress the hover overlay; when selection leaves a row
        # that the pointer still occupies, hover softly returns.
        self._animate_hover(self._hovered and not active)

        # Only animate a genuine selection change. Recreating the same row during
        # a library refresh should restore the selected state immediately.
        if active:
            should_animate = self._thread_id != type(self)._active_thread_id
            type(self)._active_thread_id = self._thread_id
        else:
            should_animate = self._thread_id == type(self)._active_thread_id
            if should_animate:
                type(self)._active_thread_id = ""

        if should_animate:
            self._animate_selection(active)
        else:
            self._set_selection_visual(active)

    def showEvent(self, event: Any) -> None:  # noqa: N802 - Qt override
        super().showEvent(event)
        self._start_entry_reveal()

    def resizeEvent(self, event: Any) -> None:  # noqa: N802 - Qt override
        super().resizeEvent(event)

        self.hover_surface.setGeometry(self._surface_rect())
        # Layout-driven resizes should not fight an in-flight selection settle.
        if self._selection_animation is None:
            self.active_surface.setGeometry(self._surface_rect())
            self.marker.setGeometry(self._accent_rect(expanded=self._active))

        reserved = 36 if self.status_dot.isVisible() else 22
        available = max(72, self.width() - reserved)
        self.title_label.setText(
            self.title_label.fontMetrics().elidedText(
                self._full_title,
                Qt.TextElideMode.ElideRight,
                available,
            )
        )


def thread_row_size(widget: QWidget) -> QSize:
    """Use a single-line sidebar density instead of the legacy 52px row."""
    del widget
    return QSize(0, _ROW_HEIGHT)


__all__ = ["ThreadListItemWidget", "thread_row_size"]
