"""Compact, polished presentation for the desktop conversation library.

The durable thread list behaviour stays in ``widgets`` and ``window``. This
module owns only the one-line row presentation and its low-cost motion.

The design deliberately avoids card-heavy history rows:
- normal conversations sit directly on the sidebar surface;
- hover adds one faint, borderless surface instead of a full card outline;
- selection uses a restrained raised surface plus a short violet rail;
- attention states use a native painted beacon rather than a font bullet;
- the scrollbar is thin and quiet;
- no animation performs per-frame layout work.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

from PySide6.QtCore import (
    Property,
    QEasingCurve,
    QEvent,
    QObject,
    QParallelAnimationGroup,
    QPointF,
    QPropertyAnimation,
    QRect,
    QRectF,
    QSize,
    Qt,
)
from PySide6.QtGui import QColor, QPainter, QPen
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


_ROW_HEIGHT = 38
_ATTENTION_STATES = {
    "running": "running",
    "starting": "running",
    "waiting_approval": "waiting_approval",
    "failed": "failed",
    "cancelled": "failed",
}

# The QListWidget must not paint a second selection/hover surface behind the
# custom row. The thin scrollbar keeps the library from looking like a legacy
# desktop list when many threads are present.
_LIST_QSS = """
QListWidget#threadList {
    background: transparent;
    border: none;
    outline: none;
    padding: 1px 0 3px 0;
}
QListWidget#threadList::item,
QListWidget#threadList::item:hover,
QListWidget#threadList::item:selected,
QListWidget#threadList::item:selected:hover {
    background: transparent;
    border: none;
    border-radius: 0;
    margin: 0;
    padding: 0;
}
QListWidget#threadList QScrollBar:vertical {
    width: 8px;
    margin: 4px 1px 4px 1px;
    background: transparent;
    border: none;
}
QListWidget#threadList QScrollBar::handle:vertical {
    min-height: 34px;
    margin: 0 1px;
    background: #343843;
    border: none;
    border-radius: 3px;
}
QListWidget#threadList QScrollBar::handle:vertical:hover {
    background: #4a4f5c;
}
QListWidget#threadList QScrollBar::add-line:vertical,
QListWidget#threadList QScrollBar::sub-line:vertical,
QListWidget#threadList QScrollBar::add-page:vertical,
QListWidget#threadList QScrollBar::sub-page:vertical {
    height: 0;
    background: transparent;
    border: none;
}
"""

_ROW_QSS = """
QWidget#threadItemWidget {
    background: transparent;
    border: none;
}
QFrame#threadHoverSurface {
    background: #1a1d24;
    border: none;
    border-radius: 8px;
}
QFrame#threadActiveSurface {
    background: qlineargradient(
        x1:0, y1:0, x2:1, y2:0,
        stop:0 #242632,
        stop:1 #211f2c
    );
    border: 1px solid #343747;
    border-radius: 8px;
}
QFrame#threadSelectionAccent {
    background: #8177ee;
    border: none;
    border-radius: 1px;
}
QLabel#threadItemTitle {
    background: transparent;
    color: #c8cdd6;
    font-size: 13px;
    font-weight: 500;
}
QLabel#threadItemTitle[active="true"] {
    color: #f3f4f7;
    font-weight: 620;
}
QLabel#threadItemMeta {
    background: transparent;
    color: #777f8d;
    font-size: 10px;
}
"""


class StatusBeacon(QLabel):
    """Small native state marker with an optional low-frequency halo pulse.

    It remains a QLabel and keeps the historical ``\u25cf`` text value so any caller
    that treated ``status_dot`` as a label continues to work; paintEvent owns the
    visible geometry so the result is DPI-independent and not font-shaped.
    """

    _COLORS = {
        "running": "#7faef0",
        "waiting_approval": "#ddb06c",
        "failed": "#d98590",
    }

    def __init__(self, state: str, parent: QWidget | None = None) -> None:
        super().__init__("\u25cf", parent)
        self.setObjectName("threadDot")
        self.setProperty("state", state)
        self.setFixedSize(14, 14)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._pulse = 0.0
        self._animation: QPropertyAnimation | None = None
        self._state = state

    def _get_pulse(self) -> float:
        return self._pulse

    def _set_pulse(self, value: float) -> None:
        value = max(0.0, min(1.0, float(value)))
        if abs(value - self._pulse) < 0.001:
            return
        self._pulse = value
        self.update()

    pulse = Property(float, _get_pulse, _set_pulse)

    def start(self) -> None:
        if self._state not in {"running", "waiting_approval"}:
            return
        if not theme.motion_enabled():
            self._set_pulse(0.35)
            return
        animation = QPropertyAnimation(self, b"pulse", self)
        animation.setDuration(1750 if self._state == "running" else 2100)
        animation.setStartValue(0.0)
        animation.setKeyValueAt(0.5, 1.0)
        animation.setEndValue(0.0)
        animation.setLoopCount(-1)
        animation.setEasingCurve(QEasingCurve.Type.InOutSine)
        animation.start()
        self._animation = animation

    def paintEvent(self, _event: Any) -> None:  # noqa: N802 - Qt override
        if not self._state:
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        color = QColor(self._COLORS.get(self._state, "#7d8490"))
        center = QPointF(self.width() / 2.0, self.height() / 2.0)

        if self._state in {"running", "waiting_approval"}:
            halo = QColor(color)
            halo.setAlpha(int(18 + 38 * self._pulse))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(halo)
            radius = 3.8 + 1.2 * self._pulse
            painter.drawEllipse(QRectF(center.x() - radius, center.y() - radius, radius * 2, radius * 2))

        dot = QColor(color)
        dot.setAlpha(236)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(dot)
        painter.drawEllipse(QRectF(center.x() - 2.15, center.y() - 2.15, 4.3, 4.3))


def _install_list_surface(view: QListWidget) -> None:
    if getattr(view, "_loom_thread_surface_polished", False):
        return
    view._loom_thread_surface_polished = True  # type: ignore[attr-defined]
    view.setStyleSheet(view.styleSheet() + "\n" + _LIST_QSS)
    view.setSpacing(1)
    view.setVerticalScrollMode(view.ScrollMode.ScrollPerPixel)


class _HoverCoordinator(QObject):
    """Animate row hover without stealing click semantics from QListWidget."""

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
    _install_list_surface(parent)
    if getattr(parent, "_loom_thread_hover_motion", None) is not None:
        return
    parent._loom_thread_hover_motion = _HoverCoordinator(parent)  # type: ignore[attr-defined]


class ThreadListItemWidget(QWidget):
    """One compact recent-conversation row with calm, low-reflow motion."""

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
        del active_workspace  # stable constructor contract

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

        self.hover_surface = QFrame(self)
        self.hover_surface.setObjectName("threadHoverSurface")
        self.hover_surface.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._hover_effect = QGraphicsOpacityEffect(self.hover_surface)
        self._hover_effect.setOpacity(0.0)
        self.hover_surface.setGraphicsEffect(self._hover_effect)
        self.hover_surface.lower()

        self.active_surface = QFrame(self)
        self.active_surface.setObjectName("threadActiveSurface")
        self.active_surface.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._surface_effect = QGraphicsOpacityEffect(self.active_surface)
        self._surface_effect.setOpacity(0.0)
        self.active_surface.setGraphicsEffect(self._surface_effect)

        self.marker = QFrame(self)
        self.marker.setObjectName("threadSelectionAccent")
        self.marker.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._marker_effect = QGraphicsOpacityEffect(self.marker)
        self._marker_effect.setOpacity(0.0)
        self.marker.setGraphicsEffect(self._marker_effect)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 0, 8, 0)
        layout.setSpacing(7)

        self.title_label = QLabel(self._full_title, self)
        self.title_label.setObjectName("threadItemTitle")
        self.title_label.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.title_label, 1, Qt.AlignmentFlag.AlignVCenter)

        state = "" if record.get("archived") else fmt.text(record.get("status"))
        attention = _ATTENTION_STATES.get(state, "")
        self.status_dot = StatusBeacon(attention, self)
        self.status_dot.setVisible(bool(attention))
        layout.addWidget(self.status_dot, 0, Qt.AlignmentFlag.AlignVCenter)
        if attention:
            self.status_dot.start()

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

    # ------------------------------------------------------------------
    # geometry + motion
    # ------------------------------------------------------------------

    def _surface_rect(self) -> QRect:
        return self.rect().adjusted(1, 1, -1, -1)

    def _accent_rect(self, *, expanded: bool) -> QRect:
        height = 20 if expanded else 5
        return QRect(4, max(1, (self.height() - height) // 2), 2, height)

    def _set_selection_visual(self, active: bool) -> None:
        self.active_surface.setGeometry(self._surface_rect())
        self.marker.setGeometry(self._accent_rect(expanded=active))
        self._surface_effect.setOpacity(1.0 if active else 0.0)
        self._marker_effect.setOpacity(0.96 if active else 0.0)

    def _animate_hover(self, hovered: bool) -> None:
        if self._hover_animation is not None:
            self._hover_animation.stop()
            self._hover_animation = None

        target = 0.0 if self._active else (1.0 if hovered else 0.0)
        if not theme.motion_enabled() or not self.isVisible():
            self._hover_effect.setOpacity(target)
            return

        animation = QPropertyAnimation(self._hover_effect, b"opacity", self)
        animation.setDuration(115 if hovered else 90)
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
        duration = 155 if active else 105

        target = self._surface_rect()
        inset = target.adjusted(3, 2, -3, -2)

        geometry = QPropertyAnimation(self.active_surface, b"geometry", group)
        geometry.setDuration(duration)
        geometry.setStartValue(
            self.active_surface.geometry()
            if self.active_surface.geometry().isValid()
            else (inset if active else target)
        )
        geometry.setEndValue(target if active else inset)
        geometry.setEasingCurve(QEasingCurve.Type.OutCubic)
        group.addAnimation(geometry)

        opacity = QPropertyAnimation(self._surface_effect, b"opacity", group)
        opacity.setDuration(duration)
        opacity.setStartValue(self._surface_effect.opacity())
        opacity.setEndValue(1.0 if active else 0.0)
        opacity.setEasingCurve(QEasingCurve.Type.OutCubic)
        group.addAnimation(opacity)

        rail_geometry = QPropertyAnimation(self.marker, b"geometry", group)
        rail_geometry.setDuration(duration)
        rail_geometry.setStartValue(self.marker.geometry())
        rail_geometry.setEndValue(self._accent_rect(expanded=active))
        rail_geometry.setEasingCurve(QEasingCurve.Type.OutCubic)
        group.addAnimation(rail_geometry)

        rail_opacity = QPropertyAnimation(self._marker_effect, b"opacity", group)
        rail_opacity.setDuration(125 if active else 85)
        rail_opacity.setStartValue(self._marker_effect.opacity())
        rail_opacity.setEndValue(0.96 if active else 0.0)
        rail_opacity.setEasingCurve(QEasingCurve.Type.OutQuad)
        group.addAnimation(rail_opacity)

        def finish() -> None:
            self._set_selection_visual(active)
            self._selection_animation = None

        group.finished.connect(finish)
        self._selection_animation = group
        group.start()

    def _start_entry_reveal(self) -> None:
        if not self._thread_id or self._thread_id in self._seen_thread_ids:
            return
        self._seen_thread_ids.add(self._thread_id)
        if not theme.motion_enabled():
            return

        effect = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(effect)
        effect.setOpacity(0.84)

        animation = QPropertyAnimation(effect, b"opacity", self)
        animation.setDuration(135)
        animation.setStartValue(0.84)
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
        active = bool(active)
        if self._active == active:
            return
        self._active = active

        for widget in (self, self.marker, self.title_label):
            if widget.property("active") != active:
                widget.setProperty("active", active)
                base.repolish(widget)

        self._animate_hover(self._hovered and not active)

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
        if self._selection_animation is None:
            self.active_surface.setGeometry(self._surface_rect())
            self.marker.setGeometry(self._accent_rect(expanded=self._active))

        reserved = 35 if self.status_dot.isVisible() else 20
        available = max(72, self.width() - reserved)
        self.title_label.setText(
            self.title_label.fontMetrics().elidedText(
                self._full_title,
                Qt.TextElideMode.ElideRight,
                available,
            )
        )


def thread_row_size(widget: QWidget) -> QSize:
    """Use a compact single-line recent-conversation density."""
    del widget
    return QSize(0, _ROW_HEIGHT)


__all__ = ["StatusBeacon", "ThreadListItemWidget", "thread_row_size"]
