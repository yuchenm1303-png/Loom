"""Professional vector iconography for the native Runtime inspector.

The desktop client intentionally does not depend on an icon font or an external
SVG bundle.  These icons use one consistent 24-unit line language, render with
Qt at device resolution, and keep native tab icons transparent instead of
rendering a styled QWidget into a pixmap.

This module installs itself over the small icon hooks in ``widgets`` before the
window module imports them.  Keeping the drawing code isolated makes the visual
system easy to refine without mixing it into transcript behaviour.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap

from app.desktop import theme


_TOOL_BLUE = "#7da9f6"
_NEUTRAL = "#8d96a7"


def _tone_color(tone: str) -> QColor:
    return QColor(
        {
            "accent": theme.ACCENT_SOFT,
            "tool": _TOOL_BLUE,
            "good": theme.GOOD,
            "warn": theme.WARN,
            "bad": theme.BAD,
            "muted": _NEUTRAL,
        }.get(tone, _NEUTRAL)
    )


def _frame_colors(tone: str) -> tuple[QColor, QColor]:
    background, border = {
        "accent": ("#151525", "#34315b"),
        "tool": ("#101824", "#263a55"),
        "good": ("#101a17", "#29463b"),
        "warn": ("#1d1810", "#4a3922"),
        "bad": ("#1e1316", "#4b2a31"),
        "muted": ("#11151b", "#272e39"),
    }.get(tone, ("#11151b", "#272e39"))
    return QColor(background), QColor(border)


def _box(rect: QRectF, *, framed: bool) -> QRectF:
    inset = rect.width() * (0.245 if framed else 0.11)
    return rect.adjusted(inset, inset, -inset, -inset)


def _p(box: QRectF, x: float, y: float) -> QPointF:
    return QPointF(
        box.left() + box.width() * x / 24.0,
        box.top() + box.height() * y / 24.0,
    )


def _line(painter: QPainter, box: QRectF, *coords: float) -> None:
    painter.drawLine(_p(box, coords[0], coords[1]), _p(box, coords[2], coords[3]))


def _ellipse(painter: QPainter, box: QRectF, x: float, y: float, w: float, h: float) -> None:
    top_left = _p(box, x, y)
    bottom_right = _p(box, x + w, y + h)
    painter.drawEllipse(QRectF(top_left, bottom_right))


def _rounded_rect(
    painter: QPainter,
    box: QRectF,
    x: float,
    y: float,
    w: float,
    h: float,
    radius: float = 2.0,
) -> None:
    top_left = _p(box, x, y)
    bottom_right = _p(box, x + w, y + h)
    rect = QRectF(top_left, bottom_right)
    r = max(1.0, box.width() * radius / 24.0)
    painter.drawRoundedRect(rect, r, r)


def _draw_activity(painter: QPainter, box: QRectF) -> None:
    # List-tree: a timeline, not a heart-rate monitor.
    _line(painter, box, 6, 5, 6, 19)
    for y in (6, 12, 18):
        _ellipse(painter, box, 4.7, y - 1.3, 2.6, 2.6)
    _line(painter, box, 8.5, 6, 19, 6)
    _line(painter, box, 8.5, 12, 16.5, 12)
    _line(painter, box, 8.5, 18, 19, 18)


def _draw_terminal(painter: QPainter, box: QRectF) -> None:
    _rounded_rect(painter, box, 2, 3, 20, 18, 2.5)
    _line(painter, box, 6, 8, 10, 12)
    _line(painter, box, 10, 12, 6, 16)
    _line(painter, box, 13, 16, 18, 16)


def _draw_diff(painter: QPainter, box: QRectF) -> None:
    path = QPainterPath(_p(box, 6, 2.5))
    path.lineTo(_p(box, 15, 2.5))
    path.lineTo(_p(box, 20, 7.5))
    path.lineTo(_p(box, 20, 21.5))
    path.lineTo(_p(box, 6, 21.5))
    path.closeSubpath()
    painter.drawPath(path)
    _line(painter, box, 15, 2.7, 15, 7.5)
    _line(painter, box, 15, 7.5, 19.7, 7.5)
    _line(painter, box, 8.5, 11, 13.5, 11)
    _line(painter, box, 11, 8.5, 11, 13.5)
    _line(painter, box, 8.5, 17, 14.5, 17)


def _draw_browser(painter: QPainter, box: QRectF) -> None:
    _ellipse(painter, box, 2.5, 2.5, 19, 19)
    _line(painter, box, 3.5, 12, 20.5, 12)
    _ellipse(painter, box, 7.4, 2.5, 9.2, 19)


def _draw_agents(painter: QPainter, box: QRectF) -> None:
    # Workflow topology: one coordinator, three delegated nodes.
    _ellipse(painter, box, 9.4, 9.4, 5.2, 5.2)
    _line(painter, box, 12, 9.2, 12, 5.8)
    _line(painter, box, 12, 14.8, 12, 18.2)
    _line(painter, box, 9.2, 12, 5.8, 12)
    _ellipse(painter, box, 10.3, 2.3, 3.4, 3.4)
    _ellipse(painter, box, 10.3, 18.3, 3.4, 3.4)
    _ellipse(painter, box, 2.3, 10.3, 3.4, 3.4)


def _draw_session(painter: QPainter, box: QRectF) -> None:
    _ellipse(painter, box, 3, 3, 18, 18)
    painter.setBrush(painter.pen().color())
    _ellipse(painter, box, 10.3, 10.3, 3.4, 3.4)
    painter.setBrush(Qt.BrushStyle.NoBrush)


def _draw_turn_start(painter: QPainter, box: QRectF) -> None:
    _ellipse(painter, box, 3, 3, 18, 18)
    path = QPainterPath(_p(box, 10, 8))
    path.lineTo(_p(box, 16, 12))
    path.lineTo(_p(box, 10, 16))
    path.closeSubpath()
    painter.drawPath(path)


def _draw_prompt(painter: QPainter, box: QRectF) -> None:
    path = QPainterPath(_p(box, 5, 5))
    path.lineTo(_p(box, 19, 5))
    path.quadTo(_p(box, 21, 5), _p(box, 21, 7))
    path.lineTo(_p(box, 21, 15))
    path.quadTo(_p(box, 21, 17), _p(box, 19, 17))
    path.lineTo(_p(box, 10, 17))
    path.lineTo(_p(box, 6, 20))
    path.lineTo(_p(box, 6.7, 17))
    path.lineTo(_p(box, 5, 17))
    path.quadTo(_p(box, 3, 17), _p(box, 3, 15))
    path.lineTo(_p(box, 3, 7))
    path.quadTo(_p(box, 3, 5), _p(box, 5, 5))
    painter.drawPath(path)


def _draw_model_request(painter: QPainter, box: QRectF) -> None:
    # Small neural graph; avoids the generic AI sparkle/diamond cliché.
    _line(painter, box, 7, 7, 12, 12)
    _line(painter, box, 17, 7, 12, 12)
    _line(painter, box, 12, 12, 7, 17)
    _line(painter, box, 12, 12, 17, 17)
    for x, y in ((5.2, 5.2), (15.2, 5.2), (10.2, 10.2), (5.2, 15.2), (15.2, 15.2)):
        _ellipse(painter, box, x, y, 3.6, 3.6)


def _draw_model_response(painter: QPainter, box: QRectF) -> None:
    _draw_prompt(painter, box)
    _line(painter, box, 8, 10, 16, 10)
    _line(painter, box, 8, 13.5, 14, 13.5)


def _draw_tool(painter: QPainter, box: QRectF) -> None:
    # Restrained wrench silhouette built entirely from the shared line weight.
    _line(painter, box, 9, 10, 17.3, 18.3)
    _ellipse(painter, box, 15.4, 16.4, 3.8, 3.8)
    path = QPainterPath(_p(box, 9, 10))
    path.cubicTo(_p(box, 5.2, 11), _p(box, 3.1, 7.8), _p(box, 4.5, 4.7))
    path.lineTo(_p(box, 7.2, 7.4))
    path.lineTo(_p(box, 10.2, 4.4))
    path.cubicTo(_p(box, 12.3, 7.3), _p(box, 11.4, 9.2), _p(box, 9, 10))
    painter.drawPath(path)


def _draw_spinner(painter: QPainter, box: QRectF) -> None:
    rect = QRectF(_p(box, 4, 4), _p(box, 20, 20))
    painter.drawArc(rect, 35 * 16, 245 * 16)
    painter.setBrush(painter.pen().color())
    _ellipse(painter, box, 17.2, 4.6, 2.2, 2.2)
    painter.setBrush(Qt.BrushStyle.NoBrush)


def _draw_check(painter: QPainter, box: QRectF) -> None:
    _ellipse(painter, box, 3, 3, 18, 18)
    _line(painter, box, 7.3, 12.1, 10.7, 15.4)
    _line(painter, box, 10.7, 15.4, 17.2, 8.7)


def _draw_check_check(painter: QPainter, box: QRectF) -> None:
    _line(painter, box, 4, 12.4, 8.3, 16.5)
    _line(painter, box, 8.3, 16.5, 14.2, 10.3)
    _line(painter, box, 10.3, 12.4, 13.5, 15.4)
    _line(painter, box, 13.5, 15.4, 20, 8.7)


def _draw_approval(painter: QPainter, box: QRectF) -> None:
    path = QPainterPath(_p(box, 12, 2.7))
    path.lineTo(_p(box, 19, 5.5))
    path.lineTo(_p(box, 18.2, 13.2))
    path.cubicTo(_p(box, 17.7, 17), _p(box, 15.2, 19.5), _p(box, 12, 21.2))
    path.cubicTo(_p(box, 8.8, 19.5), _p(box, 6.3, 17), _p(box, 5.8, 13.2))
    path.lineTo(_p(box, 5, 5.5))
    path.closeSubpath()
    painter.drawPath(path)
    _line(painter, box, 12, 8.2, 12, 13.4)
    painter.drawPoint(_p(box, 12, 16.2))


def _draw_error(painter: QPainter, box: QRectF) -> None:
    _ellipse(painter, box, 3, 3, 18, 18)
    _line(painter, box, 8.5, 8.5, 15.5, 15.5)
    _line(painter, box, 15.5, 8.5, 8.5, 15.5)


def _draw_terminal_done(painter: QPainter, box: QRectF) -> None:
    _rounded_rect(painter, box, 2, 3, 20, 18, 2.5)
    _line(painter, box, 5.2, 8, 8.3, 11)
    _line(painter, box, 8.3, 11, 5.2, 14)
    _line(painter, box, 11, 15, 13.5, 17.2)
    _line(painter, box, 13.5, 17.2, 18.8, 11.7)


def _draw_dot(painter: QPainter, box: QRectF) -> None:
    painter.setBrush(painter.pen().color())
    _ellipse(painter, box, 10, 10, 4, 4)
    painter.setBrush(Qt.BrushStyle.NoBrush)


_DRAWERS = {
    "activity": _draw_activity,
    "model": _draw_activity,
    "terminal": _draw_terminal,
    "diff": _draw_diff,
    "browser": _draw_browser,
    "agents": _draw_agents,
    "session": _draw_session,
    "turn_start": _draw_turn_start,
    "prompt": _draw_prompt,
    "model_request": _draw_model_request,
    "model_response": _draw_model_response,
    "tool": _draw_tool,
    "spinner": _draw_spinner,
    "check": _draw_check,
    "check_check": _draw_check_check,
    "approval": _draw_approval,
    "error": _draw_error,
    "terminal_done": _draw_terminal_done,
    "dot": _draw_dot,
}


def _paint_icon(
    painter: QPainter,
    rect: QRectF,
    name: str,
    tone: str,
    *,
    framed: bool,
) -> None:
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    if framed:
        background, border = _frame_colors(tone)
        painter.setPen(QPen(border, 1.0))
        painter.setBrush(background)
        painter.drawRoundedRect(rect.adjusted(1, 1, -1, -1), 7, 7)

    color = _tone_color(tone)
    width = max(1.35, min(1.8, rect.width() * 0.06))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.setPen(
        QPen(
            color,
            width,
            Qt.PenStyle.SolidLine,
            Qt.PenCapStyle.RoundCap,
            Qt.PenJoinStyle.RoundJoin,
        )
    )
    box = _box(rect, framed=framed)
    _DRAWERS.get(name, _draw_dot)(painter, box)


def _vector_paint_event(self: Any, _event: Any) -> None:
    painter = QPainter(self)
    _paint_icon(painter, QRectF(self.rect()), self.name, self.tone, framed=self.framed)


def _vector_icon(name: str, *, size: int = 18, tone: str = "muted") -> QIcon:
    """Render a truly transparent native icon; no styled QWidget is involved."""
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    _paint_icon(painter, QRectF(0, 0, size, size), name, tone, framed=False)
    painter.end()
    return QIcon(pixmap)


def _event_icon(kind: str) -> tuple[str, str]:
    value = str(kind or "").strip()
    exact = {
        "session_created": ("session", "muted"),
        "user_message": ("prompt", "muted"),
        "turn_started": ("turn_start", "muted"),
        "turn_completed": ("check_check", "good"),
        "turn_failed": ("error", "bad"),
        "model_requested": ("model_request", "accent"),
        "model_response": ("model_response", "accent"),
        "turn_diff_updated": ("diff", "accent"),
        "tool_approval_required": ("approval", "warn"),
        "tool_requested": ("tool", "tool"),
        "tool_started": ("spinner", "tool"),
        "tool_completed": ("check", "good"),
        "tool_failed": ("error", "bad"),
        "process_started": ("terminal", "tool"),
        "process_exited": ("terminal_done", "good"),
    }
    if value in exact:
        return exact[value]
    if value.startswith("model_"):
        return "model_request", "accent"
    if value.startswith("tool_"):
        return "tool", "tool"
    if value.startswith("process_"):
        return "terminal", "tool"
    if value.startswith("turn_") and "fail" in value:
        return "error", "bad"
    return "dot", "muted"


def install() -> None:
    """Install the icon system into the existing widget surface."""
    from app.desktop import widgets

    widgets.VectorIcon.paintEvent = _vector_paint_event
    widgets.vector_icon = _vector_icon
    widgets._event_icon = _event_icon
