"""Visual polish for the compact usage / send cluster in Loom's composer.

Kept as a presentation hook so the durable composer behaviour stays in
``composer.py``.  The chip communicates context usage; the circular send action
stays visually dominant without becoming a floating-action-button sized orb.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap

from app.desktop import theme


_INSTALLED = False

_COMPOSER_POLISH_QSS = r"""
/* ---- compact composer action cluster ---- */
QLabel#composerUsage {
    min-height:24px;
    max-height:24px;
    padding:0 9px;
    color:#8d96a7;
    background:#0c1016;
    border:1px solid #1d2530;
    border-radius:9px;
    font-size:10px;
    font-weight:620;
}
QLabel#composerUsage:hover {
    color:#aeb5c0;
    background:#0f141b;
    border-color:#28313d;
}
QPushButton#sendButton {
    min-width:32px;
    max-width:32px;
    min-height:32px;
    max-height:32px;
    padding:0;
    border-radius:16px;
    background:#6f65df;
    border:1px solid #8178e8;
    color:#ffffff;
}
QPushButton#sendButton:hover {
    background:#7a70e8;
    border-color:#948cf0;
}
QPushButton#sendButton:pressed {
    background:#6259ca;
    border-color:#756bd9;
}
QPushButton#sendButton:disabled {
    background:#171923;
    border-color:#242834;
    color:#555b68;
}
"""


def _compact_tokens(total: int) -> str:
    value = max(0, int(total or 0))
    if value < 1_000:
        return f"{value}"
    if value < 100_000:
        return f"{value / 1_000:.1f}k"
    if value < 1_000_000:
        return f"{value / 1_000:.0f}k"
    if value < 100_000_000:
        return f"{value / 1_000_000:.1f}m"
    return f"{value / 1_000_000:.0f}m"


def _arrow_pixmap(color: str, *, size: int = 16) -> QPixmap:
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(
        QColor(color),
        1.55,
        Qt.PenStyle.SolidLine,
        Qt.PenCapStyle.RoundCap,
        Qt.PenJoinStyle.RoundJoin,
    )
    painter.setPen(pen)
    c = size / 2
    painter.drawLine(int(c), 12, int(c), 4)
    painter.drawLine(int(c), 4, 5, 7)
    painter.drawLine(int(c), 4, 11, 7)
    painter.end()
    return pixmap


def _send_icon() -> QIcon:
    icon = QIcon()
    icon.addPixmap(_arrow_pixmap("#ffffff"), QIcon.Mode.Normal, QIcon.State.Off)
    icon.addPixmap(_arrow_pixmap("#5c6270"), QIcon.Mode.Disabled, QIcon.State.Off)
    return icon


def install() -> None:
    """Install the composer presentation tweaks once."""
    global _INSTALLED
    if _INSTALLED:
        return
    _INSTALLED = True

    from app.desktop.composer import ComposerPanel

    original_init = ComposerPanel.__init__
    original_set_usage = ComposerPanel.set_usage
    original_stylesheet = theme.stylesheet

    def init(self: Any, *args: Any, **kwargs: Any) -> None:
        original_init(self, *args, **kwargs)

        self.usage_label.setObjectName("composerUsage")
        self.usage_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.usage_label.setTextFormat(Qt.TextFormat.PlainText)
        self.usage_label.setToolTip("Conversation token usage")

        self.send_button.setText("")
        self.send_button.setIcon(_send_icon())
        self.send_button.setIconSize(QSize(16, 16))
        self.send_button.setFixedSize(32, 32)
        self.send_button.setToolTip("Send · Enter")

    def set_usage(self: Any, total: int) -> None:
        value = max(0, int(total or 0))
        if not value:
            original_set_usage(self, 0)
            return
        self.usage_label.setText(f"{_compact_tokens(value)} tokens")
        self.usage_label.setToolTip(f"{value:,} tokens in this conversation")
        self.usage_label.setVisible(True)

    def stylesheet() -> str:
        return original_stylesheet() + _COMPOSER_POLISH_QSS

    ComposerPanel.__init__ = init
    ComposerPanel.set_usage = set_usage
    theme.stylesheet = stylesheet


__all__ = ["install", "_compact_tokens"]
