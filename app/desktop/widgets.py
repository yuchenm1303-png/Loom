"""Transcript and chrome widgets for the desktop client.

The conversation is a list of real widgets in a scroll area, keyed by App Server
item id. Streaming touches only the widget that changed, so a long thread no
longer re-renders on every token the way the previous QTextBrowser did.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from PySide6.QtCore import (
    QEasingCurve,
    QEvent,
    QParallelAnimationGroup,
    QPropertyAnimation,
    QSize,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import (
    QColor,
    QGuiApplication,
    QIcon,
    QKeyEvent,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QSyntaxHighlighter,
    QTextCharFormat,
)
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QGraphicsOpacityEffect,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from app.desktop import format as fmt
from app.desktop import markdown, theme
from app.desktop.state import TranscriptEntry


def repolish(widget: QWidget) -> None:
    style = widget.style()
    style.unpolish(widget)
    style.polish(widget)
    widget.update()


def fade_in(widget: QWidget, *, duration_ms: int = theme.MOTION_CONTENT_MS) -> None:
    """Settle a newly added widget in. No-op when reduced motion is requested."""
    if not theme.motion_enabled():
        return
    effect = QGraphicsOpacityEffect(widget)
    widget.setGraphicsEffect(effect)
    animation = QPropertyAnimation(effect, b"opacity", widget)
    animation.setDuration(duration_ms)
    animation.setStartValue(0.0)
    animation.setEndValue(1.0)
    animation.setEasingCurve(QEasingCurve.Type.OutCubic)
    # Dropping the effect afterwards keeps text rendering crisp.
    animation.finished.connect(lambda: widget.setGraphicsEffect(None))
    animation.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)


class PulseLabel(QLabel):
    """Small, quiet activity signal used while a response or tool is live."""

    def __init__(self, text: str = "", parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self._pulse: QPropertyAnimation | None = None

    def set_pulsing(self, pulsing: bool) -> None:
        pulsing = bool(pulsing and theme.motion_enabled())
        if not pulsing:
            if self._pulse is not None:
                self._pulse.stop()
                self._pulse = None
            self.setGraphicsEffect(None)
            return
        if self._pulse is not None:
            return
        effect = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(effect)
        pulse = QPropertyAnimation(effect, b"opacity", self)
        pulse.setDuration(1050)
        pulse.setStartValue(0.42)
        pulse.setKeyValueAt(0.5, 1.0)
        pulse.setEndValue(0.42)
        pulse.setLoopCount(-1)
        pulse.setEasingCurve(QEasingCurve.Type.InOutSine)
        pulse.start()
        self._pulse = pulse


def plain_text_height(view: QPlainTextEdit, *, minimum: float, maximum: float) -> int:
    """Pixel height that shows all of a read-only plain-text view's content.

    ``QPlainTextDocumentLayout`` reports ``documentSize().height()`` as a line
    count, not a pixel height -- the two were being added to pixel margins, so a
    two-line code block asked for 14px and was clamped to a clipped 24.
    """
    document = view.document()
    lines = max(1.0, float(document.size().height()))
    height = (
        lines * view.fontMetrics().lineSpacing()
        + document.documentMargin() * 2
        + view.frameWidth() * 2
        + 4
    )
    if view.lineWrapMode() == QPlainTextEdit.LineWrapMode.NoWrap:
        viewport_width = view.viewport().width()
        if viewport_width > 0 and document.size().width() > viewport_width:
            height += view.horizontalScrollBar().sizeHint().height()
    return int(max(float(minimum), min(height, float(maximum))))


def copy_to_clipboard(value: str) -> None:
    clipboard = QGuiApplication.clipboard() or QApplication.clipboard()
    if clipboard is not None:
        clipboard.setText(fmt.text(value))


class VectorIcon(QWidget):
    """Small theme-aware icon drawn by Qt, independent of installed fonts."""

    def __init__(
        self,
        name: str,
        parent: QWidget | None = None,
        *,
        size: int = 28,
        tone: str = "muted",
        framed: bool = True,
    ) -> None:
        super().__init__(parent)
        self.name = name
        self.tone = tone
        self.framed = framed
        self.setFixedSize(size, size)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

    def set_tone(self, tone: str) -> None:
        if tone != self.tone:
            self.tone = tone
            self.update()

    def paintEvent(self, _event: Any) -> None:  # noqa: N802 - Qt override
        colors = {
            "accent": theme.ACCENT_SOFT,
            "good": theme.GOOD,
            "warn": theme.WARN,
            "bad": theme.BAD,
            "muted": "#858d9c",
        }
        color = QColor(colors.get(self.tone, colors["muted"]))
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self.framed:
            painter.setPen(QPen(QColor("#272d38"), 1))
            painter.setBrush(QColor("#141820"))
            painter.drawRoundedRect(self.rect().adjusted(1, 1, -1, -1), 7, 7)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(color, 1.55, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        c = self.width() / 2
        s = min(self.width(), self.height()) * (0.24 if self.framed else 0.31)
        x, y = c, self.height() / 2

        if self.name in {"tool", "model"}:
            path = QPainterPath()
            path.moveTo(x, y - s)
            path.lineTo(x + s, y)
            path.lineTo(x, y + s)
            path.lineTo(x - s, y)
            path.closeSubpath()
            painter.drawPath(path)
            if self.name == "model":
                painter.drawEllipse(int(x - 1), int(y - 1), 2, 2)
        elif self.name == "terminal":
            painter.drawLine(int(x - s), int(y - s * .55), int(x - s * .2), int(y))
            painter.drawLine(int(x - s * .2), int(y), int(x - s), int(y + s * .55))
            painter.drawLine(int(x + s * .05), int(y + s * .6), int(x + s), int(y + s * .6))
        elif self.name == "diff":
            painter.drawLine(int(x - s * .8), int(y - s), int(x - s * .8), int(y + s))
            painter.drawLine(int(x + s * .8), int(y - s), int(x + s * .8), int(y + s))
            painter.drawLine(int(x - s), int(y - s * .4), int(x - s * .55), int(y - s * .4))
            painter.drawLine(int(x - s * .78), int(y - s * .62), int(x - s * .78), int(y - s * .18))
            painter.drawLine(int(x + s * .55), int(y + s * .4), int(x + s), int(y + s * .4))
        elif self.name == "check":
            painter.drawLine(int(x - s), int(y), int(x - s * .25), int(y + s * .7))
            painter.drawLine(int(x - s * .25), int(y + s * .7), int(x + s), int(y - s * .75))
        elif self.name == "arrow":
            painter.drawLine(int(x - s), int(y), int(x + s), int(y))
            painter.drawLine(int(x + s * .35), int(y - s * .65), int(x + s), int(y))
            painter.drawLine(int(x + s * .35), int(y + s * .65), int(x + s), int(y))
        elif self.name == "error":
            painter.drawEllipse(int(x - s), int(y - s), int(s * 2), int(s * 2))
            painter.drawLine(int(x), int(y - s * .55), int(x), int(y + s * .15))
            painter.drawPoint(int(x), int(y + s * .58))
        elif self.name == "browser":
            painter.drawRoundedRect(int(x - s), int(y - s * .8), int(s * 2), int(s * 1.6), 2, 2)
            painter.drawLine(int(x - s), int(y - s * .3), int(x + s), int(y - s * .3))
        elif self.name == "agents":
            painter.drawEllipse(int(x - s * .35), int(y - s), int(s * .7), int(s * .7))
            painter.drawArc(int(x - s), int(y - s * .1), int(s * 2), int(s * 1.25), 25 * 16, 130 * 16)
        else:
            painter.drawEllipse(int(x - 2), int(y - 2), 4, 4)


class PulseVectorIcon(VectorIcon):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._pulse: QPropertyAnimation | None = None

    def set_pulsing(self, pulsing: bool) -> None:
        pulsing = bool(pulsing and theme.motion_enabled())
        if not pulsing:
            if self._pulse is not None:
                self._pulse.stop()
                self._pulse = None
            self.setGraphicsEffect(None)
            return
        if self._pulse is not None:
            return
        effect = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(effect)
        pulse = QPropertyAnimation(effect, b"opacity", self)
        pulse.setDuration(1050)
        pulse.setStartValue(0.5)
        pulse.setKeyValueAt(0.5, 1.0)
        pulse.setEndValue(0.5)
        pulse.setLoopCount(-1)
        pulse.start()
        self._pulse = pulse


def vector_icon(name: str, *, size: int = 18, tone: str = "muted") -> QIcon:
    """Render the same geometry as a QIcon for tabs and native controls."""
    canvas = VectorIcon(name, size=size, tone=tone, framed=False)
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    canvas.render(pixmap)
    canvas.deleteLater()
    return QIcon(pixmap)


def _event_icon(kind: str) -> tuple[str, str]:
    if kind in {"turn_completed", "tool_completed", "process_exited"}:
        return "check", "good"
    if kind in {"turn_failed", "tool_failed"}:
        return "error", "bad"
    if kind.startswith("model_"):
        return "model", "accent"
    if kind.startswith("tool_"):
        return "tool", "accent"
    if kind.startswith("process_"):
        return "terminal", "muted"
    if kind == "turn_diff_updated":
        return "diff", "accent"
    if kind == "turn_started":
        return "arrow", "muted"
    return "dot", "muted"


class ActivityEventRow(QFrame):
    def __init__(self, when: str, kind: str, summary: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("activityEventRow")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 9, 10, 9)
        layout.setSpacing(10)
        icon_name, tone = _event_icon(kind)
        icon = VectorIcon(icon_name, self, size=26, tone=tone)
        layout.addWidget(icon, 0, Qt.AlignmentFlag.AlignTop)
        copy = QVBoxLayout()
        copy.setContentsMargins(0, 0, 0, 0)
        copy.setSpacing(3)
        title = QLabel(summary)
        title.setObjectName("activityEventTitle")
        title.setTextFormat(Qt.TextFormat.PlainText)
        title.setWordWrap(True)
        copy.addWidget(title)
        timestamp = QLabel(when or "live")
        timestamp.setObjectName("activityEventTime")
        copy.addWidget(timestamp)
        layout.addLayout(copy, 1)


class EmptyPanel(QWidget):
    def __init__(self, icon_name: str, title: str, body: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 52, 20, 20)
        layout.setSpacing(9)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter)
        icon = VectorIcon(icon_name, self, size=42, tone="muted")
        layout.addWidget(icon, 0, Qt.AlignmentFlag.AlignHCenter)
        title_label = QLabel(title)
        title_label.setObjectName("panelPlaceholderTitle")
        title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title_label)
        body_label = QLabel(body)
        body_label.setObjectName("panelPlaceholderBody")
        body_label.setWordWrap(True)
        body_label.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
        layout.addWidget(body_label)


class ActivityTimelineView(QScrollArea):
    """Native Runtime timeline; no font glyphs are used as icons."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("activityTimeline")
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.canvas = QWidget()
        self.canvas.setObjectName("activityTimelineCanvas")
        self._layout = QVBoxLayout(self.canvas)
        self._layout.setContentsMargins(3, 8, 8, 16)
        self._layout.setSpacing(7)
        self._layout.addStretch(1)
        self.setWidget(self.canvas)
        self._plain_text = ""

    def render_events(self, events: list[tuple[str, str, str]]) -> None:
        while self._layout.count() > 1:
            item = self._layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                # Unparent before the deferred delete: a widget still parented
                # to the canvas keeps painting its old geometry, which showed up
                # as the empty-state text bleeding through the first rows.
                widget.setParent(None)
                widget.deleteLater()
        self._plain_text = "\n".join(summary for _when, _kind, summary in events)
        if not events:
            empty = EmptyPanel("activity", "Runtime is quiet", "Model steps, tools, commands, diffs, and delegated work appear here.")
            self._layout.insertWidget(0, empty, 1)
        else:
            for when, kind, summary in events:
                self._layout.insertWidget(self._layout.count() - 1, ActivityEventRow(when, kind, summary, self.canvas))
        QTimer.singleShot(0, lambda: self.verticalScrollBar().setValue(self.verticalScrollBar().maximum()))

    def toPlainText(self) -> str:  # noqa: N802
        return self._plain_text or "Runtime is quiet"


class RichLabel(QLabel):
    """Selectable rich-text paragraph that wraps to the transcript width."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("messageBody")
        self.setWordWrap(True)
        self.setTextFormat(Qt.TextFormat.RichText)
        self.setOpenExternalLinks(True)
        self.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
            | Qt.TextInteractionFlag.LinksAccessibleByMouse
        )
        # Wrapped labels only report their true height once the layout is told
        # to ask for it; without this the scroll canvas over-estimates and
        # leaves dead space under the last message.
        policy = QSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
        policy.setHeightForWidth(True)
        self.setSizePolicy(policy)

    def set_markup(self, html: str) -> None:
        self.setText(theme.MESSAGE_CSS + html)


class CodeBlock(QFrame):
    """Fenced code with a language badge and its own copy action."""

    def __init__(self, language: str, source: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("codeBlock")
        self._source = source

        layout = QVBoxLayout(self)
        layout.setContentsMargins(11, 8, 9, 10)
        layout.setSpacing(6)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(6)
        self.language_label = QLabel(language or "code")
        self.language_label.setObjectName("codeLanguage")
        header.addWidget(self.language_label)
        header.addStretch(1)
        self.copy_button = QPushButton("Copy")
        self.copy_button.setObjectName("copyButton")
        self.copy_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.copy_button.clicked.connect(self._copy)
        header.addWidget(self.copy_button)
        layout.addLayout(header)

        self.body = QPlainTextEdit()
        self.body.setObjectName("codeBody")
        self.body.setReadOnly(True)
        self.body.setFrameShape(QFrame.Shape.NoFrame)
        self.body.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.body.setPlainText(source)
        self.body.document().documentLayout().documentSizeChanged.connect(
            lambda _size: self._sync_height()
        )
        layout.addWidget(self.body)
        self._sync_height()

    def set_source(self, language: str, source: str) -> None:
        if language != self.language_label.text():
            self.language_label.setText(language or "code")
        if source != self._source:
            self._source = source
            self.body.setPlainText(source)
            self._sync_height()

    def _sync_height(self) -> None:
        self.body.setFixedHeight(plain_text_height(self.body, minimum=26, maximum=520))

    def _copy(self) -> None:
        copy_to_clipboard(self._source)
        self.copy_button.setText("Copied")
        QTimer.singleShot(1400, lambda: self.copy_button.setText("Copy"))


class MessageWidget(QFrame):
    """One user or assistant message, rendered as blocks."""

    def __init__(self, role: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.role = role
        self.setObjectName("userMessage" if role == "user" else "assistantMessage")
        self._text = ""
        self._blocks: list[markdown.Block] = []
        self._widgets: list[QWidget] = []

        outer = QVBoxLayout(self)
        outer.setContentsMargins(*( (16, 13, 16, 14) if role == "user" else (15, 14, 17, 16) ))
        outer.setSpacing(7)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(7)
        self.role_mark = QLabel("Y" if role == "user" else "L")
        self.role_mark.setObjectName("messageRoleMark")
        self.role_mark.setProperty("role", role)
        self.role_mark.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.role_mark.setFixedSize(24, 24)
        header.addWidget(self.role_mark)
        self.role_label = QLabel("You" if role == "user" else "Loom")
        self.role_label.setObjectName("messageRole")
        self.role_label.setProperty("role", role)
        header.addWidget(self.role_label)
        self.stream_badge = PulseLabel("Thinking")
        self.stream_badge.setObjectName("streamBadge")
        self.stream_badge.hide()
        header.addWidget(self.stream_badge)
        header.addStretch(1)
        self.copy_button = QPushButton("Copy")
        self.copy_button.setObjectName("copyButton")
        self.copy_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.copy_button.clicked.connect(self._copy)
        self.copy_button.hide()
        header.addWidget(self.copy_button)
        outer.addLayout(header)

        self.body_layout = QVBoxLayout()
        self.body_layout.setContentsMargins(0, 0, 0, 0)
        self.body_layout.setSpacing(8)
        outer.addLayout(self.body_layout)

        policy = QSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
        policy.setHeightForWidth(True)
        self.setSizePolicy(policy)

    def heightForWidth(self, width: int) -> int:  # noqa: N802 - Qt override
        return self.layout().heightForWidth(width) if self.layout() else super().heightForWidth(width)

    def hasHeightForWidth(self) -> bool:  # noqa: N802 - Qt override
        return True

    def enterEvent(self, event: QEvent) -> None:  # noqa: N802 - Qt override
        self.copy_button.setVisible(bool(self._text.strip()))
        super().enterEvent(event)

    def leaveEvent(self, event: QEvent) -> None:  # noqa: N802 - Qt override
        self.copy_button.hide()
        super().leaveEvent(event)

    def _copy(self) -> None:
        copy_to_clipboard(self._text)
        self.copy_button.setText("Copied")
        QTimer.singleShot(1400, lambda: self.copy_button.setText("Copy"))

    def set_streaming(self, streaming: bool) -> None:
        self.stream_badge.setVisible(bool(streaming))
        self.stream_badge.set_pulsing(streaming)

    def set_text(self, value: str) -> None:
        """Update the body, reusing every block whose content did not change."""
        if value == self._text:
            return
        self._text = value
        blocks = markdown.parse_blocks(value) if value else []

        shared = 0
        for old, new in zip(self._blocks, blocks):
            if old != new:
                break
            shared += 1

        while len(self._widgets) > shared:
            widget = self._widgets.pop()
            self.body_layout.removeWidget(widget)
            widget.setParent(None)
            widget.deleteLater()

        for block in blocks[shared:]:
            widget: QWidget
            if block.kind == "code":
                widget = CodeBlock(block.language, block.source, self)
            else:
                widget = RichLabel(self)
                widget.set_markup(block.html)
            self.body_layout.addWidget(widget)
            self._widgets.append(widget)

        self._blocks = blocks


_CARD_ICONS = {
    "tool": "tool",
    "process": "terminal",
    "diff": "diff",
    "error": "error",
}


class ActivityCard(QFrame):
    """Collapsible inline card for a tool call, process, diff or turn error."""

    BODY_LIMIT = 20_000

    def __init__(self, kind: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.kind = kind
        self.setObjectName("activityCard")
        self._body_text = ""
        self._full_title = ""
        self._expanded = kind in {"diff", "error"}
        self._user_toggled = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(11, 9, 11, 10)
        layout.setSpacing(7)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(8)
        self.icon = PulseVectorIcon(_CARD_ICONS.get(kind, "dot"), self, size=28)
        header.addWidget(self.icon)

        titles = QVBoxLayout()
        titles.setContentsMargins(0, 0, 0, 0)
        titles.setSpacing(2)
        self.title_label = QLabel("")
        self.title_label.setObjectName("cardTitle")
        self.title_label.setTextFormat(Qt.TextFormat.PlainText)
        titles.addWidget(self.title_label)
        self.subtitle_label = QLabel("")
        self.subtitle_label.setObjectName("cardSubtitle")
        self.subtitle_label.setTextFormat(Qt.TextFormat.PlainText)
        self.subtitle_label.hide()
        titles.addWidget(self.subtitle_label)
        header.addLayout(titles, 1)

        self.status_label = QLabel("")
        self.status_label.setObjectName("cardStatus")
        self.toggle_button = QPushButton("Details")
        self.toggle_button.setObjectName("cardToggle")
        self.toggle_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.toggle_button.clicked.connect(self._toggle)
        header.addWidget(self.toggle_button)
        layout.addLayout(header)

        self.body_shell = QFrame()
        self.body_shell.setObjectName("cardBodyShell")
        shell_layout = QVBoxLayout(self.body_shell)
        shell_layout.setContentsMargins(11, 8, 11, 9)
        shell_layout.setSpacing(5)
        self.body_title = QLabel(self._body_heading())
        self.body_title.setObjectName("cardBodyTitle")
        shell_layout.addWidget(self.body_title)
        self.body = QPlainTextEdit()
        self.body.setObjectName("cardBody")
        self.body.setReadOnly(True)
        self.body.setFrameShape(QFrame.Shape.NoFrame)
        self.body.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.body.document().documentLayout().documentSizeChanged.connect(
            lambda _size: self._sync_height()
        )
        shell_layout.addWidget(self.body)
        footer = QHBoxLayout()
        footer.addStretch(1)
        footer.addWidget(self.status_label)
        shell_layout.addLayout(footer)
        self.body_shell.hide()
        layout.addWidget(self.body_shell)

        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
        self._body_animation: QParallelAnimationGroup | None = None

    def _body_heading(self) -> str:
        return {
            "process": "Shell",
            "tool": "Tool output",
            "diff": "Changes",
            "error": "Error",
        }.get(self.kind, "Output")

    def _presented_title(self, title: str, status: str) -> str:
        if self.kind == "process":
            if status in {"running", "started"}:
                return f"Running {title}"
            if status == "completed":
                return f"Ran {title}"
        return title

    def _sync_title(self) -> None:
        # Hidden Runtime tabs are initially laid out at a token width. Keep the
        # source title intact until the card has a real viewport to measure.
        if self.width() < 240:
            self.title_label.setText(self._full_title)
            return
        available = max(120, self.width() - 170)
        self.title_label.setText(
            self.title_label.fontMetrics().elidedText(
                self._full_title, Qt.TextElideMode.ElideRight, available
            )
        )

    def resizeEvent(self, event: Any) -> None:  # noqa: N802 - Qt override
        super().resizeEvent(event)
        self._sync_title()

    def _toggle(self) -> None:
        self._user_toggled = True
        self._expanded = not self._expanded
        self._sync_body(animate=True)

    def _sync_body(self, *, animate: bool = False) -> None:
        has_body = bool(self._body_text)
        self.toggle_button.setVisible(has_body)
        show = has_body and self._expanded
        self.toggle_button.setText("⌃" if show else "⌄")
        self.toggle_button.setToolTip("Hide output" if show else "Show output")
        if not animate or not theme.motion_enabled():
            self.body_shell.setVisible(show)
            if show:
                self._sync_height()
            return

        if self._body_animation is not None:
            self._body_animation.stop()
            self._body_animation = None

        # The first motion pass animated ``maximumHeight`` from 0 to natural,
        # which on every disclosure toggle forced the transcript to relayout
        # every card below the toggle, making the whole page appear to bounce.
        # This pass commits the layout in a single frame and only cross-fades
        # opacity so the body "reveals" rather than "expands". No card below
        # this one ever moves during the transition.
        if show:
            # Reveal: shell already occupies its final height from the next
            # paint; just fade the opacity in.
            self.body_shell.setVisible(True)
            self._sync_height()
            effect = QGraphicsOpacityEffect(self.body_shell)
            self.body_shell.setGraphicsEffect(effect)
            effect.setOpacity(0.0)
            opacity = QPropertyAnimation(effect, b"opacity", self)
            opacity.setDuration(theme.MOTION_BASE_MS)
            opacity.setStartValue(0.0)
            opacity.setEndValue(1.0)
            opacity.setEasingCurve(QEasingCurve.Type.OutCubic)

            def finish() -> None:
                self.body_shell.setGraphicsEffect(None)
                self._body_animation = None

            opacity.finished.connect(finish)
            self._body_animation = opacity
            opacity.start()
        else:
            # Collapse: layout reclaims the space immediately. The shell fades
            # out as the cards underneath snap into place; that single layout
            # frame is what users feel as a clean "snap shut" instead of a
            # multi-frame cascade.
            self.body_shell.setVisible(False)
            self.body_shell.setGraphicsEffect(None)

    def _body_height(self) -> int:
        return plain_text_height(self.body, minimum=30, maximum=360)

    def _sync_height(self) -> None:
        # "Shown" here means the disclosure is open, not that the window happens
        # to be on screen: a card on an inactive Runtime tab must still be sized
        # correctly for when that tab is selected.
        if self.body_shell.isHidden():
            return
        self.body.setFixedHeight(self._body_height())

    def update_card(
        self,
        *,
        title: str,
        subtitle: str = "",
        status: str = "",
        body: str = "",
        auto_expand: bool | None = None,
    ) -> None:
        presented_title = self._presented_title(title, status)
        if self._full_title != presented_title:
            self._full_title = presented_title
            self.title_label.setToolTip(presented_title)
            self._sync_title()
        if subtitle:
            self.subtitle_label.setText(subtitle)
            self.subtitle_label.show()
        else:
            self.subtitle_label.hide()

        self.status_label.setText(fmt.human_status(status) if status else "")
        if self.status_label.property("state") != status:
            self.status_label.setProperty("state", status)
            repolish(self.status_label)
        if self.property("state") != status:
            self.setProperty("state", status)
            repolish(self)
        tone = (
            "good" if status == "completed" else
            "bad" if status in {"failed", "denied", "cancelled"} else
            "warn" if status in {"waiting", "waiting_approval"} else
            "accent" if status in {"running", "started"} else
            "muted"
        )
        self.icon.set_tone(tone)
        self.icon.set_pulsing(status in {"running", "started", "waiting"})

        body = body[-self.BODY_LIMIT :] if len(body) > self.BODY_LIMIT else body
        if body != self._body_text:
            self._body_text = body
            self.body.setPlainText(body)
        if auto_expand is not None and not self._user_toggled:
            self._expanded = auto_expand
        self._sync_body()


class TranscriptCanvas(QWidget):
    """Scroll content whose height follows its wrapped width.

    Without this a QScrollArea sizes the content from ``sizeHint()``, which for
    word-wrapped labels over-reports and leaves scrollable dead space below the
    last message.
    """

    def hasHeightForWidth(self) -> bool:  # noqa: N802 - Qt override
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802 - Qt override
        layout = self.layout()
        if layout is None:
            return super().heightForWidth(width)
        return layout.heightForWidth(width)

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt override
        # A wrapped label's minimum is the height it would need at its narrowest
        # width, which the scroll area would otherwise adopt as the content
        # height. Our width comes from the viewport, so height-for-width is the
        # only measurement that should decide how far this canvas scrolls.
        return QSize(0, 0)


class TranscriptView(QScrollArea):
    """Keyed, incrementally reconciled conversation view."""

    TAIL_THRESHOLD_PX = 64

    # Below this, following the tail is tracking; above it, it is a jump.
    SMOOTH_TAIL_MIN_PX = 90

    # Long lines are hard to track back to the next one, so the conversation
    # keeps a comfortable measure and centres itself in a wide window.
    MAX_CONTENT_WIDTH = 820

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        margins: tuple[int, int, int, int] = (4, 14, 14, 28),
        spacing: int = 20,
        max_content_width: int = MAX_CONTENT_WIDTH,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("transcript")
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        self.canvas = TranscriptCanvas()
        self.canvas.setObjectName("transcriptCanvas")
        canvas_policy = QSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
        canvas_policy.setHeightForWidth(True)
        self.canvas.setSizePolicy(canvas_policy)
        self._base_margins = margins
        self._max_content_width = max(0, int(max_content_width))
        self._layout = QVBoxLayout(self.canvas)
        self._layout.setContentsMargins(*margins)
        self._layout.setSpacing(spacing)
        self._layout.addStretch(1)
        self.setWidget(self.canvas)

        self._widgets: dict[str, QWidget] = {}
        self._order: list[str] = []
        self._signatures: dict[str, tuple[Any, ...]] = {}
        # Only genuinely new arrivals animate; rehydrating a thread should not
        # fade in every message at once.
        self._settled = False

        # Content height settles asynchronously, so "stay at the bottom" has to
        # react to the range growing rather than scroll once and hope.
        self._follow_tail = True
        self._auto_scrolling = False
        self._tail_animation: QPropertyAnimation | None = None
        bar = self.verticalScrollBar()
        bar.rangeChanged.connect(self._on_range_changed)
        bar.valueChanged.connect(self._on_value_changed)

    # -- reading helpers used by tests and the window ----------------------

    def message_count(self) -> int:
        return len(self._order)

    def toPlainText(self) -> str:  # noqa: N802 - mirrors the old QTextBrowser API
        parts: list[str] = []
        for key in self._order:
            widget = self._widgets.get(key)
            if isinstance(widget, MessageWidget):
                parts.append(widget._text)
            elif isinstance(widget, ActivityCard):
                parts.append(
                    "\n".join(
                        part
                        for part in (
                            widget._full_title,
                            widget.subtitle_label.text(),
                            widget._body_text,
                        )
                        if part
                    )
                )
        return "\n\n".join(parts)

    # -- reconciliation ----------------------------------------------------

    def clear(self) -> None:
        for key in list(self._order):
            widget = self._widgets.pop(key, None)
            if widget is not None:
                self._layout.removeWidget(widget)
                widget.setParent(None)
                widget.deleteLater()
        self._order.clear()
        self._signatures.clear()
        self._settled = False

    def render(self, entries: list[TranscriptEntry]) -> None:
        keys = [entry.key for entry in entries]

        for key in [key for key in self._order if key not in set(keys)]:
            widget = self._widgets.pop(key, None)
            self._signatures.pop(key, None)
            if widget is not None:
                self._layout.removeWidget(widget)
                widget.setParent(None)
                widget.deleteLater()
        self._order = [key for key in self._order if key in self._widgets]

        for index, entry in enumerate(entries):
            widget = self._widgets.get(entry.key)
            if widget is None:
                widget = self._build(entry)
                self._widgets[entry.key] = widget
                # Keep the trailing stretch last.
                alignment = (
                    Qt.AlignmentFlag.AlignRight
                    if entry.kind == "user"
                    else Qt.AlignmentFlag.AlignTop
                )
                if entry.kind == "user":
                    widget.setMaximumWidth(690)
                    policy = widget.sizePolicy()
                    policy.setHorizontalPolicy(QSizePolicy.Policy.Maximum)
                    policy.setVerticalPolicy(QSizePolicy.Policy.Minimum)
                    policy.setHeightForWidth(True)
                    widget.setSizePolicy(policy)
                self._layout.insertWidget(self._layout.count() - 1, widget, 0, alignment)
                self._order.insert(min(index, len(self._order)), entry.key)
                self._signatures[entry.key] = ()
                if self._settled:
                    fade_in(widget)
            signature = entry.signature()
            if self._signatures.get(entry.key) != signature:
                self._apply(widget, entry)
                self._signatures[entry.key] = signature

        self._order = keys
        self._settled = True

    def _build(self, entry: TranscriptEntry) -> QWidget:
        if entry.kind in {"user", "assistant"}:
            return MessageWidget(entry.kind, self.canvas)
        return ActivityCard(entry.kind, self.canvas)

    def _apply(self, widget: QWidget, entry: TranscriptEntry) -> None:
        if isinstance(widget, MessageWidget):
            widget.set_text(entry.text)
            widget.set_streaming(entry.streaming)
            return
        if isinstance(widget, ActivityCard):
            widget.update_card(**describe_card(entry))

    def resizeEvent(self, event: Any) -> None:  # noqa: N802 - Qt override
        super().resizeEvent(event)
        left, top, right, bottom = self._base_margins
        if self._max_content_width:
            slack = self.viewport().width() - self._max_content_width - left - right
            if slack > 0:
                gutter = slack // 2
                left += gutter
                right += gutter
        self._layout.setContentsMargins(left, top, right, bottom)

    def _at_tail(self) -> bool:
        bar = self.verticalScrollBar()
        return bar.maximum() - bar.value() <= self.TAIL_THRESHOLD_PX

    def _on_range_changed(self, _minimum: int, maximum: int) -> None:
        if not self._follow_tail:
            return
        bar = self.verticalScrollBar()
        distance = maximum - bar.value()
        # Token-by-token growth is a few pixels at a time and should track the
        # text exactly. A whole code block or tool card arriving at once is a
        # jump the eye cannot follow, so that one is eased instead.
        if distance <= self.SMOOTH_TAIL_MIN_PX or not theme.motion_enabled():
            self._set_scroll_value(maximum)
            return
        self._animate_to_tail(maximum)

    def _set_scroll_value(self, value: int) -> None:
        self._auto_scrolling = True
        try:
            self.verticalScrollBar().setValue(value)
        finally:
            self._auto_scrolling = False

    def _stop_tail_animation(self) -> None:
        # ``DeleteWhenStopped`` frees the C++ object as soon as the ease ends,
        # so a kept reference must be dropped before it is touched again.
        animation, self._tail_animation = self._tail_animation, None
        if animation is None:
            return
        try:
            animation.stop()
        except RuntimeError:
            pass

    def _animate_to_tail(self, maximum: int) -> None:
        bar = self.verticalScrollBar()
        self._stop_tail_animation()
        animation = QPropertyAnimation(bar, b"value", self)
        animation.setDuration(theme.MOTION_BASE_MS)
        animation.setStartValue(bar.value())
        animation.setEndValue(maximum)
        animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        animation.valueChanged.connect(lambda _value: setattr(self, "_auto_scrolling", True))

        def finish() -> None:
            self._auto_scrolling = False
            self._tail_animation = None

        animation.finished.connect(finish)
        self._tail_animation = animation
        self._auto_scrolling = True
        animation.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)

    def _on_value_changed(self, value: int) -> None:
        # Scrolling up parks the view; returning to the bottom resumes following.
        # The view moving itself is not the reader changing their mind, so an
        # eased catch-up must not be able to switch following off mid-flight.
        if self._auto_scrolling:
            return
        self._follow_tail = self.verticalScrollBar().maximum() - value <= self.TAIL_THRESHOLD_PX

    def scroll_to_tail(self) -> None:
        self._stop_tail_animation()
        self._follow_tail = True
        bar = self.verticalScrollBar()
        self._set_scroll_value(bar.maximum())


def describe_card(entry: TranscriptEntry) -> dict[str, Any]:
    """Turn a non-message transcript entry into card fields."""
    item = entry.item
    status = entry.status

    if entry.kind == "tool":
        tool = fmt.text(item.get("toolName")) or "tool"
        arguments = item.get("arguments") or {}
        subtitle = ""
        if isinstance(arguments, dict) and arguments:
            subtitle = fmt.pretty(arguments).replace("\n", " ")[:160]
        body_parts = []
        if arguments:
            body_parts.append(f"arguments\n{fmt.pretty(arguments)}")
        content = fmt.text(item.get("content"))
        if content:
            body_parts.append(f"result\n{content}")
        reason = fmt.text(item.get("reason"))
        if reason:
            body_parts.append(f"reason\n{reason}")
        return {
            "title": tool,
            "subtitle": subtitle,
            "status": status,
            "body": "\n\n".join(body_parts),
            "auto_expand": status in {"failed", "denied"},
        }

    if entry.kind == "process":
        command = fmt.command_line(item.get("argv")) or fmt.text(item.get("command"))
        stdout = fmt.text(item.get("stdout"))
        stderr = fmt.text(item.get("stderr"))
        body = f"$ {command}" if command else ""
        if stdout:
            body += ("\n\n" if body else "") + stdout
        if stderr:
            body += ("\n" if body else "") + stderr
        exit_code = item.get("exitCode")
        subtitle = fmt.text(item.get("cwd"))
        if exit_code not in (None, ""):
            subtitle = f"exit {exit_code}" + (f" · {subtitle}" if subtitle else "")
        return {
            "title": f"$ {command}" if command else "Managed process",
            "subtitle": subtitle,
            "status": status,
            "body": body,
            "auto_expand": bool(body) and status in {"running", "started", "failed"},
        }

    if entry.kind == "diff":
        paths = [fmt.text(path) for path in (item.get("paths") or [])]
        diff = fmt.text(item.get("diff"))
        if item.get("truncated"):
            diff += "\n\n(diff truncated by Runtime)"
        count = len(paths)
        return {
            "title": f"Edited {count} file{'' if count == 1 else 's'}" if count else "Workspace change",
            "subtitle": ", ".join(paths[:4]) + (" …" if count > 4 else ""),
            "status": status or "completed",
            "body": diff,
            "auto_expand": False,
        }

    detail = fmt.text(item.get("message")) or fmt.text(item.get("reason")) or fmt.text(item.get("text"))
    return {
        "title": fmt.human_status(status) or "Turn error",
        "subtitle": "",
        "status": status or "failed",
        "body": detail,
        "auto_expand": bool(detail),
    }


class CardListView(QWidget):
    """A Runtime tab rendered as cards, or a quiet placeholder when empty.

    Reuses the transcript's keyed reconciliation so a running process updates in
    place instead of the whole panel being rewritten as text.
    """

    def __init__(
        self,
        kind: str,
        *,
        empty_title: str,
        empty_body: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.kind = kind
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        icons = {"process": "terminal", "tool": "browser", "diff": "diff"}
        self.placeholder = EmptyPanel(icons.get(kind, "tool"), empty_title, empty_body, self)
        layout.addWidget(self.placeholder, 1)

        # The Runtime panel is already narrow, so it takes no reading measure.
        self.cards = TranscriptView(margins=(2, 8, 8, 16), spacing=8, max_content_width=0)
        self.cards.hide()
        layout.addWidget(self.cards, 1)
        # Widgets on an inactive tab report isVisible() as False, so what is
        # showing has to be tracked rather than asked.
        self._has_items = False

    def render_items(self, items: list[dict[str, Any]]) -> None:
        entries = [
            TranscriptEntry(
                key=fmt.text(item.get("id")) or f"{self.kind}:{index}",
                kind=self.kind,
                item=item,
            )
            for index, item in enumerate(items)
        ]
        self._has_items = bool(entries)
        if not entries:
            self.cards.clear()
            self.cards.hide()
            self.placeholder.show()
            return
        self.placeholder.hide()
        self.cards.show()
        self.cards.render(entries)

    def toPlainText(self) -> str:  # noqa: N802 - mirrors the panels it replaces
        return self.cards.toPlainText() if self._has_items else self.placeholder.text()


class DiffHighlighter(QSyntaxHighlighter):
    """Colour unified-diff lines so additions and removals read at a glance."""

    def __init__(self, document: Any) -> None:
        super().__init__(document)
        self._added = QTextCharFormat()
        self._added.setForeground(QColor(theme.GOOD))
        self._removed = QTextCharFormat()
        self._removed.setForeground(QColor(theme.BAD))
        self._hunk = QTextCharFormat()
        self._hunk.setForeground(QColor(theme.ACCENT_SOFT))
        self._meta = QTextCharFormat()
        self._meta.setForeground(QColor(theme.TEXT_MUTED))

    def highlightBlock(self, text: str) -> None:  # noqa: N802 - Qt override
        if text.startswith(("+++", "---", "diff ", "index ")):
            fmt_ = self._meta
        elif text.startswith("@@"):
            fmt_ = self._hunk
        elif text.startswith("+"):
            fmt_ = self._added
        elif text.startswith("-"):
            fmt_ = self._removed
        else:
            return
        self.setFormat(0, len(text), fmt_)


class DiffView(QPlainTextEdit):
    """Read-only unified diff with colouring."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("diffView")
        self.setReadOnly(True)
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.highlighter = DiffHighlighter(self.document())

    def set_diff(self, value: str, *, keep_columns: bool) -> None:
        self.setLineWrapMode(
            QPlainTextEdit.LineWrapMode.NoWrap
            if keep_columns
            else QPlainTextEdit.LineWrapMode.WidgetWidth
        )
        self.setPlainText(value)


# Only states that ask something of the reader earn a dot. Idle and completed
# are the resting states of almost every row, so marking them says nothing.
_ATTENTION_STATES = {
    "running": "running",
    "starting": "running",
    "waiting_approval": "waiting_approval",
    "failed": "failed",
    "cancelled": "failed",
}


class ThreadListItemWidget(QWidget):
    """One conversation row: a title, when it last moved, and a dot when it asks.

    A row used to be a title alone on a 52px band, so a project whose threads
    are all called the same thing read as a stack of identical grey text. The
    second line spends height that was already being paid for.
    """

    def __init__(
        self,
        record: dict[str, Any],
        parent: QWidget | None = None,
        *,
        active_workspace: str | Path | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("threadItemWidget")
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._full_title = fmt.text(record.get("title")).strip() or "New conversation"

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 9, 0)
        layout.setSpacing(0)

        # Selection reads as a bar in the gutter rather than a lighter box, so
        # the row does not change width or shift its text when it is chosen.
        self.marker = QFrame()
        self.marker.setObjectName("threadRowMarker")
        self.marker.setFixedWidth(2)
        gutter = QVBoxLayout()
        gutter.setContentsMargins(3, 8, 6, 8)
        gutter.addWidget(self.marker)
        layout.addLayout(gutter)

        lines = QVBoxLayout()
        lines.setContentsMargins(0, 7, 0, 7)
        lines.setSpacing(2)
        layout.addLayout(lines, 1)

        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(8)
        self.title_label = QLabel(self._full_title)
        self.title_label.setObjectName("threadItemTitle")
        self.title_label.setTextFormat(Qt.TextFormat.PlainText)
        top.addWidget(self.title_label, 1)

        state = "" if record.get("archived") else fmt.text(record.get("status"))
        marker = _ATTENTION_STATES.get(state, "")
        self.status_dot = QLabel("●")
        self.status_dot.setObjectName("threadDot")
        self.status_dot.setProperty("state", marker)
        self.status_dot.setVisible(bool(marker))
        top.addWidget(self.status_dot, 0, Qt.AlignmentFlag.AlignVCenter)
        lines.addLayout(top)

        when = fmt.relative_time(record.get("updatedAt"))
        # Resting states are the norm; naming them on every row says nothing.
        attention = fmt.human_status(state) if marker else ""
        self.meta_label = QLabel(" · ".join(part for part in (when, attention) if part))
        self.meta_label.setObjectName("threadItemMeta")
        self.meta_label.setProperty("state", marker)
        self.meta_label.setTextFormat(Qt.TextFormat.PlainText)
        lines.addWidget(self.meta_label)

        detail = " · ".join(
            part
            for part in (
                fmt.short_path(record.get("workspace")) if record.get("workspace") else "",
                fmt.human_status(record.get("status")),
                f"{fmt.format_tokens((record.get('usage') or {}).get('totalTokens'))} tokens"
                if isinstance(record.get("usage"), dict)
                and (record.get("usage") or {}).get("totalTokens")
                else "",
            )
            if part
        )
        # The metadata every row used to print is available, just not shouted.
        self.setToolTip(f"{self._full_title}\n{detail}" if detail else self._full_title)

    def set_active(self, active: bool) -> None:
        """Mark the row the transcript is currently showing."""
        for widget in (self.marker, self.title_label):
            if widget.property("active") != active:
                widget.setProperty("active", active)
                repolish(widget)

    def resizeEvent(self, event: Any) -> None:  # noqa: N802 - Qt override
        super().resizeEvent(event)
        reserved = 21 + 8 if self.status_dot.isVisible() else 21
        available = max(48, self.width() - reserved)
        self.title_label.setText(
            self.title_label.fontMetrics().elidedText(
                self._full_title, Qt.TextElideMode.ElideRight, available
            )
        )


class ThreadGroupHeader(QWidget):
    """Non-selectable separator naming the project a run of rows belongs to."""

    def __init__(self, title: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("threadGroupHeader")
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(11, 14, 10, 5)
        layout.setSpacing(9)
        self.label = QLabel(title.upper())
        self.label.setObjectName("threadGroupLabel")
        self.label.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.label)
        # A hairline carries the grouping to the edge of the panel, so runs of
        # rows separate without another line of text.
        rule = QFrame()
        rule.setObjectName("threadGroupRule")
        rule.setFixedHeight(1)
        layout.addWidget(rule, 1)


class ApprovalCard(QFrame):
    """Approval prompt that owns the call it is answering."""

    responded = Signal(str, bool)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("approvalCard")
        self._call_id = ""

        layout = QVBoxLayout(self)
        layout.setContentsMargins(15, 13, 15, 13)
        layout.setSpacing(9)

        header = QHBoxLayout()
        header.setSpacing(9)
        icon = QLabel("!")
        icon.setObjectName("approvalIcon")
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon.setFixedSize(24, 24)
        header.addWidget(icon)
        self.title_label = QLabel("Approval required")
        self.title_label.setObjectName("approvalTitle")
        header.addWidget(self.title_label, 1)
        layout.addLayout(header)

        self.details_label = QLabel("")
        self.details_label.setObjectName("approvalDetails")
        self.details_label.setWordWrap(True)
        self.details_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        layout.addWidget(self.details_label)

        self.arguments_view = QPlainTextEdit()
        self.arguments_view.setObjectName("approvalArguments")
        self.arguments_view.setReadOnly(True)
        self.arguments_view.setFrameShape(QFrame.Shape.NoFrame)
        self.arguments_view.setMaximumHeight(150)
        self.arguments_view.hide()
        layout.addWidget(self.arguments_view)
        self._entrance: QPropertyAnimation | None = None

        actions = QHBoxLayout()
        actions.addStretch(1)
        self.deny_button = QPushButton("Deny")
        self.deny_button.setObjectName("denyButton")
        self.deny_button.clicked.connect(lambda: self._respond(False))
        actions.addWidget(self.deny_button)
        self.allow_button = QPushButton("Allow once")
        self.allow_button.setObjectName("allowButton")
        self.allow_button.clicked.connect(lambda: self._respond(True))
        actions.addWidget(self.allow_button)
        layout.addLayout(actions)
        self.hide()

    @property
    def call_id(self) -> str:
        return self._call_id

    def present(self, approval: dict[str, Any]) -> None:
        self._call_id = fmt.text(approval.get("callId"))
        tool = fmt.text(approval.get("toolName")) or "tool"
        self.title_label.setText(f"Approval required · {tool}")
        detail = " · ".join(
            part
            for part in (fmt.text(approval.get("effect")), fmt.text(approval.get("reason")))
            if part
        )
        self.details_label.setText(detail or f"Loom wants to run {tool}.")
        arguments = approval.get("arguments") or {}
        if arguments:
            self.arguments_view.setPlainText(fmt.pretty(arguments))
            self._sync_arguments_height()
            self.arguments_view.show()
        else:
            self.arguments_view.hide()
        self.set_busy(False)
        was_visible = self.isVisible()
        self.show()
        # An approval interrupts the reader mid-turn, so it should arrive rather
        # than appear: enough motion to catch the eye, not enough to delay them.
        if not was_visible:
            fade_in(self, duration_ms=theme.MOTION_BASE_MS)

    def _sync_arguments_height(self) -> None:
        """Show the call as it is, not padded out to a fixed inspector box."""
        self.arguments_view.setFixedHeight(
            plain_text_height(self.arguments_view, minimum=38, maximum=150)
        )

    def dismiss(self) -> None:
        self._call_id = ""
        self.hide()

    def set_busy(self, busy: bool) -> None:
        self.allow_button.setEnabled(not busy)
        self.deny_button.setEnabled(not busy)

    def _respond(self, approved: bool) -> None:
        if not self._call_id:
            return
        self.set_busy(True)
        self.responded.emit(self._call_id, approved)

    # Kept so callers and tests can read the rendered detail text directly.
    def text(self) -> str:
        return self.details_label.text()


class Banner(QFrame):
    """Dismissible inline notice; replaces the old modal error dialogs."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("banner")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 8, 8, 8)
        layout.setSpacing(8)

        self.label = QLabel("")
        self.label.setObjectName("bannerText")
        self.label.setWordWrap(True)
        self.label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.label, 1)

        self.close_button = QPushButton("✕")
        self.close_button.setObjectName("bannerClose")
        self.close_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.close_button.clicked.connect(self.dismiss)
        layout.addWidget(self.close_button, 0, Qt.AlignmentFlag.AlignTop)
        self.hide()

    def show_message(self, message: str) -> None:
        self.label.setText(fmt.text(message).strip())
        self.show()

    def dismiss(self) -> None:
        self.label.clear()
        self.hide()


class EmptyState(QFrame):
    """Starting point shown when a thread has no conversation yet."""

    promptChosen = Signal(str)

    PROMPTS: tuple[tuple[str, str], ...] = (
        (
            "Explore project",
            "Inspect this project and summarize its architecture. Do not modify files.",
        ),
        (
            "Find a bug",
            "Inspect the current project, identify one meaningful bug or risk, and explain the "
            "root cause before changing anything.",
        ),
        (
            "Run tests",
            "Run the relevant test suite, summarize failures, and do not modify code yet.",
        ),
        (
            "Map architecture",
            "Explain this codebase from the entry points down to the main runtime and tool layers.",
        ),
    )

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("emptyState")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(30, 20, 30, 24)
        layout.setSpacing(0)
        layout.addStretch(1)

        content = QFrame()
        content.setObjectName("emptyStateContent")
        self.content = content
        content.setMinimumWidth(0)
        content.setMaximumWidth(640)
        inner = QVBoxLayout(content)
        inner.setContentsMargins(8, 12, 8, 12)
        inner.setSpacing(16)

        emblem = QLabel("L")
        emblem.setObjectName("emptyEmblem")
        emblem.setFixedSize(64, 64)
        emblem.setAlignment(Qt.AlignmentFlag.AlignCenter)
        inner.addWidget(emblem)
        inner.addSpacing(8)
        kicker = QLabel("LOOM WORKSPACE")
        kicker.setObjectName("emptyKicker")
        inner.addWidget(kicker)
        title = QLabel("Ready to begin?")
        title.setObjectName("emptyTitle")
        title.setWordWrap(True)
        inner.addWidget(title)
        body = QLabel(
            "Turn an idea into progress.\nAsk anything, or pick a place to start."
        )
        body.setObjectName("emptyBody")
        body.setWordWrap(True)
        inner.addWidget(body)

        from app.desktop.interaction import SuggestionCard

        grid = QGridLayout()
        grid.setSpacing(12)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        descriptions = ("Understand the codebase", "Trace a problem to its source", "Check what works", "See how it fits together")
        inner.addSpacing(10)
        for index, (label, prompt) in enumerate(self.PROMPTS):
            button = SuggestionCard(label, descriptions[index])
            button.setObjectName("promptSuggestion")
            button.setToolTip(prompt)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.clicked.connect(
                lambda _checked=False, value=prompt: self.promptChosen.emit(value)
            )
            grid.addWidget(button, index // 2, index % 2)
        inner.addLayout(grid)

        layout.addWidget(content, 0, Qt.AlignmentFlag.AlignHCenter)
        layout.addStretch(1)

    def resizeEvent(self, event: Any) -> None:
        super().resizeEvent(event)
        self.content.setFixedWidth(max(0, min(640, self.width() - 24)))

    def showEvent(self, event: Any) -> None:
        super().showEvent(event)
        fade_in(self, duration_ms=320)


class CenteredColumn(QWidget):
    """Hold one child at the conversation's measure, centred in a wide window.

    Laying the child out with an alignment flag instead would hand it its
    sizeHint width, which is narrower than the transcript and leaves the two
    visibly out of line.
    """

    def __init__(self, child: QWidget, max_width: int, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._max_width = max(0, int(max_width))
        self.child = child
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(child)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)

    def resizeEvent(self, event: Any) -> None:  # noqa: N802 - Qt override
        super().resizeEvent(event)
        gutter = max(0, (self.width() - self._max_width) // 2)
        self.layout().setContentsMargins(gutter, 0, gutter, 0)


def read_only_panel(name: str) -> QPlainTextEdit:
    view = QPlainTextEdit()
    view.setObjectName(name)
    view.setReadOnly(True)
    view.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
    return view


def set_panel_text(view: QPlainTextEdit, value: str, *, keep_columns: bool) -> None:
    """Fill a runtime panel, wrapping unless the content needs its columns.

    Command output and diffs are read by column, so they stay unwrapped; prose
    placeholders wrap instead of forcing a horizontal scrollbar across the panel.
    """
    view.setLineWrapMode(
        QPlainTextEdit.LineWrapMode.NoWrap
        if keep_columns
        else QPlainTextEdit.LineWrapMode.WidgetWidth
    )
    view.setPlainText(value)


def thread_row_size(widget: QWidget) -> QSize:
    """Row height driven by the widget itself, not a hard-coded constant."""
    hint = widget.sizeHint()
    return QSize(0, max(52, hint.height()))


__all__ = [
    "ActivityCard",
    "ApprovalCard",
    "Banner",
    "CodeBlock",
    "EmptyState",
    "MessageWidget",
    "RichLabel",
    "ThreadListItemWidget",
    "TranscriptView",
    "copy_to_clipboard",
    "describe_card",
    "read_only_panel",
    "repolish",
    "thread_row_size",
]
