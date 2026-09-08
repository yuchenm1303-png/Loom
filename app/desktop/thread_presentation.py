"""Compact, animated presentation for the desktop conversation library.

The durable thread list behavior remains in ``widgets`` and ``window``. This
module only changes how one conversation row is presented so the sidebar reads
like a mature recent-conversations list instead of a stack of tall cards.

Motion is deliberately restrained:
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
QFrame#threadActiveSurface {
    background: #24262d;
    border: 1px solid #30333b;
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
        self._entry_animation: QPropertyAnimation | None = None
        self._selection_animation: QParallelAnimationGroup | None = None
        self._attention_animation: QPropertyAnimation | None = None

        # Animated selection surface. The QListWidget still supplies immediate
        # hit feedback; this slightly richer layer fades/settles on top of it.
        self.active_surface = QFrame(self)
        self.active_surface.setObjectName("threadActiveSurface")
        self.active_surface.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._surface_effect = QGraphicsOpacityEffect(self.active_surface)
        self._surface_effect.setOpacity(0.0)
        self.active_surface.setGraphicsEffect(self._surface_effect)
        self.active_surface.lower()

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

    def _animate_selection(self, active: bool) -> None:
        if self._selection_animation is not None:
            self._selection_animation.stop()
            self._selection_animation = None

        if not theme.motion_enabled() or not self.isVisible():
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
