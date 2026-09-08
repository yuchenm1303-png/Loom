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
import time
from typing import Any

from PySide6.QtCore import (
    Property,
    QEasingCurve,
    QParallelAnimationGroup,
    QPointF,
    QPropertyAnimation,
    QRectF,
    QSize,
    QTimer,
    Qt,
)
from PySide6.QtGui import QColor, QPainter, QPen, QTextDocument
from PySide6.QtWidgets import (
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from app.desktop import format as fmt
from app.desktop import markdown, theme
from app.desktop import widgets as base
from app.desktop.state import TranscriptEntry


_USER_BUBBLE_QSS = """
QFrame#userMessage {
    background:qlineargradient(
        x1:0, y1:0, x2:1, y2:1,
        stop:0 #282439,
        stop:1 #22202f
    );
    border:1px solid #48405f;
    border-radius:14px;
}
QFrame#userMessage:hover {
    background:qlineargradient(
        x1:0, y1:0, x2:1, y2:1,
        stop:0 #2c2740,
        stop:1 #262235
    );
    border-color:#413b64;
}
QFrame#userMessage QLabel#messageBody {
    background:transparent;
    color:#f0eef8;
}
"""

# Rich text paragraphs carry an 11px trailing margin in the assistant document
# stylesheet. Inside a compact outgoing bubble that margin reads as a blank row
# below the text and makes the baseline look too high. User-message blocks already
# get their separation from body_layout, so their paragraph itself should be tight.
_USER_MESSAGE_CSS = theme.MESSAGE_CSS.replace(
    "p { margin:0 0 11px; line-height:1.68; }",
    "p { margin:0; line-height:1.48; }",
)

_REASONING_QSS = f"""
QFrame#reasoningBlock {{ background:transparent; border:none; }}
QPushButton#reasoningToggle {{
    background:transparent;
    border:none;
    border-radius:6px;
    padding:2px 8px 2px 18px;
    color:#8a90a6;
    font-size:11px;
    font-weight:600;
    text-align:left;
    min-height:20px;
}}
QPushButton#reasoningToggle:hover {{ background:#1b1d27; color:#c3c7d4; }}
QLabel#reasoningBody {{
    background:transparent;
    border-left:2px solid #33374a;
    margin-left:7px;
    padding:2px 6px 4px 11px;
    color:#9aa0b2;
    font-family:{theme.FONT_UI};
    font-size:12px;
}}
"""

_STREAM_QSS = """
QWidget#streamStatus, QWidget#streamGlyph {
    background:transparent;
}
QLabel#streamStateLabel {
    background:transparent;
    color:#aca2d6;
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
        self.setObjectName("streamGlyph")
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
            self._fade.deleteLater()
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
                animation.deleteLater()

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
            animation.deleteLater()

        animation.finished.connect(finish_out)
        self._fade = animation
        animation.start()


class ReasoningToggle(QPushButton):
    """One quiet line that opens the model's own thinking."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("reasoningToggle")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFlat(True)
        self._angle = 0.0
        self._animation: QPropertyAnimation | None = None

    def _get_angle(self) -> float:
        return self._angle

    def _set_angle(self, value: float) -> None:
        value = float(value)
        if value != self._angle:
            self._angle = value
            self.update()

    angle = Property(float, _get_angle, _set_angle)

    def set_expanded(self, expanded: bool, *, animate: bool) -> None:
        target = 90.0 if expanded else 0.0
        if self._animation is not None:
            self._animation.stop()
            self._animation = None
        if not animate or not theme.motion_enabled() or abs(self._angle - target) < 0.5:
            self._set_angle(target)
            return
        animation = QPropertyAnimation(self, b"angle", self)
        animation.setDuration(theme.MOTION_FAST_MS)
        animation.setStartValue(self._angle)
        animation.setEndValue(target)
        animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        animation.finished.connect(lambda: self._set_angle(target))
        self._animation = animation
        animation.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)

    def paintEvent(self, event: Any) -> None:  # noqa: N802 - Qt override
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.translate(6.0, self.height() / 2)
        painter.rotate(self._angle)
        color = QColor("#a8a1c8" if self.underMouse() else "#7d829a")
        painter.setPen(
            QPen(color, 1.3, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
        )
        painter.drawLine(QPointF(-1.8, -3.3), QPointF(1.9, 0.0))
        painter.drawLine(QPointF(1.9, 0.0), QPointF(-1.8, 3.3))


class ReasoningBlock(QFrame):
    """The model's private thinking, folded away from the reading line.

    Reasoning models stream their chain of thought in the ordinary content
    field. It is genuinely useful when a reply surprises you, and pure noise the
    rest of the time, so it stays one click away instead of in the transcript.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("reasoningBlock")
        self.setStyleSheet(_REASONING_QSS)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        self.toggle = ReasoningToggle(self)
        self.toggle.clicked.connect(self._toggle)
        layout.addWidget(self.toggle, 0, Qt.AlignmentFlag.AlignLeft)

        self.body = QLabel(self)
        self.body.setObjectName("reasoningBody")
        self.body.setWordWrap(True)
        self.body.setTextFormat(Qt.TextFormat.PlainText)
        self.body.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.body.hide()
        layout.addWidget(self.body)

        self._text = ""
        self._live = False
        self._expanded = False
        self._started: float | None = None
        self._seconds = 0
        self._animation: QParallelAnimationGroup | None = None
        self._clock = QTimer(self)
        self._clock.setInterval(1000)
        self._clock.timeout.connect(self._sync_label)
        self.hide()

    # -- content ---------------------------------------------------------

    def set_reasoning(self, text: str, *, live: bool) -> None:
        text = text or ""
        if not text:
            self._clock.stop()
            self._started = None
            self.hide()
            return

        if self._started is None:
            self._started = time.monotonic()
        if text != self._text:
            self._text = text
            self.body.setText(text)
        if live != self._live:
            self._live = live
            if live:
                if theme.motion_enabled():
                    self._clock.start()
            else:
                self._clock.stop()
                self._seconds = self._elapsed()
        self.show()
        self._sync_label()

    def finish(self) -> None:
        """The turn ended; whatever was being thought is no longer in progress."""
        if self._live:
            self._live = False
            self._clock.stop()
            self._seconds = self._elapsed()
            self._sync_label()

    def _elapsed(self) -> int:
        if self._started is None:
            return 0
        return max(0, int(time.monotonic() - self._started))

    def _sync_label(self) -> None:
        if self._live:
            elapsed = self._elapsed()
            suffix = f" · {fmt.elapsed_label(elapsed)}" if elapsed >= 2 else ""
            self.toggle.setText(f"Thinking{suffix}")
        else:
            seconds = self._seconds or self._elapsed()
            # Only claim a duration this widget actually watched; a rehydrated
            # message has no honest number to report.
            self.toggle.setText(
                f"Thought for {fmt.elapsed_label(seconds)}" if seconds >= 2 else "Thought process"
            )
        self.toggle.setToolTip(
            "The model's own reasoning. It is kept in the conversation history "
            "but stays out of the reply."
        )

    # -- disclosure ------------------------------------------------------

    def _toggle(self) -> None:
        self._expanded = not self._expanded
        self._sync_body(animate=True)

    def _settle_layout(self, *, again: bool = True) -> None:
        """Tell the transcript the message changed height.

        The disclosure runs inside an already-laid-out message; without an
        explicit invalidation the message keeps its old height and the tail of
        the reply is clipped. Wrapped labels only report their final height once
        they have been re-measured at the new width, so this runs twice: now,
        and once the layout that follows has settled.
        """
        self.updateGeometry()
        parent = self.parentWidget()
        if parent is not None:
            if parent.layout() is not None:
                parent.layout().invalidate()
            parent.updateGeometry()
        if again:
            QTimer.singleShot(0, lambda: self._settle_layout(again=False))

    def _sync_body(self, *, animate: bool) -> None:
        show = self._expanded
        self.toggle.set_expanded(show, animate=animate)
        if not animate or not theme.motion_enabled():
            self.body.setMaximumHeight(16777215)
            self.body.setVisible(show)
            self._settle_layout()
            return

        if self._animation is not None:
            self._animation.stop()
        natural = max(0, self.body.heightForWidth(max(120, self.width())))
        start = self.body.height() if self.body.isVisible() else 0
        self.body.setVisible(True)
        self.body.setMaximumHeight(max(0, start))

        effect = QGraphicsOpacityEffect(self.body)
        self.body.setGraphicsEffect(effect)
        effect.setOpacity(1.0 if start else 0.0)

        group = QParallelAnimationGroup(self)
        height = QPropertyAnimation(self.body, b"maximumHeight", group)
        height.setDuration(theme.MOTION_BASE_MS)
        height.setStartValue(start)
        height.setEndValue(natural if show else 0)
        height.setEasingCurve(QEasingCurve.Type.OutCubic)
        opacity = QPropertyAnimation(effect, b"opacity", group)
        opacity.setDuration(theme.MOTION_FAST_MS)
        opacity.setStartValue(1.0 if start else 0.0)
        opacity.setEndValue(1.0 if show else 0.0)
        group.addAnimation(height)
        group.addAnimation(opacity)

        def finish() -> None:
            self.body.setVisible(show)
            self.body.setMaximumHeight(16777215)
            self.body.setGraphicsEffect(None)
            self._animation = None
            self._settle_layout()

        group.finished.connect(finish)
        self._animation = group
        group.start()


class MessageWidget(base.MessageWidget):
    """Message without avatar/name chrome and with stable live-stream rendering."""

    # An outgoing bubble hugs its text, so it needs a reading measure of its own.
    USER_MAX_WIDTH = 620

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
        # Only a reply can think. A sent message is never reasoning.
        self.reasoning = ReasoningBlock(self) if role == "assistant" else None
        if self.reasoning is not None and layout is not None:
            # Above the answer, because that is the order it happened in.
            layout.insertWidget(0, self.reasoning)
        if layout is not None:
            if role == "user":
                # Equal vertical insets keep one-line messages optically centered.
                # The rich-text paragraph itself has no trailing margin (see
                # _USER_MESSAGE_CSS), so these are the only top/bottom gutters.
                layout.setContentsMargins(14, 10, 14, 10)
                layout.setSpacing(0)
                self.body_layout.setSpacing(6)
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

    # ---- measurement -----------------------------------------------------
    #
    # An outgoing bubble is laid out with AlignRight and a Maximum size policy,
    # so the layout hands it exactly its sizeHint() width. QLabel's own hint for
    # wrapped rich text is a heuristic that under-reports badly, which used to
    # squeeze a long sentence into a ~200px bubble and clip it on one line.
    # Measuring the document is the only honest answer.

    def _text_frame(self) -> int:
        margins = self.layout().contentsMargins() if self.layout() is not None else None
        if margins is None:
            return 0
        return margins.left() + margins.right() + 2

    def _ideal_text_width(self, limit: int) -> int:
        ideal = 0
        for widget in self._widgets:
            if isinstance(widget, base.RichLabel):
                document = QTextDocument()
                document.setDefaultFont(widget.font())
                document.setHtml(widget.text())
                document.setTextWidth(limit)
                ideal = max(ideal, int(document.idealWidth() + 0.999))
            else:
                ideal = max(ideal, widget.sizeHint().width())
        return ideal

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt override
        if self.role != "user" or not self._widgets:
            return super().sizeHint()
        frame = self._text_frame()
        limit = max(80, self.USER_MAX_WIDTH - frame)
        width = min(limit, max(48, self._ideal_text_width(limit))) + frame
        return QSize(width, self.heightForWidth(width))

    def heightForWidth(self, width: int) -> int:  # noqa: N802 - Qt override
        # An assistant message with no text yet is not empty: it carries the
        # thinking indicator. Collapsing it to zero is why the transcript looked
        # frozen between pressing Enter and the first token arriving.
        return max(int(super().heightForWidth(width)), self.minimumSizeHint().height())

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
        if not self._streaming and self.reasoning is not None:
            self.reasoning.finish()
        self._sync_stream_status()

    def _sync_stream_status(self) -> None:
        if self.role != "assistant":
            return
        mode = StreamGlyph.WRITING if self._text.strip() else StreamGlyph.THINKING
        self.stream_status.set_mode(mode)
        self.stream_status.set_active(self._streaming)

        # A message whose body is still empty has no height-for-width to report,
        # and the transcript layout collapses it to nothing -- which is why the
        # thinking indicator was invisible for the whole pre-token wait. A floor
        # equal to the status row keeps that moment on screen; it is dropped
        # again as soon as real content can measure itself.
        floor = self.minimumSizeHint().height() if self._streaming and not self._widgets else 0
        if self.minimumHeight() != floor:
            self.setMinimumHeight(floor)
            self.updateGeometry()

    def _set_rich_markup(self, widget: base.RichLabel, html: str) -> None:
        """Apply role-appropriate rich text without a fake blank row in user bubbles."""
        if self.role == "user":
            widget.setText(_USER_MESSAGE_CSS + html)
            widget.setAlignment(Qt.AlignmentFlag.AlignCenter)
            # Keep the RichLabel at least as wide as its unwrapped text so a
            # short "你好" never collapses to a one-character-per-line column
            # when the transcript pane itself is narrow.
            document = QTextDocument()
            document.setDefaultFont(widget.font())
            document.setHtml(_USER_MESSAGE_CSS + html)
            widget.setMinimumWidth(int(document.idealWidth() + 0.999))
            widget.updateGeometry()
            # The bubble frame also needs a minimum width equal to its body, or
            # the parent layout can still shrink the frame below the label's
            # content and produce the same narrow column.
            frame = self._text_frame()
            self.setMinimumWidth(max(48, int(document.idealWidth() + 0.999)) + frame)
            self.updateGeometry()
            return
        widget.set_markup(html)

    def set_text(self, value: str) -> None:
        """Update streaming Markdown in place instead of rebuilding every token.

        The base widget recreates the first changed block. During a stream the
        last prose/code block changes on almost every delta, which causes tiny
        flashes and unnecessary layout churn. Reuse one changed block when its
        type stays the same, then only create genuinely new blocks.
        """
        if self.reasoning is not None:
            # A reasoning model streams its thinking inline in the reply. It is
            # folded into its own block; only the answer stays in the transcript.
            thinking, value, live = markdown.split_reasoning(value)
            self.reasoning.set_reasoning(thinking, live=live and self._streaming)
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
                self._set_rich_markup(widget, new.html)
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
                self._set_rich_markup(widget, block.html)
            self.body_layout.addWidget(widget)
            self._widgets.append(widget)

            # New paragraph/code blocks earn one quiet reveal. Existing blocks
            # are never faded on token deltas, so streaming text stays crisp.
            if self.role == "assistant" and self._streaming and theme.motion_enabled():
                base.fade_in(widget, duration_ms=theme.MOTION_FAST_MS)

        self._blocks = blocks
        if self.role == "user":
            # The bubble hugs its content, so a new measurement is needed before
            # the layout hands it a width.
            self.updateGeometry()
        if self._streaming and value:
            self.stream_status.kick()
        self._sync_stream_status()


class TranscriptView(base.TranscriptView):
    """Transcript whose outgoing bubbles fit their content instead of a card width."""

    USER_MAX_WIDTH = MessageWidget.USER_MAX_WIDTH

    def _build(self, entry: TranscriptEntry) -> QWidget:
        if entry.kind in {"user", "assistant"}:
            return MessageWidget(entry.kind, self.canvas)
        return super()._build(entry)

    def render(self, entries: list[TranscriptEntry]) -> None:
        super().render(entries)

        # The legacy transcript enforced a 280px minimum width for user cards.
        # Reset the *upper* bound here so long messages wrap at a readable width,
        # but leave the per-message minimum width alone — each MessageWidget
        # already tracks its own content-based minimum so a short "你好" stays
        # a single line and is not crushed into a one-character-per-line column.
        for entry in entries:
            if entry.kind != "user":
                continue
            widget = self._widgets.get(entry.key)
            if not isinstance(widget, MessageWidget):
                continue
            widget.setMaximumWidth(self.USER_MAX_WIDTH)
            policy = widget.sizePolicy()
            policy.setHorizontalPolicy(QSizePolicy.Policy.Maximum)
            policy.setVerticalPolicy(QSizePolicy.Policy.Minimum)
            policy.setHeightForWidth(True)
            widget.setSizePolicy(policy)
            widget.updateGeometry()


__all__ = [
    "MessageWidget",
    "ReasoningBlock",
    "ReasoningToggle",
    "StreamGlyph",
    "StreamingStatus",
    "TranscriptView",
]
