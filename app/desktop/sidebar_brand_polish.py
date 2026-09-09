"""Final polish for the brand / primary-action block at the top of the sidebar.

The original header used a large gradient tile and two text-heavy buttons. At
narrow sidebar widths that made the most frequently seen chrome feel cramped and
caused the action row to clip. This pass keeps the same behavior, but gives the
area a quieter desktop-product hierarchy:

* compact 34px brand mark;
* tighter Loom/title typography;
* one readable New thread action;
* a compact icon-only Open project action;
* no extra decorative pill or gradient chrome.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QPointF, QSize, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QLabel

from app.desktop import theme


_INSTALLED = False
_WINDOW_INSTALLED = False

_BRAND_QSS = r"""
/* Sidebar identity ------------------------------------------------------- */
QLabel#brandMark {
    background:#6f63dc;
    border:none;
    border-radius:10px;
    color:#ffffff;
    font-size:14px;
    font-weight:760;
}
QLabel#brandLabel {
    background:transparent;
    color:#f2f3f6;
    font-size:18px;
    font-weight:680;
    letter-spacing:0.1px;
}
QLabel#brandSubtitle {
    background:transparent;
    color:#7d8592;
    font-size:10px;
    font-weight:500;
}

/* Primary sidebar actions ---------------------------------------------- */
QPushButton#newThreadButton {
    min-height:34px;
    max-height:34px;
    background:#1a1c22;
    border:1px solid #292c34;
    border-radius:9px;
    padding:0 12px;
    color:#e7e9ee;
    font-size:12px;
    font-weight:620;
    text-align:left;
}
QPushButton#newThreadButton:hover {
    background:#202229;
    border-color:#343741;
    color:#f5f6f8;
}
QPushButton#newThreadButton:pressed {
    background:#17191f;
    border-color:#2a2d35;
}
QPushButton#newThreadButton:disabled {
    background:#17181d;
    border-color:#22242a;
    color:#646a75;
}

QPushButton#openProjectButton {
    min-width:34px;
    max-width:34px;
    min-height:34px;
    max-height:34px;
    padding:0;
    background:transparent;
    border:1px solid transparent;
    border-radius:9px;
    color:#8e96a3;
}
QPushButton#openProjectButton:hover {
    background:#1c1e24;
    border-color:#292c34;
}
QPushButton#openProjectButton:pressed {
    background:#17191f;
    border-color:#252830;
}
"""


def _icon(kind: str, *, size: int = 16) -> QIcon:
    """Render tiny brand-adjacent icons without font glyph dependencies."""
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    color = QColor("#aaa4ee" if kind == "plus" else "#8e96a3")
    painter.setPen(
        QPen(
            color,
            1.45,
            Qt.PenStyle.SolidLine,
            Qt.PenCapStyle.RoundCap,
            Qt.PenJoinStyle.RoundJoin,
        )
    )

    if kind == "plus":
        c = size / 2.0
        span = 3.25
        painter.drawLine(QPointF(c - span, c), QPointF(c + span, c))
        painter.drawLine(QPointF(c, c - span), QPointF(c, c + span))
    else:
        # Familiar open-folder outline, optically centered on a 16px box.
        path = QPainterPath(QPointF(2.3, 5.2))
        path.lineTo(QPointF(6.2, 5.2))
        path.lineTo(QPointF(7.6, 6.7))
        path.lineTo(QPointF(13.5, 6.7))
        path.lineTo(QPointF(13.5, 12.6))
        path.lineTo(QPointF(2.3, 12.6))
        path.closeSubpath()
        painter.drawPath(path)
        painter.drawLine(QPointF(2.6, 7.8), QPointF(13.2, 7.8))

    painter.end()
    return QIcon(pixmap)


def install() -> None:
    """Append the brand rules after generic sidebar de-plasticizing."""
    global _INSTALLED
    if _INSTALLED:
        return
    _INSTALLED = True

    original_stylesheet = theme.stylesheet

    def stylesheet() -> str:
        return original_stylesheet() + _BRAND_QSS

    theme.stylesheet = stylesheet


def install_window(window_cls: type[Any]) -> None:
    """Refine the existing sidebar layout without changing any callbacks."""
    global _WINDOW_INSTALLED
    if _WINDOW_INSTALLED:
        return
    _WINDOW_INSTALLED = True

    original_build_sidebar = window_cls._build_sidebar

    def build_sidebar(self: Any) -> None:
        original_build_sidebar(self)

        sidebar_layout = self.sidebar_panel.layout()
        if sidebar_layout is not None:
            sidebar_layout.setContentsMargins(16, 20, 12, 16)
            sidebar_layout.setSpacing(10)

            brand_layout = sidebar_layout.itemAt(0).layout() if sidebar_layout.count() else None
            if brand_layout is not None:
                brand_layout.setContentsMargins(3, 0, 3, 3)
                brand_layout.setSpacing(10)
                # Item 1 is the two-line title layout in the canonical sidebar.
                if brand_layout.count() > 1:
                    title_layout = brand_layout.itemAt(1).layout()
                    if title_layout is not None:
                        title_layout.setSpacing(1)

            actions_layout = sidebar_layout.itemAt(1).layout() if sidebar_layout.count() > 1 else None
            if actions_layout is not None:
                actions_layout.setContentsMargins(2, 3, 2, 3)
                actions_layout.setSpacing(6)

        mark = self.sidebar_panel.findChild(QLabel, "brandMark")
        if mark is not None:
            mark.setFixedSize(34, 34)

        self.new_thread_button.setText("New thread")
        self.new_thread_button.setIcon(_icon("plus"))
        self.new_thread_button.setIconSize(QSize(16, 16))
        self.new_thread_button.setMinimumWidth(0)

        self.open_project_button.setText("")
        self.open_project_button.setIcon(_icon("folder"))
        self.open_project_button.setIconSize(QSize(16, 16))
        self.open_project_button.setFixedSize(34, 34)
        self.open_project_button.setAccessibleName("Open project")

    window_cls._build_sidebar = build_sidebar


__all__ = ["install", "install_window"]
