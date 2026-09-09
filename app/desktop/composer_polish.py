"""Presentation polish for Loom's composer surface and action controls.

The composer is the one piece of chrome users touch on every turn, so its
controls should read as one deliberately designed system rather than a row of
unrelated pills.  Behaviour stays in ``composer.py``; this module only refines
visual hierarchy, vector iconography, density, and state styling.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QPointF, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap

from app.desktop import theme


_INSTALLED = False

_COMPOSER_POLISH_QSS = r"""
/* ---- composer surface ------------------------------------------------ */
QFrame#composerFrame {
    background:qlineargradient(
        x1:0,y1:0,x2:0,y2:1,
        stop:0 #1d1f2a,
        stop:0.55 #1a1c26,
        stop:1 #171922
    );
    border:1px solid #353846;
    border-radius:18px;
}
QFrame#composerFrame:hover {
    border-color:#3c4050;
}
QFrame#composerFrame[focused="true"] {
    border-color:#5a5873;
    background:qlineargradient(
        x1:0,y1:0,x2:0,y2:1,
        stop:0 #20212d,
        stop:1 #191b24
    );
}
QTextEdit#composer {
    background:transparent;
    border:none;
    padding:5px 4px 7px 5px;
    color:#f0f2f7;
    font-size:14px;
    selection-background-color:#514a83;
}

/* All composer decision controls share the same physical geometry. */
QPushButton#composerControl,
QPushButton#composerWorkspace,
QPushButton#composerPermission,
QPushButton#composerModel {
    min-height:29px;
    max-height:29px;
    padding:0 10px;
    border-radius:9px;
    background:#161922;
    border:1px solid #292e3b;
    color:#adb4c2;
    font-size:11px;
    font-weight:620;
}
QPushButton#composerControl:hover,
QPushButton#composerWorkspace:hover,
QPushButton#composerPermission:hover,
QPushButton#composerModel:hover {
    background:#1d202b;
    border-color:#3a4050;
    color:#edf0f5;
}
QPushButton#composerControl:pressed,
QPushButton#composerWorkspace:pressed,
QPushButton#composerPermission:pressed,
QPushButton#composerModel:pressed {
    background:#13161e;
    border-color:#303645;
}
QPushButton#composerControl:disabled,
QPushButton#composerWorkspace:disabled,
QPushButton#composerPermission:disabled,
QPushButton#composerModel:disabled {
    color:#555d6b;
    background:#12141b;
    border-color:#1e222c;
}

/* Project and model are neutral decisions; the subtle tint only aids scanning. */
QPushButton#composerWorkspace {
    color:#bbc3d0;
    background:#171a23;
}
QPushButton#composerWorkspace:hover {
    color:#f1f3f7;
    border-color:#3b4252;
}
QPushButton#composerModel {
    color:#b8b5db;
    background:#181923;
    border-color:#2d2d3d;
}
QPushButton#composerModel:hover {
    color:#dedaff;
    background:#1d1d2a;
    border-color:#42405b;
}

/* Permission colour is meaningful, but restrained enough not to dominate. */
QPushButton#composerPermission[mode="full-access"] {
    color:#e6c47f;
    background:#1d180e;
    border-color:#55431f;
}
QPushButton#composerPermission[mode="full-access"]:hover {
    color:#f2d99e;
    background:#241d10;
    border-color:#705824;
}
QPushButton#composerPermission[mode="read-only"] {
    color:#9fc1dc;
    background:#131a21;
    border-color:#294052;
}
QPushButton#composerPermission[mode="workspace"] {
    color:#bbb5f0;
    background:#171725;
    border-color:#383456;
}
QPushButton#composerPermission[mode="approval"] {
    color:#c7bddd;
    background:#191820;
    border-color:#393442;
}

/* Usage is metadata, not a fourth button. */
QLabel#composerUsage {
    min-height:25px;
    max-height:25px;
    padding:0 9px;
    color:#858fa0;
    background:#0f131a;
    border:1px solid #202734;
    border-radius:8px;
    font-size:10px;
    font-weight:650;
}
QLabel#composerUsage:hover {
    color:#aab2bf;
    background:#121720;
    border-color:#2b3442;
}

/* Send is the only primary action in the row. */
QPushButton#sendButton {
    min-width:34px;
    max-width:34px;
    min-height:34px;
    max-height:34px;
    padding:0;
    border-radius:17px;
    background:qlineargradient(x1:0,y1:0,x2:0,y2:1,stop:0 #8278ed,stop:1 #685fda);
    border:1px solid #9189f2;
    color:#ffffff;
}
QPushButton#sendButton:hover {
    background:qlineargradient(x1:0,y1:0,x2:0,y2:1,stop:0 #9188f4,stop:1 #756be4);
    border-color:#aaa4f7;
}
QPushButton#sendButton:pressed {
    background:#6259cb;
    border-color:#7d74df;
}
QPushButton#sendButton:disabled {
    background:#191b25;
    border-color:#292d39;
    color:#59606e;
}

QPushButton#stopButton {
    min-height:29px;
    max-height:29px;
    border-radius:9px;
    padding:0 11px;
    background:#18151a;
    border:1px solid #3d292f;
    color:#d58d98;
    font-size:11px;
    font-weight:650;
}
QPushButton#stopButton:hover {
    background:#21171b;
    border-color:#5a343e;
    color:#efabb5;
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


def _icon_pixmap(kind: str, color: str, *, size: int = 16) -> QPixmap:
    """Render crisp, font-independent composer icons with Qt primitives."""
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.setPen(
        QPen(
            QColor(color),
            1.35,
            Qt.PenStyle.SolidLine,
            Qt.PenCapStyle.RoundCap,
            Qt.PenJoinStyle.RoundJoin,
        )
    )
    c = size / 2.0

    if kind == "workspace":
        # Small folder/project glyph.
        path = QPainterPath()
        path.moveTo(2.7, 5.0)
        path.lineTo(6.1, 5.0)
        path.lineTo(7.3, 6.25)
        path.lineTo(13.2, 6.25)
        path.lineTo(13.2, 12.25)
        path.lineTo(2.7, 12.25)
        path.closeSubpath()
        painter.drawPath(path)
        painter.drawLine(QPointF(3.0, 7.45), QPointF(12.9, 7.45))
    elif kind == "permission":
        path = QPainterPath()
        path.moveTo(c, 2.4)
        path.cubicTo(9.2, 3.55, 10.6, 4.0, 12.1, 4.35)
        path.lineTo(11.55, 8.85)
        path.cubicTo(11.2, 11.1, 9.7, 12.65, c, 13.55)
        path.cubicTo(6.3, 12.65, 4.8, 11.1, 4.45, 8.85)
        path.lineTo(3.9, 4.35)
        path.cubicTo(5.4, 4.0, 6.8, 3.55, c, 2.4)
        painter.drawPath(path)
    elif kind == "model":
        path = QPainterPath()
        path.moveTo(c, 2.8)
        path.lineTo(12.2, c)
        path.lineTo(c, 13.2)
        path.lineTo(3.8, c)
        path.closeSubpath()
        painter.drawPath(path)
        painter.drawEllipse(QRectF(c - 1.15, c - 1.15, 2.3, 2.3))
    elif kind == "send":
        painter.setPen(
            QPen(
                QColor(color),
                1.6,
                Qt.PenStyle.SolidLine,
                Qt.PenCapStyle.RoundCap,
                Qt.PenJoinStyle.RoundJoin,
            )
        )
        painter.drawLine(QPointF(c, 12.1), QPointF(c, 4.0))
        painter.drawLine(QPointF(c, 4.0), QPointF(4.8, 7.15))
        painter.drawLine(QPointF(c, 4.0), QPointF(11.2, 7.15))

    painter.end()
    return pixmap


def _composer_icon(kind: str, normal: str, disabled: str = "#5b6270") -> QIcon:
    icon = QIcon()
    icon.addPixmap(_icon_pixmap(kind, normal), QIcon.Mode.Normal, QIcon.State.Off)
    icon.addPixmap(_icon_pixmap(kind, disabled), QIcon.Mode.Disabled, QIcon.State.Off)
    return icon


def _set_control_icon(button: Any, kind: str, color: str) -> None:
    # ``ControlButton`` historically embedded a Unicode glyph in its text. Keep
    # value handling intact while switching the icon to a platform-independent
    # vector so Windows font fallback cannot make the toolbar look inconsistent.
    button._icon = ""
    button.setIcon(_composer_icon(kind, color))
    button.setIconSize(QSize(15, 15))
    button.setMinimumHeight(29)
    button.setMaximumHeight(29)
    if getattr(button, "_value", ""):
        button.setText(button._value)


def install() -> None:
    """Install composer presentation tweaks once."""
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

        # Give each decision a semantic selector while retaining the generic
        # composer-control styling as a fallback for future controls (Attach,
        # reasoning, etc.).
        self.workspace_button.setObjectName("composerWorkspace")
        self.permission_button.setObjectName("composerPermission")
        self.model_button.setObjectName("composerModel")
        _set_control_icon(self.workspace_button, "workspace", "#9ca7b9")
        _set_control_icon(self.permission_button, "permission", "#c5ad73")
        _set_control_icon(self.model_button, "model", "#aaa5df")

        self.usage_label.setObjectName("composerUsage")
        self.usage_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.usage_label.setTextFormat(Qt.TextFormat.PlainText)
        self.usage_label.setToolTip("Conversation token usage")

        self.send_button.setText("")
        self.send_button.setIcon(_composer_icon("send", "#ffffff"))
        self.send_button.setIconSize(QSize(16, 16))
        self.send_button.setFixedSize(34, 34)
        self.send_button.setToolTip("Send · Enter")

        # Slightly more deliberate breathing room than the original 6px row,
        # while keeping the entire composer compact.
        outer = self.layout()
        if outer is not None:
            outer.setContentsMargins(16, 12, 12, 10)
            outer.setSpacing(9)
            if outer.count() > 1:
                controls = outer.itemAt(1).layout()
                if controls is not None:
                    controls.setSpacing(7)

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
