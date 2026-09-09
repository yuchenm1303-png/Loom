"""Presentation polish for Loom's composer surface and action controls.

The composer is the one piece of chrome users touch on every turn, so its
controls should read as one deliberately designed system rather than a row of
unrelated pills. Behaviour stays in ``composer.py``; this module only refines
visual hierarchy, vector iconography, density, and state styling.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QPointF, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QPushButton

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

/* ---- compact control system ---------------------------------------- */
/*
   All four controls share one silhouette and baseline. Their semantic colour
   lives in the icon and a very small border/text tint instead of four unrelated
   fills, which makes the row read like one professional toolbar.
*/
QPushButton#composerControl,
QPushButton#composerAttach,
QPushButton#composerWorkspace,
QPushButton#composerPermission,
QPushButton#composerModel {
    min-height:31px;
    max-height:31px;
    padding:0 11px 0 10px;
    border-radius:10px;
    background:#151821;
    border:1px solid #2d3240;
    color:#c2c8d3;
    font-size:11px;
    font-weight:610;
}
QPushButton#composerControl:hover,
QPushButton#composerAttach:hover,
QPushButton#composerWorkspace:hover,
QPushButton#composerPermission:hover,
QPushButton#composerModel:hover {
    background:#1b1f2a;
    border-color:#414858;
    color:#f0f2f6;
}
QPushButton#composerControl:pressed,
QPushButton#composerAttach:pressed,
QPushButton#composerWorkspace:pressed,
QPushButton#composerPermission:pressed,
QPushButton#composerModel:pressed {
    background:#11141b;
    border-color:#353b49;
}
QPushButton#composerControl:disabled,
QPushButton#composerAttach:disabled,
QPushButton#composerWorkspace:disabled,
QPushButton#composerPermission:disabled,
QPushButton#composerModel:disabled {
    color:#5f6674;
    background:#12151c;
    border-color:#222733;
}

/* Attach is an action, but intentionally quiet beside Send. */
QPushButton#composerAttach {
    color:#bcc4d0;
    background:#151821;
    border-color:#2e3442;
}
QPushButton#composerAttach:hover {
    color:#eef2f8;
    background:#1b202a;
    border-color:#465164;
}

/* Workspace should feel structural rather than decorative. */
QPushButton#composerWorkspace {
    color:#c1c8d3;
    background:#161922;
    border-color:#303644;
}
QPushButton#composerWorkspace:hover {
    color:#f1f3f7;
    background:#1b1f29;
    border-color:#465062;
}

/* Permission colour is meaningful. Full access is the only deliberately warm
   state because it changes the safety boundary of the agent. */
QPushButton#composerPermission[mode="full-access"] {
    color:#f0cf88;
    background:#211a0c;
    border-color:#6c501c;
}
QPushButton#composerPermission[mode="full-access"]:hover {
    color:#ffe1a1;
    background:#2a210e;
    border-color:#8a6724;
}
QPushButton#composerPermission[mode="read-only"] {
    color:#a9cbe4;
    background:#141b22;
    border-color:#30495a;
}
QPushButton#composerPermission[mode="workspace"] {
    color:#c6bff4;
    background:#181725;
    border-color:#433c67;
}
QPushButton#composerPermission[mode="approval"] {
    color:#cbc2df;
    background:#191820;
    border-color:#40394a;
}

/* Model is the only cool accent in the row; keep it subtle enough that the
   active permission state remains easier to scan. */
QPushButton#composerModel {
    color:#c8c3ed;
    background:#181923;
    border-color:#343248;
}
QPushButton#composerModel:hover {
    color:#e6e1ff;
    background:#1e1e2b;
    border-color:#514d6f;
}

/* Usage is metadata, not another decision pill. */
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
    """Render crisp, font-independent composer icons with Qt primitives.

    Every glyph uses the same 16px optical box and round 1.4-ish stroke so the
    row stays visually coherent on Windows at fractional DPI scaling.
    """
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.setPen(
        QPen(
            QColor(color),
            1.42,
            Qt.PenStyle.SolidLine,
            Qt.PenCapStyle.RoundCap,
            Qt.PenJoinStyle.RoundJoin,
        )
    )
    c = size / 2.0

    if kind == "attach":
        # A real paperclip, not a plus sign. Two nested arcs make it recognisable
        # at 16 px without turning into a generic chain/link icon.
        path = QPainterPath()
        path.moveTo(10.9, 5.0)
        path.cubicTo(12.4, 6.5, 12.35, 8.6, 10.9, 10.05)
        path.lineTo(7.45, 13.5)
        path.cubicTo(5.7, 15.25, 2.85, 15.15, 1.25, 13.55)
        path.cubicTo(-0.35, 11.95, -0.45, 9.2, 1.35, 7.4)
        path.lineTo(6.75, 2.0)
        path.cubicTo(8.0, 0.75, 10.0, 0.8, 11.2, 2.0)
        path.cubicTo(12.4, 3.2, 12.4, 5.05, 11.2, 6.25)
        path.lineTo(5.85, 11.6)
        path.cubicTo(5.15, 12.3, 4.05, 12.25, 3.4, 11.6)
        path.cubicTo(2.75, 10.95, 2.75, 9.9, 3.45, 9.2)
        path.lineTo(8.35, 4.3)
        painter.drawPath(path)
    elif kind == "workspace":
        # Clean folder silhouette with a small tab. No internal divider: at this
        # size a second line made the old glyph feel busy and icon-font-like.
        path = QPainterPath()
        path.moveTo(2.35, 5.1)
        path.lineTo(6.05, 5.1)
        path.lineTo(7.25, 6.45)
        path.lineTo(13.45, 6.45)
        path.lineTo(13.45, 12.45)
        path.cubicTo(13.45, 13.0, 13.0, 13.45, 12.45, 13.45)
        path.lineTo(3.35, 13.45)
        path.cubicTo(2.8, 13.45, 2.35, 13.0, 2.35, 12.45)
        path.closeSubpath()
        painter.drawPath(path)
    elif kind == "permission":
        # Shield with a small centre keyhole. This reads as capability / access,
        # rather than the previous generic outline shield.
        shield = QPainterPath()
        shield.moveTo(c, 2.25)
        shield.cubicTo(9.2, 3.35, 10.55, 3.85, 12.0, 4.2)
        shield.lineTo(11.55, 8.7)
        shield.cubicTo(11.25, 10.95, 9.65, 12.65, c, 13.65)
        shield.cubicTo(6.35, 12.65, 4.75, 10.95, 4.45, 8.7)
        shield.lineTo(4.0, 4.2)
        shield.cubicTo(5.45, 3.85, 6.8, 3.35, c, 2.25)
        painter.drawPath(shield)
        painter.drawEllipse(QRectF(c - 0.9, 6.45, 1.8, 1.8))
        painter.drawLine(QPointF(c, 8.25), QPointF(c, 10.0))
    elif kind == "model":
        # Four-point model/spark mark: more distinctive than a plain diamond,
        # while still quiet enough for a utility control.
        outer = QPainterPath()
        outer.moveTo(c, 2.15)
        outer.cubicTo(8.55, 5.4, 10.05, 6.9, 13.25, c)
        outer.cubicTo(10.05, 9.1, 8.55, 10.6, c, 13.85)
        outer.cubicTo(7.45, 10.6, 5.95, 9.1, 2.75, c)
        outer.cubicTo(5.95, 6.9, 7.45, 5.4, c, 2.15)
        painter.drawPath(outer)
        painter.drawEllipse(QRectF(c - 0.95, c - 0.95, 1.9, 1.9))
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
    if hasattr(button, "_icon"):
        button._icon = ""
    button.setIcon(_composer_icon(kind, color))
    button.setIconSize(QSize(16, 16))
    button.setMinimumHeight(31)
    button.setMaximumHeight(31)
    if getattr(button, "_value", ""):
        button.setText(button._value)


def _polish_attach_button(panel: Any) -> None:
    """Upgrade an attachment control when the attachment feature is installed.

    Attachment support is intentionally optional in the desktop client. Some
    builds add the button in a later feature layer, so find it semantically
    instead of making composer_polish own attachment behaviour.
    """
    for button in panel.findChildren(QPushButton):
        name = button.objectName().casefold()
        label = " ".join(button.text().split()).casefold()
        if "attach" not in name and label not in {"attach", "+ attach"}:
            continue
        button.setObjectName("composerAttach")
        button.setText("Attach")
        _set_control_icon(button, "attach", "#aeb8c8")
        button.setToolTip(button.toolTip() or "Attach files or images")
        return


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

        # Give each decision a semantic selector and one consistent vector-icon
        # system. Colours are restrained, with the safety-sensitive permission
        # state carrying the strongest semantic emphasis.
        self.workspace_button.setObjectName("composerWorkspace")
        self.permission_button.setObjectName("composerPermission")
        self.model_button.setObjectName("composerModel")
        _set_control_icon(self.workspace_button, "workspace", "#aab5c7")
        _set_control_icon(self.permission_button, "permission", "#d3b466")
        _set_control_icon(self.model_button, "model", "#b6aef0")
        _polish_attach_button(self)

        self.usage_label.setObjectName("composerUsage")
        self.usage_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.usage_label.setTextFormat(Qt.TextFormat.PlainText)
        self.usage_label.setToolTip("Conversation token usage")

        self.send_button.setText("")
        self.send_button.setIcon(_composer_icon("send", "#ffffff"))
        self.send_button.setIconSize(QSize(16, 16))
        self.send_button.setFixedSize(34, 34)
        self.send_button.setToolTip("Send · Enter")

        # 8 px between utility controls is enough separation to scan each target,
        # but still lets the four buttons read as one compact control group.
        outer = self.layout()
        if outer is not None:
            outer.setContentsMargins(16, 12, 12, 10)
            outer.setSpacing(9)
            if outer.count() > 1:
                controls = outer.itemAt(1).layout()
                if controls is not None:
                    controls.setSpacing(8)

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


__all__ = ["install", "_compact_tokens", "_icon_pixmap"]
