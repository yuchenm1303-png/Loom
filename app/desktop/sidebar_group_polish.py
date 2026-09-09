"""Polish project/group headers in the conversation sidebar.

The header is deliberately quiet: it should separate runs of conversations,
not look like another selectable card. Existing actions/counts are detected
semantically so this presentation layer also works with builds that add project
controls without taking ownership of their behaviour.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QPointF, QSize, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QFrame, QLabel, QPushButton

from app.desktop import widgets


_INSTALLED = False

_GROUP_QSS = r"""
QWidget#threadGroupHeader {
    background: transparent;
}
QLabel#threadGroupLabel {
    background: transparent;
    color: #8e97aa;
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 0.9px;
    padding: 0px;
}
QWidget#threadGroupHeader[orphan="true"] QLabel#threadGroupLabel {
    color: #767f91;
}
QFrame#threadGroupRule {
    min-height: 1px;
    max-height: 1px;
    border: none;
    background: #242731;
}
QPushButton#threadGroupAddButton,
QPushButton#threadGroupMoreButton {
    min-width: 24px;
    max-width: 24px;
    min-height: 24px;
    max-height: 24px;
    padding: 0px;
    background: transparent;
    border: 1px solid transparent;
    border-radius: 6px;
}
QPushButton#threadGroupAddButton:hover,
QPushButton#threadGroupMoreButton:hover {
    background: #1c1e25;
    border-color: transparent;
}
QPushButton#threadGroupAddButton:pressed,
QPushButton#threadGroupMoreButton:pressed {
    background: #181a20;
    border-color: transparent;
}
QLabel#threadGroupCount {
    min-width: 14px;
    max-height: 20px;
    padding: 0px 2px;
    background: transparent;
    border: none;
    border-radius: 0px;
    color: #798294;
    font-size: 10px;
    font-weight: 620;
}
"""


def _action_icon(kind: str, *, size: int = 14) -> QIcon:
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(
        QPen(
            QColor("#7f899b"),
            1.3,
            Qt.PenStyle.SolidLine,
            Qt.PenCapStyle.RoundCap,
            Qt.PenJoinStyle.RoundJoin,
        )
    )
    c = size / 2.0
    if kind == "add":
        painter.drawLine(QPointF(c, 3.5), QPointF(c, size - 3.5))
        painter.drawLine(QPointF(3.5, c), QPointF(size - 3.5, c))
    else:
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#7f899b"))
        for x in (c - 3.5, c, c + 3.5):
            painter.drawEllipse(QPointF(x, c), 1.0, 1.0)
    painter.end()
    return QIcon(pixmap)


def _normalise_action(button: QPushButton) -> None:
    text = "".join(button.text().strip().lower().split())
    name = button.objectName().lower()
    if text in {"+", "＋"} or "add" in name or "new" in name:
        button.setObjectName("threadGroupAddButton")
        button.setText("")
        button.setIcon(_action_icon("add"))
        button.setIconSize(QSize(14, 14))
        button.setToolTip(button.toolTip() or "New conversation in this project")
    elif text in {"...", "…", "•••", "⋯"} or "more" in name or "menu" in name:
        button.setObjectName("threadGroupMoreButton")
        button.setText("")
        button.setIcon(_action_icon("more"))
        button.setIconSize(QSize(14, 14))
        button.setToolTip(button.toolTip() or "Project actions")
    else:
        return
    button.setFixedSize(24, 24)
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    button.setFocusPolicy(Qt.FocusPolicy.NoFocus)


def _polish_header(header: Any, title: str) -> None:
    header.setObjectName("threadGroupHeader")
    header.setProperty("orphan", title.strip().casefold() in {"no project", "unassigned"})
    header.setStyleSheet(_GROUP_QSS)
    header.setMinimumHeight(31)

    layout = header.layout()
    if layout is not None:
        # A little more breathing room above the group than below it makes the
        # section boundary read clearly without adding another card surface.
        layout.setContentsMargins(5, 7, 3, 5)
        layout.setSpacing(9)

    title_label = getattr(header, "label", None)
    if isinstance(title_label, QLabel):
        title_label.setObjectName("threadGroupLabel")
        title_label.setTextFormat(Qt.TextFormat.PlainText)
        title_label.setToolTip(title)

    for rule in header.findChildren(QFrame):
        if rule is header:
            continue
        if rule.objectName() == "threadGroupRule" or rule.height() <= 2:
            rule.setObjectName("threadGroupRule")
            rule.setFixedHeight(1)

    for button in header.findChildren(QPushButton):
        _normalise_action(button)

    # Counts are metadata, not badges. Keep the public label while removing the
    # extra outlined capsule that previously competed with the section title.
    for label in header.findChildren(QLabel):
        if label is title_label:
            continue
        if label.text().strip().isdigit():
            label.setObjectName("threadGroupCount")
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    _INSTALLED = True

    original_init = widgets.ThreadGroupHeader.__init__

    def init(self: Any, title: str, parent: Any = None, *args: Any, **kwargs: Any) -> None:
        original_init(self, title, parent, *args, **kwargs)
        _polish_header(self, str(title or ""))

    widgets.ThreadGroupHeader.__init__ = init


__all__ = ["install"]
