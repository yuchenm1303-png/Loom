"""Commercial visual polish for the completed-response action row.

The first pass established the behavior. This pass keeps the copy icon intact
and refines the weaker controls into a calmer, optically aligned desktop action
strip: a proper thumbs-down silhouette, a standard open-in-focus arrow, and a
shared 28 px vertical rhythm with the timestamp.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QPushButton

from app.desktop import message_actions


_INSTALLED = False

_REFINED_QSS = """
QWidget#messageActions {
    background: transparent;
}
QPushButton#messageAction {
    background: transparent;
    border: none;
    border-radius: 7px;
    padding: 0;
}
QPushButton#messageAction:hover {
    background: #171a20;
}
QPushButton#messageAction:pressed {
    background: #1d2128;
}
QPushButton#messageAction:checked {
    background: #171b22;
}
QLabel#messageActionTime {
    background: transparent;
    color: #777f8b;
    font-size: 11px;
    padding: 0;
}
"""


def _icon_color(button: Any) -> QColor:
    if button.isDown():
        return QColor("#d2d6dd")
    if button.isChecked():
        return QColor("#aab3c0")
    if button.underMouse():
        return QColor("#a7aeb8")
    return QColor("#747d89")


def _draw_feedback(painter: QPainter) -> None:
    """Draw a balanced outline thumbs-down at a 16 px optical scale."""

    # Cuff first: a small rounded vertical block separates the control surface
    # from the hand instead of letting the silhouette collapse into one blob.
    painter.drawRoundedRect(QRectF(3.0, -4.7, 3.0, 7.7), 1.0, 1.0)

    hand = QPainterPath()
    hand.moveTo(QPointF(3.0, -4.0))
    hand.lineTo(QPointF(-2.8, -4.0))
    hand.cubicTo(QPointF(-4.0, -4.0), QPointF(-4.8, -3.3), QPointF(-5.1, -2.2))
    hand.lineTo(QPointF(-5.8, 0.7))
    hand.cubicTo(QPointF(-6.0, 1.7), QPointF(-5.2, 2.5), QPointF(-4.1, 2.5))
    hand.lineTo(QPointF(-1.0, 2.5))
    hand.lineTo(QPointF(-1.3, 4.7))
    hand.cubicTo(QPointF(-1.5, 5.8), QPointF(-0.6, 6.3), QPointF(0.1, 5.5))
    hand.lineTo(QPointF(3.0, 2.3))
    painter.drawPath(hand)


def _draw_open(painter: QPainter) -> None:
    """Draw a standard open-in-focus mark, not a game-style expand glyph."""

    # Quiet lower-left frame communicates "open/view" while the single diagonal
    # arrow supplies direction. Both sit on the same 16 px optical box as copy.
    frame = QPainterPath()
    frame.moveTo(QPointF(-5.0, -1.2))
    frame.lineTo(QPointF(-5.0, 4.8))
    frame.lineTo(QPointF(1.2, 4.8))
    painter.drawPath(frame)

    painter.drawLine(QPointF(-1.5, 1.5), QPointF(4.8, -4.8))
    painter.drawLine(QPointF(1.3, -4.8), QPointF(4.8, -4.8))
    painter.drawLine(QPointF(4.8, -4.8), QPointF(4.8, -1.3))


def install() -> None:
    """Install the refined geometry after the behavioral action module."""

    global _INSTALLED
    if _INSTALLED:
        return
    _INSTALLED = True

    original_button_init = message_actions.MessageActionButton.__init__
    original_paint = message_actions.MessageActionButton.paintEvent
    original_bar_init = message_actions.MessageActionBar.__init__

    def button_init(self: Any, kind: str, tooltip: str, parent: Any = None) -> None:
        original_button_init(self, kind, tooltip, parent)
        # One hit target and one centerline for every action. The previous 28x26
        # buttons made the timestamp and icons look like separate rows.
        self.setFixedSize(28, 28)

    def paint_event(self: Any, event: Any) -> None:
        if self.kind == "copy":
            # The user explicitly preferred the existing copy glyph; preserve it.
            original_paint(self, event)
            return

        # Paint only the button surface, then our own icon. Calling the original
        # MessageActionButton paint here would also draw the first-pass glyph.
        QPushButton.paintEvent(self, event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.translate(self.width() / 2.0, self.height() / 2.0 - 0.25)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(
            QPen(
                _icon_color(self),
                1.45,
                Qt.PenStyle.SolidLine,
                Qt.PenCapStyle.RoundCap,
                Qt.PenJoinStyle.RoundJoin,
            )
        )

        if self.kind == "dislike":
            _draw_feedback(painter)
        else:
            _draw_open(painter)

    def bar_init(self: Any, message: Any) -> None:
        original_bar_init(self, message)
        self.setStyleSheet(self.styleSheet() + "\n" + _REFINED_QSS)
        self.setFixedHeight(30)

        row = self.layout()
        if row is not None:
            row.setContentsMargins(0, 1, 0, 1)
            row.setSpacing(1)
            row.setAlignment(Qt.AlignmentFlag.AlignVCenter)

        for button in (self.copy_button, self.dislike_button, self.open_button):
            button.setFixedSize(28, 28)

        # Give the timestamp the exact same vertical box as the controls. Qt then
        # centers the text baseline instead of aligning it against the layout's
        # font-dependent size hint.
        self.time_label.setFixedHeight(28)
        self.time_label.setMinimumWidth(42)
        self.time_label.setContentsMargins(7, 0, 0, 0)
        self.time_label.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        )

    message_actions.MessageActionButton.__init__ = button_init
    message_actions.MessageActionButton.paintEvent = paint_event
    message_actions.MessageActionBar.__init__ = bar_init


__all__ = ["install"]
