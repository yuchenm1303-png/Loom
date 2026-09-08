"""Polished presentation for conversation messages.

The durable transcript widgets own behavior; this module only changes how chat
messages are presented.  In particular, sent messages should read like normal
chat bubbles rather than identity/profile cards.

Streaming is deliberately subtle: Loom shows a small thinking wave before the
first token, switches to a quiet live cursor once text is arriving, fades that
cursor away when the response completes, and updates the active Markdown block
in place so token deltas do not make the whole message flicker.
"""

from __future__ import annotations

import math
from typing import Any

from PySide6.QtCore import QEasingCurve, QPropertyAnimation, QRectF, QTimer, Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QWidget,
)

from app.desktop import markdown, theme
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

_STREAM_QSS = """
QWidget#streamStatus {
    background:transparent;
}
QLabel#streamStateLabel {
    background:transparent;
    color:#8d88bd;
    font-size:11px;
    font-weight:600;
}
"""


class StreamGlyph(QWidget):
    """Tiny native animation used for thinking and live generation.

    It paints its own geometry instead of relying on a font glyph, which keeps
    the animation crisp at any DPI and avoids re-laying out the message on every
    frame.
    """

    THINKING = "thinking"
    WRITING = "writing"

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._mode = self.THINKING
        self._phase = 0.0
        self._active = False
        self.setFixedSize(28, 12)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

        self._timer = QTimer(self)
        self._timer.setInterval(54)
        self._timer.timeout.connect(self._advance)

    @property
    def mode(self) -> str:
        return self._mode

    def set_mode(self, mode: str) -> None:
        mode = self.WRITING if mode == self.WRITING else self.THINKING
        if mode == self._mode:
            return
        self._mode = mode
        self.setFixedWidth(7 if mode == self.WRITING else 28)
        self._phase = 0.0
        self.updateGeometry()
        self.update()

    def set_active(self, active: bool) -> None:
        active = bool(active)
        self._active = active
        if active and theme.motion_enabled():
            if not self._timer.isActive():
                self._timer.start()
        else:
            self._timer.stop()
        self.update()

    def kick(self) -> None:
        """Make the live cursor react immediately to a newly arrived text delta."""
        if self._mode == self.WRITING:
            self._phase = 0.18
            self.update()

    def _advance(self) -> None:
        self._phase = (self._phase + 0.085) % 1.0
        self.update()

    def paintEvent(self, _event: Any) -> None:  # noqa: N802 - Qt override
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)

        if self._mode == self.THINKING:
            centers = (4, 12, 20)
            for index, x in enumerate(centers):
                wave = (math.sin((self._phase * math.tau) - index * 0.9) + 1.0) / 2.0
                color = QColor(theme.ACCENT_SOFT)
                color.setAlpha(int(72 + 168 * wave) if self._active else 112)
                painter.setBrush(color)
                painter.drawEllipse(x, 4, 4, 4)
            return

        wave = (math.sin(self._phase * math.tau) + 1.0) / 2.0
        color = QColor(theme.ACCENT_SOFT)
        color.setAlpha(int(92 + 150 * wave) if self._active else 138)
        painter.setBrush(color)
        painter.drawRoundedRect(QRectF(2, 1, 2, 10), 1, 1)


class StreamingStatus(QWidget):
    """One stable status row that transitions from thinking to a live cursor."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("streamStatus")
        self.setStyleSheet(_STREAM_QSS)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(1, 0, 0, 0)
        layout.setSpacing(7)

        self.glyph = StreamGlyph(self)
        layout.addWidget(self.glyph, 0, Qt.AlignmentFlag.AlignVCenter)

        self.label = QLabel("Thinking", self)
        self.label.setObjectName("streamStateLabel")
        layout.addWidget(self.label, 0, Qt.AlignmentFlag.AlignVCenter)
        layout.addStretch(1)

        self._mode = StreamGlyph.THINKING
        self._active = False
        self._fade: QPropertyAnimation | None = None
        self.hide()

    @property
    def mode(self) -> str:
        return self._mode

    def set_mode(self, mode: str) -> None:
        mode = StreamGlyph.WRITING if mode == StreamGlyph.WRITING else StreamGlyph.THINKING
        if mode == self._mode:
            return
        self._mode = mode
        self.glyph.set_mode(mode)
        self.label.setVisible(mode == StreamGlyph.THINKING)
        self.updateGeometry()

    def kick(self) -> None:
        self.glyph.kick()

    def set_active(self, active: bool) -> None:
        active = bool(active)
        if active == self._active:
            self.glyph.set_active(active)
            return
        self._active = active

        if self._fade is not None:
            self._fade.stop()
            self._fade = None
        self.setGraphicsEffect(None)

        if active:
            self.show()
            self.glyph.set_active(True)
            if not theme.motion_enabled():
                return
            effect = QGraphicsOpacityEffect(self)
            self.setGraphicsEffect(effect)
            effect.setOpacity(0.0)
            animation = QPropertyAnimation(effect, b"opacity", self)
            animation.setDuration(theme.MOTION_FAST_MS)
            animation.setStartValue(0.0)
            animation.setEndValue(1.0)
            animation.setEasingCurve(QEasingCurve.Type.OutCubic)

            def finish_in() -> None:
                if self._active:
                    self.setGraphicsEffect(None)
                self._fade = None

            animation.finished.connect(finish_in)
            self._fade = animation
            animation.start()
            return

        self.glyph.set_active(False)
        if not self.isVisible() or not theme.motion_enabled():
            self.hide()
            return

        effect = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(effect)
        effect.setOpacity(1.0)
        animation = QPropertyAnimation(effect, b"opacity", self)
        animation.setDuration(theme.MOTION_FAST_MS)
        animation.setStartValue(1.0)
        animation.setEndValue(0.0)
        animation.setEasingCurve(QEasingCurve.Type.OutCubic)

        def finish_out() -> None:
            if not self._active:
                self.hide()
            self.setGraphicsEffect(None)
            self._fade = None

        animation.finished.connect(finish_out)
        self._fade = animation
        animation.start()


class MessageWidget(base.MessageWidget):
    """Message without avatar/name chrome and with stable live-stream rendering."""

    def __init__(self, role: str, parent: QWidget | None = None) -> None:
        super().__init__(role, parent)
        self._streaming = False

        # Conversation roles are already implied by position. Keeping an avatar
        # and a name above every line made short messages look like profile cards.
        self.role_mark.hide()
        self.role_label.hide()

        # The old "Thinking" pill lives in the header row. Replace it with a
        # dedicated status row below the content so the message body never shifts
        # sideways when generation starts or stops.
        self.stream_badge.hide()
        self.stream_badge.set_pulsing(False)

        # Message-level copy chrome caused the header row to reappear on hover.
        # Code blocks keep their own copy action, while normal messages stay still.
        self.copy_button.hide()

        layout = self.layout()
        self.stream_status = StreamingStatus(self)
        if layout is not None:
            if role == "user":
                layout.setContentsMargins(14, 9, 14, 10)
                layout.setSpacing(0)
            else:
                layout.setContentsMargins(15, 8, 17, 12)
                layout.setSpacing(5)
                layout.addWidget(
                    self.stream_status,
                    0,
                    Qt.AlignmentFlag.AlignLeft,
                )

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
            self._streaming = False
            self.stream_status.set_active(False)
            self.stream_badge.hide()
            self.stream_badge.set_pulsing(False)
            return

        self._streaming = bool(streaming)
        self._sync_stream_status()

    def _sync_stream_status(self) -> None:
        if self.role != "assistant":
            return
        mode = StreamGlyph.WRITING if self._text.strip() else StreamGlyph.THINKING
        self.stream_status.set_mode(mode)
        self.stream_status.set_active(self._streaming)

    def set_text(self, value: str) -> None:
        """Update streaming Markdown in place instead of rebuilding every token.

        The base widget recreates the first changed block. During a stream the
        last prose/code block changes on almost every delta, which causes tiny
        flashes and unnecessary layout churn. Reuse one changed block when its
        type stays the same, then only create genuinely new blocks.
        """
        if value == self._text:
            self._sync_stream_status()
            return

        self._text = value
        blocks = markdown.parse_blocks(value) if value else []

        shared = 0
        for old, new in zip(self._blocks, blocks):
            if old != new:
                break
            shared += 1

        # The normal streaming case is "all old blocks are identical except the
        # active tail block". Updating that widget in place makes token delivery
        # visually continuous. It is also safe for larger edits because only one
        # same-kind block is reused; everything after it is reconciled normally.
        if shared < len(self._blocks) and shared < len(blocks):
            old = self._blocks[shared]
            new = blocks[shared]
            widget = self._widgets[shared]
            if old.kind == new.kind == "rich" and isinstance(widget, base.RichLabel):
                widget.set_markup(new.html)
                shared += 1
            elif old.kind == new.kind == "code" and isinstance(widget, base.CodeBlock):
                widget.set_source(new.language, new.source)
                shared += 1

        while len(self._widgets) > shared:
            widget = self._widgets.pop()
            self.body_layout.removeWidget(widget)
            widget.setParent(None)
            widget.deleteLater()

        for block in blocks[shared:]:
            if block.kind == "code":
                widget = base.CodeBlock(block.language, block.source, self)
            else:
                widget = base.RichLabel(self)
                widget.set_markup(block.html)
            self.body_layout.addWidget(widget)
            self._widgets.append(widget)

            # New paragraph/code blocks earn one quiet reveal. Existing blocks
            # are never faded on token deltas, so streaming text stays crisp.
            if self.role == "assistant" and self._streaming and theme.motion_enabled():
                base.fade_in(widget, duration_ms=theme.MOTION_FAST_MS)

        self._blocks = blocks
        if self._streaming and value:
            self.stream_status.kick()
        self._sync_stream_status()


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


__all__ = [
    "MessageWidget",
    "StreamGlyph",
    "StreamingStatus",
    "TranscriptView",
]
