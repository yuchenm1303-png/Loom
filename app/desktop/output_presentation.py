"""Flat Codex-style presentation for the main agent transcript.

The conversation should read as one continuous document, not a stack of cards.
User messages keep their compact outgoing bubble, while assistant prose and
inline Runtime activity are presented directly on the transcript background.
The Runtime inspector keeps its richer cards because those are useful there.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QWidget

from app.desktop import widgets as base
from app.desktop.message_presentation import (
    MessageWidget as AnimatedMessageWidget,
    TranscriptView as AnimatedTranscriptView,
)
from app.desktop.state import TranscriptEntry


_ASSISTANT_PLAIN_QSS = """
QFrame#assistantMessage {
    background: transparent;
    border: none;
    border-radius: 0;
}
QFrame#assistantMessage:hover {
    background: transparent;
    border: none;
}
QFrame#assistantMessage QLabel#messageBody {
    background: transparent;
    color: #e8ebf1;
}
"""


_ACTIVITY_PLAIN_QSS = """
QFrame#activityCard,
QFrame#activityCard:hover,
QFrame#activityCard[state="completed"],
QFrame#activityCard[state="running"],
QFrame#activityCard[state="started"],
QFrame#activityCard[state="waiting"],
QFrame#activityCard[state="waiting_approval"],
QFrame#activityCard[state="failed"],
QFrame#activityCard[state="denied"],
QFrame#activityCard[state="cancelled"] {
    background: transparent;
    border: none;
    border-radius: 0;
}
QLabel#cardTitle {
    background: transparent;
    color: #d9dde5;
    font-size: 13px;
    font-weight: 640;
}
QLabel#cardSubtitle {
    background: transparent;
    color: #737b89;
    font-size: 10px;
}
QFrame#cardBodyShell {
    background: transparent;
    border: none;
    border-radius: 0;
}
QLabel#cardBodyTitle {
    background: transparent;
    color: #747c89;
    border: none;
}
QPlainTextEdit#cardBody {
    background: transparent;
    border: none;
    color: #bbc1cb;
    padding: 2px 0 2px 0;
}
QPushButton#cardToggle {
    min-width: 24px;
    max-width: 24px;
    min-height: 24px;
    max-height: 24px;
    padding: 0;
    background: transparent;
    border: none;
    border-radius: 6px;
    color: #89919f;
    font-size: 15px;
    font-weight: 600;
}
QPushButton#cardToggle:hover {
    background: #12151b;
    border: none;
    color: #d8dce4;
}
QPushButton#cardToggle:pressed {
    background: #0f1217;
    border: none;
}
QLabel#cardStatus,
QLabel#cardStatus[state="completed"],
QLabel#cardStatus[state="failed"],
QLabel#cardStatus[state="denied"],
QLabel#cardStatus[state="cancelled"],
QLabel#cardStatus[state="waiting"],
QLabel#cardStatus[state="waiting_approval"],
QLabel#cardStatus[state="running"],
QLabel#cardStatus[state="started"] {
    background: transparent;
    border: none;
    border-radius: 0;
    padding: 0;
    font-size: 10px;
}
"""


class MessageWidget(AnimatedMessageWidget):
    """Assistant output reads as prose directly on the transcript canvas."""

    def __init__(self, role: str, parent: QWidget | None = None) -> None:
        super().__init__(role, parent)
        if role != "assistant":
            return

        self.setStyleSheet(_ASSISTANT_PLAIN_QSS)
        layout = self.layout()
        if layout is not None:
            # No card chrome means the prose should share the transcript's exact
            # reading edge instead of carrying the old card's inset.
            layout.setContentsMargins(0, 2, 0, 5)
            layout.setSpacing(5)


class FlatActivityCard(base.ActivityCard):
    """Inline tool/process row for the main transcript, without container boxes."""

    _ATTENTION = {"failed", "denied", "cancelled", "waiting", "waiting_approval"}

    def __init__(self, kind: str, parent: QWidget | None = None) -> None:
        super().__init__(kind, parent)
        self.setStyleSheet(_ACTIVITY_PLAIN_QSS)

        # The icon made every tool call read like a dashboard card. The title and
        # disclosure affordance are enough in a document-style transcript.
        self.icon.hide()
        self.body_title.hide()

        layout = self.layout()
        if layout is not None:
            layout.setContentsMargins(0, 4, 0, 4)
            layout.setSpacing(3)

        shell_layout = self.body_shell.layout()
        if shell_layout is not None:
            # Expanded output is indented, not boxed. This keeps command output
            # visually subordinate while matching the reference's plain flow.
            shell_layout.setContentsMargins(14, 5, 0, 7)
            shell_layout.setSpacing(4)

    def _sync_body(self, *, animate: bool = False) -> None:
        super()._sync_body(animate=animate)
        if not self.toggle_button.isVisible():
            return
        show = bool(self._body_text and self._expanded)
        self.toggle_button.setText("⌃" if show else "›")
        self.toggle_button.setToolTip("Hide output" if show else "Show output")

    def update_card(
        self,
        *,
        title: str,
        subtitle: str = "",
        status: str = "",
        body: str = "",
        auto_expand: bool | None = None,
    ) -> None:
        super().update_card(
            title=title,
            subtitle=subtitle,
            status=status,
            body=body,
            auto_expand=auto_expand,
        )
        # Normal completed/running rows do not need another visual badge. Keep a
        # small textual state only when the user may need to notice or act on it.
        self.status_label.setVisible(status in self._ATTENTION)
        self._sync_body()


class TranscriptView(AnimatedTranscriptView):
    """Main transcript with flat assistant prose and flat inline activity."""

    def _build(self, entry: TranscriptEntry) -> QWidget:
        if entry.kind in {"user", "assistant"}:
            return MessageWidget(entry.kind, self.canvas)

        # CardListView creates TranscriptView(max_content_width=0) for the Runtime
        # inspector. Leave those inspector cards intact; only the central agent
        # transcript is converted to the document-style presentation.
        if self._max_content_width and entry.kind in {"tool", "process", "diff", "error"}:
            return FlatActivityCard(entry.kind, self.canvas)
        return base.ActivityCard(entry.kind, self.canvas)


__all__ = ["FlatActivityCard", "MessageWidget", "TranscriptView"]
