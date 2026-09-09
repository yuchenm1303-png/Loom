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
    color: #98a2b8;
    font-size: 10px;
    font-weight: 720;
    letter-spacing: 0.95px;
    padding: 0px;
}
QWidget#threadGroupHeader[orphan="true"] QLabel#threadGroupLabel {
    color: #7f899d;
}
QFrame#threadGroupRule {
    min-height: 1px;
    max-height: 1px;
    border: none;
    background: qlineargradient(
        x1:0, y1:0, x2:1, y2:0,
        stop:0 #303747,
        stop:0.65 #282e3b,
        stop:1 transparent
    );
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
    border-radius: 7px;
}
QPushButton#threadGroupAddButton:hover,
QPushButton#threadGroupMoreButton:hover {
    background: #1b2029;
    border-color: #303846;
}
QPushButton#threadGroupAddButton:pressed,
QPushButton#threadGroupMoreButton:pressed {
    background: #121720;
    border-color: #262e3b;
}
QLabel#threadGroupCount {
    min-width: 20px;
    max-height: 20px;
    padding: 0px 6px;
    background: #151a23;
    border: 1px solid #293142;
    border-radius: 9px;
    color: #929db4;
    font-size: 10px;
    font-weight: 700;
}
"""


def _action_icon(kind: str, *, size: int = 14) -> QIcon:
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(
        QPen(
            QColor("#8994a9"),
            1.35,
            Qt.PenStyle.SolidLine,
            Qt.PenCapStyle.RoundCap,
            Qt.PenJoinStyle.RoundJoin,
        )
    )
    c = size / 2.0
    if kind == "add":
        painter.drawLine(QPointF(c, 3.4), QPointF(c, size - 3.4))
        painter.drawLine(QPointF(3.4, c), QPointF(size - 3.4, c))
    else:
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#8994a9"))
        for x in (c - 3.5, c, c + 3.5):
            painter.drawEllipse(QPointF(x, c), 1.05, 1.05)
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

    # Some sidebar variants expose a group count next to the divider. Turn that
    # raw number into metadata rather than letting it float as plain text.
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
