"""Polished presentation for conversation messages.

The durable transcript widgets own behavior; this module only changes how chat
messages are presented.  In particular, sent messages should read like normal
chat bubbles rather than identity/profile cards.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtWidgets import QFrame, QSizePolicy, QWidget

from app.desktop import widgets as base
from app.desktop.state import TranscriptEntry


_USER_BUBBLE_QSS = """
QFrame#userMessage {
    background:qlineargradient(
        x1:0, y1:0, x2:1, y2:1,
        stop:0 #1b192e,
        stop:1 #171626
    );
    border:1px solid #343053;
    border-radius:14px;
}
QFrame#userMessage:hover {
    background:qlineargradient(
        x1:0, y1:0, x2:1, y2:1,
        stop:0 #1e1c33,
        stop:1 #1a182b
    );
    border-color:#413b64;
}
QFrame#userMessage QLabel#messageBody {
    background:transparent;
    color:#f0eef8;
}
"""


class MessageWidget(base.MessageWidget):
    """Message without avatar/name chrome; user messages become compact bubbles."""

    def __init__(self, role: str, parent: QWidget | None = None) -> None:
        super().__init__(role, parent)

        # Conversation roles are already implied by position.  Keeping an avatar
        # and a name above every line made short messages look like profile cards.
        self.role_mark.hide()
        self.role_label.hide()

        # Message-level copy chrome caused the header row to reappear on hover.
        # Code blocks keep their own copy action, while normal messages stay still.
        self.copy_button.hide()

        layout = self.layout()
        if layout is not None:
            if role == "user":
                layout.setContentsMargins(14, 9, 14, 10)
                layout.setSpacing(0)
            else:
                # Assistant messages retain a little room for the live Thinking
                # badge, but no longer reserve a profile/header band.
                layout.setContentsMargins(15, 8, 17, 12)
                layout.setSpacing(5)

        if role == "user":
            self.setStyleSheet(_USER_BUBBLE_QSS)

    def enterEvent(self, event: Any) -> None:  # noqa: N802 - Qt override
        self.copy_button.hide()
        QFrame.enterEvent(self, event)

    def leaveEvent(self, event: Any) -> None:  # noqa: N802 - Qt override
        self.copy_button.hide()
        QFrame.leaveEvent(self, event)

    def set_streaming(self, streaming: bool) -> None:
        # The optimistic copy of a sent user message is not "thinking".
        if self.role == "user":
            self.stream_badge.hide()
            self.stream_badge.set_pulsing(False)
            return
        super().set_streaming(streaming)


class TranscriptView(base.TranscriptView):
    """Transcript whose outgoing bubbles fit their content instead of a card width."""

    USER_MAX_WIDTH = 620

    def _build(self, entry: TranscriptEntry) -> QWidget:
        if entry.kind in {"user", "assistant"}:
            return MessageWidget(entry.kind, self.canvas)
        return super()._build(entry)

    def render(self, entries: list[TranscriptEntry]) -> None:
        super().render(entries)

        # The legacy transcript enforced a 280px minimum width for user cards.
        # Reset that after reconciliation so "你好" and other short messages are
        # genuinely compact, while long messages still wrap at a readable width.
        for entry in entries:
            if entry.kind != "user":
                continue
            widget = self._widgets.get(entry.key)
            if not isinstance(widget, MessageWidget):
                continue
            widget.setMinimumWidth(0)
            widget.setMaximumWidth(self.USER_MAX_WIDTH)
            policy = widget.sizePolicy()
            policy.setHorizontalPolicy(QSizePolicy.Policy.Maximum)
            policy.setVerticalPolicy(QSizePolicy.Policy.Minimum)
            policy.setHeightForWidth(True)
            widget.setSizePolicy(policy)
            widget.updateGeometry()


__all__ = ["MessageWidget", "TranscriptView"]
