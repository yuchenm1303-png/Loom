"""Quiet post-response actions for completed assistant messages.

The transcript stays visually clean while Loom is working. Once an assistant
reply is complete, a compact footer appears with copy, negative-feedback and
focused-preview actions plus the response time.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app.desktop import message_presentation as presentation
from app.desktop import theme
from app.desktop import widgets as base


_INSTALLED = False

_ACTION_QSS = """
QWidget#messageActions {
    background: transparent;
}
QPushButton#messageAction {
    background: transparent;
    border: none;
    border-radius: 6px;
    padding: 0;
}
QPushButton#messageAction:hover {
    background: #181b21;
}
QPushButton#messageAction:pressed,
QPushButton#messageAction:checked {
    background: #20242c;
}
QLabel#messageActionTime {
    background: transparent;
    color: #747b87;
    font-size: 11px;
    padding-left: 4px;
}
"""

_PREVIEW_QSS = f"""
QDialog#messagePreview {{
    background: #101218;
}}
QPlainTextEdit#messagePreviewText {{
    background: #151820;
    border: 1px solid #2b303a;
    border-radius: 10px;
    color: #e5e8ee;
    font-family: {theme.FONT_UI};
    font-size: 13px;
    padding: 12px;
    selection-background-color: #343946;
}}
"""


def _format_time(value: Any) -> str:
    if value in (None, ""):
        return datetime.now().strftime("%H:%M")

    if isinstance(value, (int, float)):
        stamp = float(value)
        if stamp > 10_000_000_000:
            stamp /= 1000.0
        try:
            return datetime.fromtimestamp(stamp).strftime("%H:%M")
        except (OverflowError, OSError, ValueError):
            return datetime.now().strftime("%H:%M")

    text = str(value).strip()
    if not text:
        return datetime.now().strftime("%H:%M")
    if len(text) >= 5 and text[2] == ":" and text[:2].isdigit() and text[3:5].isdigit():
        return text[:5]

    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone()
        return parsed.strftime("%H:%M")
    except ValueError:
        return datetime.now().strftime("%H:%M")


class MessageActionButton(QPushButton):
    """Small font-independent icon button matching the transcript's quiet chrome."""

    def __init__(self, kind: str, tooltip: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.kind = kind
        self.setObjectName("messageAction")
        self.setText("")
        self.setFlat(True)
        self.setFixedSize(28, 26)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setToolTip(tooltip)
        self.setAccessibleName(tooltip)

    def paintEvent(self, event: Any) -> None:  # noqa: N802 - Qt override
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.translate(self.width() / 2.0, self.height() / 2.0)

        active = self.isChecked() or bool(self.property("success"))
        if self.isDown() or active:
            color = QColor("#c4c9d2")
        elif self.underMouse():
            color = QColor("#a7aeb9")
        else:
            color = QColor("#737b88")
        painter.setPen(
            QPen(
                color,
                1.35,
                Qt.PenStyle.SolidLine,
                Qt.PenCapStyle.RoundCap,
                Qt.PenJoinStyle.RoundJoin,
            )
        )
        painter.setBrush(Qt.BrushStyle.NoBrush)

        if self.kind == "copy":
            if bool(self.property("success")):
                painter.drawLine(QPointF(-4.0, 0.0), QPointF(-1.0, 3.0))
                painter.drawLine(QPointF(-1.0, 3.0), QPointF(4.5, -3.5))
                return
            painter.drawRoundedRect(QRectF(-5.0, -4.0, 7.5, 8.5), 1.5, 1.5)
            painter.drawRoundedRect(QRectF(-1.5, -6.0, 7.5, 8.5), 1.5, 1.5)
            return

        if self.kind == "dislike":
            path = QPainterPath()
            path.moveTo(QPointF(-5.5, -4.0))
            path.lineTo(QPointF(2.0, -4.0))
            path.cubicTo(QPointF(4.4, -4.0), QPointF(5.2, -2.3), QPointF(4.4, -0.5))
            path.lineTo(QPointF(1.8, 5.0))
            path.cubicTo(QPointF(1.2, 6.1), QPointF(-0.5, 5.6), QPointF(-0.4, 4.2))
            path.lineTo(QPointF(-0.1, 1.0))
            path.lineTo(QPointF(-5.5, 1.0))
            path.closeSubpath()
            painter.drawPath(path)
            painter.drawLine(QPointF(-5.5, -4.0), QPointF(-5.5, 1.0))
            return

        # Focus/open: two opposing corner arrows, compact enough to read at 14 px.
        painter.drawLine(QPointF(-4.8, 4.8), QPointF(4.2, -4.2))
        painter.drawLine(QPointF(0.8, -4.2), QPointF(4.2, -4.2))
        painter.drawLine(QPointF(4.2, -4.2), QPointF(4.2, -0.8))
        painter.drawLine(QPointF(-4.8, 1.4), QPointF(-4.8, 4.8))
        painter.drawLine(QPointF(-4.8, 4.8), QPointF(-1.4, 4.8))


class MessageActionBar(QWidget):
    def __init__(self, message: Any) -> None:
        super().__init__(message)
        self.message = message
        self.setObjectName("messageActions")
        self.setStyleSheet(_ACTION_QSS)
        self._timestamp_explicit = False

        row = QHBoxLayout(self)
        row.setContentsMargins(0, 1, 0, 0)
        row.setSpacing(2)

        self.copy_button = MessageActionButton("copy", "Copy response", self)
        self.copy_button.clicked.connect(self._copy)
        row.addWidget(self.copy_button)

        self.dislike_button = MessageActionButton("dislike", "Not helpful", self)
        self.dislike_button.setCheckable(True)
        row.addWidget(self.dislike_button)

        self.open_button = MessageActionButton("open", "Open response", self)
        self.open_button.clicked.connect(self._open_preview)
        row.addWidget(self.open_button)

        self.time_label = QLabel("", self)
        self.time_label.setObjectName("messageActionTime")
        row.addWidget(self.time_label, 0, Qt.AlignmentFlag.AlignVCenter)
        row.addStretch(1)
        self.hide()

    def set_timestamp(self, value: Any, *, explicit: bool = True) -> None:
        if self._timestamp_explicit and not explicit:
            return
        self.time_label.setText(_format_time(value))
        if explicit and value not in (None, ""):
            self._timestamp_explicit = True

    def sync(self) -> None:
        completed = (
            getattr(self.message, "role", "") == "assistant"
            and bool(getattr(self.message, "_text", "").strip())
            and not bool(getattr(self.message, "_streaming", False))
        )
        if completed and not self.time_label.text():
            self.set_timestamp(None, explicit=False)
        self.setVisible(completed)

    def _copy(self) -> None:
        text = str(getattr(self.message, "_text", "") or "")
        if not text:
            return
        base.copy_to_clipboard(text)
        self.copy_button.setProperty("success", True)
        self.copy_button.setToolTip("Copied")
        self.copy_button.update()

        def reset() -> None:
            self.copy_button.setProperty("success", False)
            self.copy_button.setToolTip("Copy response")
            self.copy_button.update()

        QTimer.singleShot(1200, reset)

    def _open_preview(self) -> None:
        dialog = QDialog(self.message)
        dialog.setObjectName("messagePreview")
        dialog.setWindowTitle("Response")
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        dialog.resize(760, 560)
        dialog.setStyleSheet(_PREVIEW_QSS)

        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(14, 14, 14, 14)
        editor = QPlainTextEdit(dialog)
        editor.setObjectName("messagePreviewText")
        editor.setReadOnly(True)
        editor.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        editor.setPlainText(str(getattr(self.message, "_text", "") or ""))
        layout.addWidget(editor)
        dialog.show()


def install() -> None:
    """Attach the footer to the existing message presentation without changing Runtime cards."""
    global _INSTALLED
    if _INSTALLED:
        return
    _INSTALLED = True

    original_init = presentation.MessageWidget.__init__
    original_set_text = presentation.MessageWidget.set_text
    original_set_streaming = presentation.MessageWidget.set_streaming
    original_render = presentation.TranscriptView.render

    def message_init(self: Any, role: str, parent: QWidget | None = None) -> None:
        original_init(self, role, parent)
        self.message_actions = MessageActionBar(self) if role == "assistant" else None
        if self.message_actions is not None and self.layout() is not None:
            self.layout().addWidget(
                self.message_actions,
                0,
                Qt.AlignmentFlag.AlignLeft,
            )

    def set_text(self: Any, value: str) -> None:
        original_set_text(self, value)
        if self.message_actions is not None:
            self.message_actions.sync()

    def set_streaming(self: Any, streaming: bool) -> None:
        original_set_streaming(self, streaming)
        if self.message_actions is not None:
            self.message_actions.sync()

    def set_message_timestamp(self: Any, value: Any) -> None:
        if self.message_actions is not None:
            self.message_actions.set_timestamp(value, explicit=True)
            self.message_actions.sync()

    def render(self: Any, entries: list[Any]) -> None:
        original_render(self, entries)
        for entry in entries:
            if getattr(entry, "kind", "") != "assistant":
                continue
            widget = self._widgets.get(entry.key)
            if not isinstance(widget, presentation.MessageWidget):
                continue
            item = getattr(entry, "item", {}) or {}
            widget.set_message_timestamp(item.get("updatedAt") or item.get("createdAt"))

    presentation.MessageWidget.__init__ = message_init
    presentation.MessageWidget.set_text = set_text
    presentation.MessageWidget.set_streaming = set_streaming
    presentation.MessageWidget.set_message_timestamp = set_message_timestamp
    presentation.TranscriptView.render = render


__all__ = ["MessageActionBar", "MessageActionButton", "install"]
