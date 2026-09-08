"""Loom's native vector icon system.

The desktop client uses a restrained, product-style line icon language rather
than decorative AI glyphs. Every icon sits on the same 24-unit grid and uses
the same stroke geometry so Runtime tabs, event rows, and inline cards read as
one commercial desktop product.

Identity and state stay separate: the glyph says what is acting, while the
adjacent status badge says how it is going. Completed tool cards therefore keep
their capability icon instead of becoming a wall of green checks.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap

from app.desktop import theme


# Deliberately subdued. These are information colors, not neon decoration.
_TOOL_BLUE = "#77a0d6"
_AGENT_PURPLE = "#8e88d8"
_NEUTRAL = "#9098a5"


def _tone_color(tone: str) -> QColor:
    return QColor(
        {
            "accent": _AGENT_PURPLE,
            "tool": _TOOL_BLUE,
            "good": theme.GOOD,
            "warn": theme.WARN,
            "bad": theme.BAD,
            "muted": _NEUTRAL,
        }.get(tone, _NEUTRAL)
    )


def _frame_colors(tone: str) -> tuple[QColor, QColor]:
    """Quiet framing: one dark surface, only a small semantic border shift."""
    border = {
        "accent": "#343246",
        "tool": "#2d3743",
        "good": "#2d3934",
        "warn": "#3c3529",
        "bad": "#3b2e31",
        "muted": "#2a2f37",
    }.get(tone, "#2a2f37")
    return QColor("#101318"), QColor(border)


def _box(rect: QRectF, *, framed: bool) -> QRectF:
    inset = rect.width() * (0.235 if framed else 0.10)
    return rect.adjusted(inset, inset, -inset, -inset)


def _p(box: QRectF, x: float, y: float) -> QPointF:
    return QPointF(
        box.left() + box.width() * x / 24.0,
        box.top() + box.height() * y / 24.0,
    )


def _line(painter: QPainter, box: QRectF, *coords: float) -> None:
    painter.drawLine(_p(box, coords[0], coords[1]), _p(box, coords[2], coords[3]))


def _ellipse(
    painter: QPainter,
    box: QRectF,
    x: float,
    y: float,
    w: float,
    h: float,
) -> None:
    painter.drawEllipse(QRectF(_p(box, x, y), _p(box, x + w, y + h)))


def _rounded_rect(
    painter: QPainter,
    box: QRectF,
    x: float,
    y: float,
    w: float,
    h: float,
    radius: float = 2.0,
) -> None:
    rect = QRectF(_p(box, x, y), _p(box, x + w, y + h))
    r = max(1.0, box.width() * radius / 24.0)
    painter.drawRoundedRect(rect, r, r)


def _filled_dot(
    painter: QPainter,
    box: QRectF,
    x: float,
    y: float,
    diameter: float,
) -> None:
    old = painter.brush()
    painter.setBrush(painter.pen().color())
    _ellipse(painter, box, x - diameter / 2, y - diameter / 2, diameter, diameter)
    painter.setBrush(old)


# ---------------------------------------------------------------------------
# Product glyphs
# ---------------------------------------------------------------------------


def _draw_agent(painter: QPainter, box: QRectF) -> None:
    """Agent / orchestration: a clean routed branch, not a molecule symbol."""
    _line(painter, box, 5, 6, 10, 6)
    _line(painter, box, 10, 6, 10, 18)
    _line(painter, box, 10, 10, 18, 10)
    _line(painter, box, 10, 18, 18, 18)
    _ellipse(painter, box, 3.7, 4.7, 2.6, 2.6)
    _ellipse(painter, box, 16.7, 8.7, 2.6, 2.6)
    _ellipse(painter, box, 16.7, 16.7, 2.6, 2.6)


def _draw_agents(painter: QPainter, box: QRectF) -> None:
    """Delegated work: stacked work surfaces rather than a node constellation."""
    _rounded_rect(painter, box, 6, 4, 13, 11, 2.0)
    _rounded_rect(painter, box, 3, 9, 13, 11, 2.0)
    _line(painter, box, 7, 13, 12, 13)
    _line(painter, box, 7, 16.5, 10.5, 16.5)


def _draw_terminal(painter: QPainter, box: QRectF) -> None:
    _rounded_rect(painter, box, 2.2, 3.2, 19.6, 17.6, 2.4)
    _line(painter, box, 6, 8.1, 9.7, 11.7)
    _line(painter, box, 9.7, 11.7, 6, 15.3)
    _line(painter, box, 12.7, 15.3, 18, 15.3)


def _draw_computer(painter: QPainter, box: QRectF) -> None:
    _rounded_rect(painter, box, 2.2, 3.2, 19.6, 13.8, 2.0)
    _line(painter, box, 9, 20.5, 15, 20.5)
    _line(painter, box, 12, 17.2, 12, 20.2)
    pointer = QPainterPath(_p(box, 12.7, 7.0))
    pointer.lineTo(_p(box, 18.5, 11.0))
    pointer.lineTo(_p(box, 15.6, 11.8))
    pointer.lineTo(_p(box, 17.5, 15.3))
    pointer.lineTo(_p(box, 15.6, 16.3))
    pointer.lineTo(_p(box, 13.6, 12.6))
    pointer.lineTo(_p(box, 11.8, 14.7))
    pointer.closeSubpath()
    painter.drawPath(pointer)


def _draw_browser(painter: QPainter, box: QRectF) -> None:
    _rounded_rect(painter, box, 2.2, 3.2, 19.6, 17.6, 2.3)
    _line(painter, box, 2.8, 8.2, 21.2, 8.2)
    for x in (5.1, 8.2, 11.3):
        _filled_dot(painter, box, x, 5.8, 1.35)
    _line(painter, box, 6, 12, 18, 12)
    _line(painter, box, 6, 15.2, 15.2, 15.2)


def _draw_tool(painter: QPainter, box: QRectF) -> None:
    """Generic tool: a conventional wrench, instantly readable in business UI."""
    path = QPainterPath(_p(box, 5.0, 4.5))
    path.cubicTo(_p(box, 8.0, 3.0), _p(box, 11.2, 5.2), _p(box, 10.5, 8.5))
    path.lineTo(_p(box, 8.5, 10.5))
    path.cubicTo(_p(box, 5.3, 11.2), _p(box, 3.0, 8.0), _p(box, 4.5, 5.0))
    path.lineTo(_p(box, 7.0, 7.5))
    path.lineTo(_p(box, 8.2, 6.3))
    path.closeSubpath()
    painter.drawPath(path)
    _line(painter, box, 9.7, 9.7, 18.2, 18.2)
    _ellipse(painter, box, 16.4, 16.4, 3.6, 3.6)


def _draw_search(painter: QPainter, box: QRectF) -> None:
    _ellipse(painter, box, 3.2, 3.2, 12.7, 12.7)
    _line(painter, box, 14.3, 14.3, 20.6, 20.6)


def _draw_file(painter: QPainter, box: QRectF) -> None:
    path = QPainterPath(_p(box, 5, 2.8))
    path.lineTo(_p(box, 14.6, 2.8))
    path.lineTo(_p(box, 19.6, 7.8))
    path.lineTo(_p(box, 19.6, 21.2))
    path.lineTo(_p(box, 5, 21.2))
    path.closeSubpath()
    painter.drawPath(path)
    _line(painter, box, 14.6, 3, 14.6, 7.8)
    _line(painter, box, 14.6, 7.8, 19.3, 7.8)
    _line(painter, box, 8, 12.2, 16.4, 12.2)
    _line(painter, box, 8, 16.1, 14.2, 16.1)


def _draw_edit(painter: QPainter, box: QRectF) -> None:
    _rounded_rect(painter, box, 3.2, 4.1, 13.5, 16.1, 1.8)
    pen = QPainterPath(_p(box, 9.1, 16.2))
    pen.lineTo(_p(box, 17.8, 7.5))
    pen.lineTo(_p(box, 20.5, 10.2))
    pen.lineTo(_p(box, 11.8, 18.9))
    pen.lineTo(_p(box, 8.7, 19.3))
    pen.closeSubpath()
    painter.drawPath(pen)


def _draw_diff(painter: QPainter, box: QRectF) -> None:
    """Two-column change marker; simpler than a decorated document glyph."""
    _line(painter, box, 6, 4, 6, 20)
    _line(painter, box, 18, 4, 18, 20)
    _line(painter, box, 3.5, 9, 8.5, 9)
    _line(painter, box, 6, 6.5, 6, 11.5)
    _line(painter, box, 15.5, 15, 20.5, 15)


def _draw_activity(painter: QPainter, box: QRectF) -> None:
    _line(painter, box, 6.2, 5, 6.2, 19)
    for y in (6, 12, 18):
        _filled_dot(painter, box, 6.2, y, 2.2)
    _line(painter, box, 9, 6, 19, 6)
    _line(painter, box, 9, 12, 16.5, 12)
    _line(painter, box, 9, 18, 19, 18)


# ---------------------------------------------------------------------------
# Runtime / event glyphs
# ---------------------------------------------------------------------------


def _draw_session(painter: QPainter, box: QRectF) -> None:
    _ellipse(painter, box, 3, 3, 18, 18)
    _filled_dot(painter, box, 12, 12, 2.8)


def _draw_turn_start(painter: QPainter, box: QRectF) -> None:
    _ellipse(painter, box, 3, 3, 18, 18)
    _line(painter, box, 8.2, 12, 15.8, 12)
    _line(painter, box, 12.5, 8.7, 15.8, 12)
    _line(painter, box, 12.5, 15.3, 15.8, 12)


def _draw_prompt(painter: QPainter, box: QRectF) -> None:
    _rounded_rect(painter, box, 3, 4, 18, 13, 2.5)
    path = QPainterPath(_p(box, 8, 17))
    path.lineTo(_p(box, 6.2, 20))
    path.lineTo(_p(box, 11, 17))
    painter.drawPath(path)


def _draw_model_request(painter: QPainter, box: QRectF) -> None:
    """Model call: compact processor chip, not a sparkle or game-like node."""
    _rounded_rect(painter, box, 6, 6, 12, 12, 2.0)
    _rounded_rect(painter, box, 9, 9, 6, 6, 1.3)
    for x in (8, 12, 16):
        _line(painter, box, x, 3, x, 6)
        _line(painter, box, x, 18, x, 21)
    for y in (8, 12, 16):
        _line(painter, box, 3, y, 6, y)
        _line(painter, box, 18, y, 21, y)


def _draw_model_response(painter: QPainter, box: QRectF) -> None:
    _draw_prompt(painter, box)
    _line(painter, box, 7, 9, 17, 9)
    _line(painter, box, 7, 12.5, 14.5, 12.5)


def _draw_spinner(painter: QPainter, box: QRectF) -> None:
    """Running state: quiet clock metaphor instead of a bright open C-spinner."""
    _ellipse(painter, box, 3, 3, 18, 18)
    _line(painter, box, 12, 7.2, 12, 12)
    _line(painter, box, 12, 12, 15.8, 14.2)


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
    _draw_terminal(painter, box)
    _line(painter, box, 12.2, 14.7, 14.4, 16.8)
    _line(painter, box, 14.4, 16.8, 19, 12.0)


def _draw_dot(painter: QPainter, box: QRectF) -> None:
    _filled_dot(painter, box, 12, 12, 3.5)


_DRAWERS = {
    "activity": _draw_activity,
    "model": _draw_agent,
    "agent": _draw_agent,
    "agents": _draw_agents,
    "terminal": _draw_terminal,
    "computer": _draw_computer,
    "browser": _draw_browser,
    "tool": _draw_tool,
    "search": _draw_search,
    "file": _draw_file,
    "edit": _draw_edit,
    "diff": _draw_diff,
    "session": _draw_session,
    "turn_start": _draw_turn_start,
    "prompt": _draw_prompt,
    "model_request": _draw_model_request,
    "model_response": _draw_model_response,
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
    width = max(1.3, min(1.58, rect.width() * 0.052))
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
    _DRAWERS.get(name, _draw_dot)(painter, _box(rect, framed=framed))


def _vector_paint_event(self: Any, _event: Any) -> None:
    painter = QPainter(self)
    _paint_icon(painter, QRectF(self.rect()), self.name, self.tone, framed=self.framed)


def _vector_icon(name: str, *, size: int = 18, tone: str = "muted") -> QIcon:
    """Render a truly transparent native icon without a styled QWidget."""
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


def _tool_icon_name(title: str) -> str:
    """Choose a stable identity glyph from a tool's presented name."""
    value = " ".join(str(title or "").lower().replace("-", "_").split())

    if any(
        token in value
        for token in (
            "spawn_agent",
            "send_agent",
            "wait_agent",
            "list_agents",
            "close_agent",
            "sub_agent",
            "subagent",
            "delegate",
            "delegated",
        )
    ):
        return "agent"
    if any(
        token in value
        for token in (
            "computer",
            "desktop",
            "screen",
            "mouse",
            "keyboard",
            "gui_",
            "uia",
        )
    ):
        return "computer"
    if any(token in value for token in ("browser", "navigate", "playwright", "page_", "tab_")):
        return "browser"
    if any(token in value for token in ("search", "find_", "grep", "lookup")):
        return "search"
    if any(token in value for token in ("apply_patch", "patch", "edit", "write_file", "replace")):
        return "edit"
    if any(token in value for token in ("read_file", "open_file", "list_file", "glob", "workspace_file")):
        return "file"
    if any(
        token in value
        for token in (
            "exec",
            "shell",
            "terminal",
            "command",
            "powershell",
            "cmd",
            "bash",
        )
    ):
        return "terminal"
    return "tool"


def _card_icon_for(kind: str, title: str) -> tuple[str, str]:
    if kind == "process":
        return "terminal", "muted"
    if kind == "diff":
        return "diff", "accent"
    if kind == "error":
        return "error", "bad"
    if kind == "tool":
        name = _tool_icon_name(title)
        return name, "accent" if name in {"agent", "agents"} else "muted"
    return "dot", "muted"


def _install_activity_card_routing(widgets: Any) -> None:
    """Give inline cards a capability glyph without changing transcript behavior."""
    cls = widgets.ActivityCard
    if getattr(cls, "_loom_iconography_routing", False):
        return

    original = cls.update_card

    def update_card(
        self: Any,
        *,
        title: str,
        subtitle: str = "",
        status: str = "",
        body: str = "",
        auto_expand: bool | None = None,
    ) -> None:
        original(
            self,
            title=title,
            subtitle=subtitle,
            status=status,
            body=body,
            auto_expand=auto_expand,
        )
        icon_name, icon_tone = _card_icon_for(str(getattr(self, "kind", "")), title)
        if getattr(self.icon, "name", "") != icon_name:
            self.icon.name = icon_name
            self.icon.update()

        # Status already has its own badge. Keep the left-side icon neutral so
        # completed cards do not acquire a bright green "selected/game" look.
        self.icon.set_tone(icon_tone)

    cls.update_card = update_card
    cls._loom_iconography_routing = True


def install() -> None:
    """Install the icon system into the existing widget surface."""
    from app.desktop import widgets

    widgets.VectorIcon.paintEvent = _vector_paint_event
    widgets.vector_icon = _vector_icon
    widgets._event_icon = _event_icon
    _install_activity_card_routing(widgets)
