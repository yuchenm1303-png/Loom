"""Presentation polish for Loom's composer surface and action controls.

The composer is the one piece of chrome users touch on every turn, so its
controls should read as one deliberately designed system rather than a row of
unrelated pills. Behaviour stays in ``composer.py``; this module only refines
visual hierarchy, vector iconography, density, and state styling.

Composer icons deliberately use a ``QIconEngine`` instead of storing 16x16
pixmaps. Qt can therefore paint the original vector geometry directly into the
button at the target screen's device pixel ratio. This matters on Windows at
125%/150% scaling, where pre-rasterised 16px icons otherwise get interpolated
and look visibly softer than the neighbouring DirectWrite text.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, qVersion
from PySide6.QtGui import QColor, QIcon, QIconEngine, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QLabel, QPushButton

from app.desktop import theme


_INSTALLED = False


def _qt_version_tuple() -> tuple[int, int, int]:
    values: list[int] = []
    for part in qVersion().split(".")[:3]:
        digits = "".join(ch for ch in part if ch.isdigit())
        values.append(int(digits or 0))
    while len(values) < 3:
        values.append(0)
    return values[0], values[1], values[2]


# Qt fixed QIcon::pixmap(size, dpr) in 6.8. Before then, scaledPixmap() was
# handed a device-dependent size; from 6.8 onward it receives logical pixels.
_QT_SCALED_PIXMAP_SIZE_IS_PHYSICAL = _qt_version_tuple() < (6, 8, 0)

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

QLabel#composerUsage {
    min-height:27px;
    max-height:27px;
    padding:0 10px 0 25px;
    color:#959faf;
    background:#11151d;
    border:1px solid #272e3a;
    border-radius:9px;
    font-size:10px;
    font-weight:600;
}
QLabel#composerUsage:hover {
    color:#c0c7d2;
    background:#141922;
    border-color:#343c4a;
}

QPushButton#sendButton {
    min-width:32px;
    max-width:32px;
    min-height:32px;
    max-height:32px;
    padding:0;
    margin:0;
    border-radius:10px;
    background:qlineargradient(
        x1:0,y1:0,x2:0,y2:1,
        stop:0 #7d72e8,
        stop:1 #675bd3
    );
    border:1px solid #8d84e8;
    color:#ffffff;
}
QPushButton#sendButton:hover {
    background:qlineargradient(
        x1:0,y1:0,x2:0,y2:1,
        stop:0 #8b81ef,
        stop:1 #7468dc
    );
    border-color:#a49cf0;
}
QPushButton#sendButton:pressed {
    background:#6055c8;
    border-color:#8178df;
}
QPushButton#sendButton:disabled {
    background:#181b24;
    border-color:#292e3a;
    color:#59616f;
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


class UsageBadge(QLabel):
    """Quiet token metadata with a native context-ring glyph."""

    def __init__(self, parent: Any = None) -> None:
        super().__init__(parent)
        self.setObjectName("composerUsage")
        self.setTextFormat(Qt.TextFormat.PlainText)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setToolTip("Conversation token usage")
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)

    def paintEvent(self, event: Any) -> None:  # noqa: N802 - Qt override
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(9.0, self.height() / 2.0 - 3.7, 7.4, 7.4)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor("#4f586b"), 1.05, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.drawEllipse(rect)
        painter.setPen(QPen(QColor("#8d84df"), 1.35, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.drawArc(rect, 38 * 16, 155 * 16)
        painter.end()


def _paint_composer_icon(painter: QPainter, rect: QRectF, kind: str, color: str) -> None:
    """Paint one glyph once, at the final paint device resolution."""
    side = min(float(rect.width()), float(rect.height()))
    if side <= 0.0:
        return
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.translate(rect.center().x() - side / 2.0, rect.center().y() - side / 2.0)
    painter.scale(side / 16.0, side / 16.0)
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
    c = 8.0

    if kind == "attach":
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
                1.55,
                Qt.PenStyle.SolidLine,
                Qt.PenCapStyle.RoundCap,
                Qt.PenJoinStyle.RoundJoin,
            )
        )
        painter.drawLine(QPointF(c, 11.8), QPointF(c, 4.25))
        painter.drawLine(QPointF(c, 4.25), QPointF(4.95, 7.05))
        painter.drawLine(QPointF(c, 4.25), QPointF(11.05, 7.05))
    painter.restore()


def _render_icon_pixmap(
    kind: str,
    color: str,
    logical_size: QSize,
    *,
    device_pixel_ratio: float = 1.0,
    physical_size: QSize | None = None,
) -> QPixmap:
    """DPR-aware fallback for APIs that explicitly request a pixmap."""
    dpr = max(1.0, float(device_pixel_ratio or 1.0))
    logical_width = max(1, int(logical_size.width()))
    logical_height = max(1, int(logical_size.height()))
    physical = physical_size or QSize(
        max(1, int(round(logical_width * dpr))),
        max(1, int(round(logical_height * dpr))),
    )
    pixmap = QPixmap(physical)
    pixmap.fill(Qt.GlobalColor.transparent)
    pixmap.setDevicePixelRatio(dpr)
    painter = QPainter(pixmap)
    _paint_composer_icon(
        painter,
        QRectF(0.0, 0.0, float(logical_width), float(logical_height)),
        kind,
        color,
    )
    painter.end()
    return pixmap


def _scaled_pixmap_geometry(size: QSize, scale: float) -> tuple[QSize, QSize]:
    """Return (logical, physical) sizes for Qt 6.7 and Qt 6.8+ contracts."""
    dpr = max(1.0, float(scale or 1.0))
    if _QT_SCALED_PIXMAP_SIZE_IS_PHYSICAL:
        physical = QSize(max(1, size.width()), max(1, size.height()))
        logical = QSize(
            max(1, int(round(physical.width() / dpr))),
            max(1, int(round(physical.height() / dpr))),
        )
        return logical, physical
    logical = QSize(max(1, size.width()), max(1, size.height()))
    physical = QSize(
        max(1, int(round(logical.width() * dpr))),
        max(1, int(round(logical.height() * dpr))),
    )
    return logical, physical


class _ComposerIconEngine(QIconEngine):
    """Vector-backed icon engine that stays sharp across screen DPR changes."""

    def __init__(self, kind: str, normal: str, disabled: str = "#5b6270") -> None:
        super().__init__()
        self.kind = kind
        self.normal = normal
        self.disabled = disabled

    def clone(self) -> "_ComposerIconEngine":
        return _ComposerIconEngine(self.kind, self.normal, self.disabled)

    def _color(self, mode: QIcon.Mode) -> str:
        return self.disabled if mode == QIcon.Mode.Disabled else self.normal

    def paint(self, painter: QPainter, rect: Any, mode: QIcon.Mode, state: QIcon.State) -> None:
        del state
        _paint_composer_icon(painter, QRectF(rect), self.kind, self._color(mode))

    def pixmap(self, size: QSize, mode: QIcon.Mode, state: QIcon.State) -> QPixmap:
        del state
        return _render_icon_pixmap(self.kind, self._color(mode), size)

    def scaledPixmap(
        self,
        size: QSize,
        mode: QIcon.Mode,
        state: QIcon.State,
        scale: float,
    ) -> QPixmap:  # noqa: N802 - Qt virtual name
        del state
        logical, physical = _scaled_pixmap_geometry(size, scale)
        return _render_icon_pixmap(
            self.kind,
            self._color(mode),
            logical,
            device_pixel_ratio=scale,
            physical_size=physical,
        )


def _icon_pixmap(
    kind: str,
    color: str,
    *,
    size: int = 16,
    device_pixel_ratio: float = 1.0,
) -> QPixmap:
    """Compatibility/testing helper; production buttons use ``QIconEngine``."""
    return _render_icon_pixmap(
        kind,
        color,
        QSize(size, size),
        device_pixel_ratio=device_pixel_ratio,
    )


def _composer_icon(kind: str, normal: str, disabled: str = "#5b6270") -> QIcon:
    # No pre-rendered pixmaps: the icon engine can repaint after the Loom window
    # moves between monitors with different scale factors.
    return QIcon(_ComposerIconEngine(kind, normal, disabled))


def _set_control_icon(button: Any, kind: str, color: str) -> None:
    if hasattr(button, "_icon"):
        button._icon = ""
    button.setIcon(_composer_icon(kind, color))
    button.setIconSize(QSize(16, 16))
    button.setMinimumHeight(31)
    button.setMaximumHeight(31)
    if getattr(button, "_value", ""):
        button.setText(button._value)


def _polish_attach_button(panel: Any) -> None:
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


def _replace_usage_badge(panel: Any, controls: Any) -> UsageBadge:
    old = panel.usage_label
    index = controls.indexOf(old)
    badge = UsageBadge(panel)
    badge.setText(old.text())
    badge.setToolTip(old.toolTip() or "Conversation token usage")
    badge.setVisible(old.isVisible())
    controls.removeWidget(old)
    old.hide()
    old.deleteLater()
    if index >= 0:
        controls.insertWidget(index, badge, 0, Qt.AlignmentFlag.AlignVCenter)
    else:
        controls.addWidget(badge, 0, Qt.AlignmentFlag.AlignVCenter)
    panel.usage_label = badge
    return badge


def install() -> None:
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
        self.workspace_button.setObjectName("composerWorkspace")
        self.permission_button.setObjectName("composerPermission")
        self.model_button.setObjectName("composerModel")
        _set_control_icon(self.workspace_button, "workspace", "#aab5c7")
        _set_control_icon(self.permission_button, "permission", "#d3b466")
        _set_control_icon(self.model_button, "model", "#b6aef0")
        _polish_attach_button(self)

        self.send_button.setText("")
        self.send_button.setIcon(_composer_icon("send", "#ffffff"))
        self.send_button.setIconSize(QSize(15, 15))
        self.send_button.setFixedSize(32, 32)
        self.send_button.setToolTip("Send · Enter")

        outer = self.layout()
        if outer is not None:
            outer.setContentsMargins(16, 12, 12, 12)
            outer.setSpacing(9)
            if outer.count() > 1:
                controls = outer.itemAt(1).layout()
                if controls is not None:
                    _replace_usage_badge(self, controls)
                    controls.setSpacing(8)
                    controls.setContentsMargins(0, 4, 0, 4)
                    controls.setAlignment(self.send_button, Qt.AlignmentFlag.AlignVCenter)
                    controls.setAlignment(self.usage_label, Qt.AlignmentFlag.AlignVCenter)
            outer.invalidate()
            outer.activate()
            self.updateGeometry()
        else:
            self.usage_label.setObjectName("composerUsage")
            self.usage_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.usage_label.setTextFormat(Qt.TextFormat.PlainText)
            self.usage_label.setToolTip("Conversation token usage")

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


__all__ = [
    "UsageBadge",
    "_ComposerIconEngine",
    "_compact_tokens",
    "_composer_icon",
    "_icon_pixmap",
    "_paint_composer_icon",
    "_scaled_pixmap_geometry",
    "install",
]
