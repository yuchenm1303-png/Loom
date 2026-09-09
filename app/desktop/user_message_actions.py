"""Compact actions for outgoing user messages.

User messages are already visually distinct bubbles.  This module adds the
small utility row that belongs *under* that bubble instead of painting controls
inside it: copy, copy-for-sharing, and edit-in-composer.

The shell is installed at the final transcript presentation layer so the keyed
reconciler still owns exactly one widget per transcript entry and all existing
streaming / ordering logic remains intact.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, QTimer
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QHBoxLayout, QPushButton, QSizePolicy, QVBoxLayout, QWidget

from app.desktop import output_presentation
from app.desktop import widgets as base
from app.desktop.state import TranscriptEntry


_INSTALLED = False

_ACTION_QSS = """
QWidget#userMessageActions {
    background: transparent;
}
QPushButton#userMessageAction {
    background: transparent;
    border: none;
    border-radius: 7px;
    padding: 0;
}
QPushButton#userMessageAction:hover {
    background: #171a20;
}
QPushButton#userMessageAction:pressed {
    background: #20242b;
}
"""


class UserMessageActionButton(QPushButton):
    """Small native vector action used beneath an outgoing message."""

    def __init__(self, kind: str, tooltip: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.kind = kind
        self.setObjectName("userMessageAction")
        self.setText("")
        self.setFlat(True)
        self.setFixedSize(27, 27)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setToolTip(tooltip)
        self.setAccessibleName(tooltip)

    def _color(self) -> QColor:
        if bool(self.property("success")) or self.isDown():
            return QColor("#d5d8de")
        if self.underMouse():
            return QColor("#c4c9d1")
        return QColor("#8b929d")

    def paintEvent(self, event: Any) -> None:  # noqa: N802 - Qt override
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.translate(self.width() / 2.0, self.height() / 2.0)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(
            QPen(
                self._color(),
                1.45,
                Qt.PenStyle.SolidLine,
                Qt.PenCapStyle.RoundCap,
                Qt.PenJoinStyle.RoundJoin,
            )
        )

        if bool(self.property("success")):
            painter.drawLine(QPointF(-4.5, 0.0), QPointF(-1.2, 3.2))
            painter.drawLine(QPointF(-1.2, 3.2), QPointF(4.8, -3.8))
            return

        if self.kind == "copy":
            painter.drawRoundedRect(QRectF(-5.5, -4.0, 7.7, 8.2), 1.45, 1.45)
            painter.drawRoundedRect(QRectF(-1.8, -6.0, 7.7, 8.2), 1.45, 1.45)
            return

        if self.kind == "share":
            # Standard share/export language: upward arrow emerging from a quiet
            # tray.  The tray is deliberately open at the top so it does not read
            # like another clipboard icon beside Copy.
            painter.drawLine(QPointF(0.0, 2.0), QPointF(0.0, -6.0))
            painter.drawLine(QPointF(0.0, -6.0), QPointF(-3.0, -3.0))
            painter.drawLine(QPointF(0.0, -6.0), QPointF(3.0, -3.0))
            tray = QPainterPath()
            tray.moveTo(QPointF(-5.2, 0.4))
            tray.lineTo(QPointF(-5.2, 4.4))
            tray.cubicTo(QPointF(-5.2, 5.3), QPointF(-4.5, 5.8), QPointF(-3.6, 5.8))
            tray.lineTo(QPointF(3.6, 5.8))
            tray.cubicTo(QPointF(4.5, 5.8), QPointF(5.2, 5.3), QPointF(5.2, 4.4))
            tray.lineTo(QPointF(5.2, 0.4))
            painter.drawPath(tray)
            return

        # Pencil/edit.  Two parallel strokes and a tiny cap read clearly at this
        # size without a filled icon that would be heavier than its neighbours.
        painter.save()
        painter.rotate(-43.0)
        painter.drawRoundedRect(QRectF(-1.7, -6.0, 3.4, 10.2), 0.8, 0.8)
        painter.drawLine(QPointF(-1.7, 2.0), QPointF(1.7, 2.0))
        tip = QPainterPath()
        tip.moveTo(QPointF(-1.7, 4.2))
        tip.lineTo(QPointF(0.0, 6.1))
        tip.lineTo(QPointF(1.7, 4.2))
        painter.drawPath(tip)
        painter.restore()


class UserMessageActionBar(QWidget):
    """Right-aligned actions shown immediately under one user bubble."""

    def __init__(self, shell: "UserMessageShell") -> None:
        super().__init__(shell)
        self.shell = shell
        self._text = ""
        self.setObjectName("userMessageActions")
        self.setStyleSheet(_ACTION_QSS)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(1)

        self.copy_button = UserMessageActionButton("copy", "Copy message", self)
        self.copy_button.clicked.connect(self._copy)
        row.addWidget(self.copy_button)

        self.share_button = UserMessageActionButton("share", "Copy for sharing", self)
        self.share_button.clicked.connect(self._share)
        row.addWidget(self.share_button)

        self.edit_button = UserMessageActionButton("edit", "Edit in composer", self)
        self.edit_button.clicked.connect(self._edit)
        row.addWidget(self.edit_button)

        self.setFixedHeight(28)
        self.hide()

    def set_text(self, text: str) -> None:
        self._text = str(text or "")
        self.setVisible(bool(self._text.strip()))
        self.adjustSize()

    def _flash_success(self, button: UserMessageActionButton, reset_tooltip: str) -> None:
        button.setProperty("success", True)
        button.setToolTip("Copied")
        button.update()

        def reset() -> None:
            button.setProperty("success", False)
            button.setToolTip(reset_tooltip)
            button.update()

        QTimer.singleShot(1100, reset)

    def _copy(self) -> None:
        if not self._text:
            return
        base.copy_to_clipboard(self._text)
        self._flash_success(self.copy_button, "Copy message")

    def _share(self) -> None:
        """Use the portable share contract: copy text and surface clear feedback.

        Qt has no cross-platform native share sheet on desktop. Copying keeps this
        action useful on Windows/macOS/Linux without pretending a system share UI
        exists where it does not.
        """
        if not self._text:
            return
        base.copy_to_clipboard(self._text)
        self._flash_success(self.share_button, "Copy for sharing")
        window = self.window()
        notify = getattr(window, "notify", None)
        if callable(notify):
            notify("Message copied — paste it anywhere to share")

    def _edit(self) -> None:
        if not self._text:
            return
        window = self.window()
        composer = getattr(window, "composer_panel", None)
        setter = getattr(composer, "set_text", None)
        if callable(setter):
            setter(self._text)
            editor = getattr(composer, "editor", None)
            if editor is not None:
                editor.setFocus()
            return
        notify = getattr(window, "notify", None)
        if callable(notify):
            notify("Composer is unavailable")


class UserMessageShell(QWidget):
    """One transcript row containing the purple bubble and its external actions."""

    MAX_WIDTH = 620

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("userMessageShell")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(3)

        self.message = output_presentation.MessageWidget("user", self)
        self.message.setMaximumWidth(self.MAX_WIDTH)
        message_policy = self.message.sizePolicy()
        message_policy.setHorizontalPolicy(QSizePolicy.Policy.Maximum)
        message_policy.setVerticalPolicy(QSizePolicy.Policy.Minimum)
        message_policy.setHeightForWidth(True)
        self.message.setSizePolicy(message_policy)
        layout.addWidget(self.message, 0, Qt.AlignmentFlag.AlignRight)

        self.actions = UserMessageActionBar(self)
        layout.addWidget(self.actions, 0, Qt.AlignmentFlag.AlignRight)

        shell_policy = QSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Minimum)
        shell_policy.setHeightForWidth(True)
        self.setSizePolicy(shell_policy)

    @property
    def _text(self) -> str:
        return str(getattr(self.message, "_text", "") or "")

    def set_entry(self, entry: TranscriptEntry) -> None:
        self.message.set_text(entry.text)
        self.message.set_streaming(entry.streaming)
        self.actions.set_text(entry.text)
        self.updateGeometry()

    def hasHeightForWidth(self) -> bool:  # noqa: N802 - Qt override
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802 - Qt override
        width = max(1, min(int(width), self.MAX_WIDTH))
        bubble = max(self.message.minimumSizeHint().height(), self.message.heightForWidth(width))
        actions = self.actions.sizeHint().height() if self.actions.isVisible() else 0
        return bubble + (3 if actions else 0) + actions

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt override
        message_hint = self.message.sizeHint()
        actions_hint = self.actions.sizeHint() if self.actions.isVisible() else QSize(0, 0)
        width = min(self.MAX_WIDTH, max(message_hint.width(), actions_hint.width()))
        return QSize(width, self.heightForWidth(width))


def install() -> None:
    """Install the user-message shell after all transcript presentation passes."""
    global _INSTALLED
    if _INSTALLED:
        return
    _INSTALLED = True

    view_cls = output_presentation.TranscriptView
    original_build = view_cls._build
    original_apply = view_cls._apply
    original_to_plain_text = view_cls.toPlainText

    def build(self: Any, entry: TranscriptEntry) -> QWidget:
        if entry.kind == "user":
            return UserMessageShell(self.canvas)
        return original_build(self, entry)

    def apply(self: Any, widget: QWidget, entry: TranscriptEntry) -> None:
        if isinstance(widget, UserMessageShell):
            widget.set_entry(entry)
            return
        original_apply(self, widget, entry)

    def to_plain_text(self: Any) -> str:
        # Preserve the old public transcript text surface now that user messages
        # live in a shell rather than being MessageWidget instances directly.
        if not any(isinstance(self._widgets.get(key), UserMessageShell) for key in self._order):
            return original_to_plain_text(self)

        parts: list[str] = []
        for key in self._order:
            widget = self._widgets.get(key)
            if isinstance(widget, UserMessageShell):
                parts.append(widget._text)
            elif isinstance(widget, base.MessageWidget):
                parts.append(str(getattr(widget, "_text", "") or ""))
            elif isinstance(widget, base.ActivityCard):
                parts.append(
                    "\n".join(
                        part
                        for part in (
                            str(getattr(widget, "_full_title", "") or ""),
                            widget.subtitle_label.text(),
                            str(getattr(widget, "_body_text", "") or ""),
                        )
                        if part
                    )
                )
        return "\n\n".join(parts)

    view_cls._build = build
    view_cls._apply = apply
    view_cls.toPlainText = to_plain_text


__all__ = [
    "UserMessageActionBar",
    "UserMessageActionButton",
    "UserMessageShell",
    "install",
]
