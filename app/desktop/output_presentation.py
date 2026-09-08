"""Polished Codex-style presentation for the main agent transcript.

The conversation reads as one continuous document. Assistant prose stays flat,
while inline tool/process activity uses a compact disclosure row and one quiet
output surface when expanded. The Runtime inspector keeps its richer cards.

Motion is intentionally restrained:
- new transcript rows still use the shared fade-in;
- disclosure chevrons rotate instead of snapping glyphs;
- output expands/collapses with height + opacity easing;
- live tool icons pulse while work is running;
- status changes fade in softly;
- LOOM_REDUCE_MOTION disables all custom motion.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import (
    QEasingCurve,
    QPointF,
    Property,
    QPropertyAnimation,
    Qt,
)
from PySide6.QtGui import (
    QColor,
    QPainter,
    QPen,
    QSyntaxHighlighter,
    QTextCharFormat,
)
from PySide6.QtWidgets import (
    QGraphicsOpacityEffect,
    QPushButton,
    QWidget,
)

from app.desktop import theme
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


_ACTIVITY_POLISHED_QSS = f"""
QFrame#activityCard,
QFrame#activityCard:hover,
QFrame#activityCard[state="completed"],
QFrame#activityCard[state="running"],
QFrame#activityCard[state="started"],
QFrame#activityCard[state="waiting"],
QFrame#activityCard[state="waiting_approval"],
QFrame#activityCard[state="failed"],
QFrame#activityCard[state="denied"],
QFrame#activityCard[state="cancelled"] {{
    background: transparent;
    border: none;
    border-radius: 0;
}}
QLabel#cardTitle {{
    background: transparent;
    color: #eceff4;
    font-size: 13px;
    font-weight: 650;
}}
QLabel#cardSubtitle {{
    background: transparent;
    color: #707887;
    font-family: {theme.FONT_MONO};
    font-size: 10px;
    padding-top: 1px;
}}
QFrame#cardBodyShell {{
    background: #151719;
    border: 1px solid #2a2e33;
    border-radius: 10px;
}}
QFrame#cardBodyShell[state="running"],
QFrame#cardBodyShell[state="started"] {{
    border-color: #2b3644;
}}
QFrame#cardBodyShell[state="failed"],
QFrame#cardBodyShell[state="denied"],
QFrame#cardBodyShell[state="cancelled"] {{
    border-color: #4a2b31;
}}
QFrame#cardBodyShell[state="waiting"],
QFrame#cardBodyShell[state="waiting_approval"] {{
    border-color: #4a3a24;
}}
QLabel#cardBodyTitle {{
    background: transparent;
    border: none;
    color: #b7bbc2;
    font-size: 11px;
    font-weight: 620;
}}
QPlainTextEdit#cardBody {{
    background: transparent;
    border: none;
    color: #c5c9cf;
    font-family: {theme.FONT_MONO};
    font-size: 12px;
    padding: 2px 1px 1px 1px;
    selection-background-color: #343742;
}}
QLabel#cardStatus {{
    background: transparent;
    border: none;
    border-radius: 0;
    padding: 0;
    color: #8f959f;
    font-size: 10px;
    font-weight: 620;
}}
QLabel#cardStatus[state="completed"] {{ color: #91aa9e; }}
QLabel#cardStatus[state="running"],
QLabel#cardStatus[state="started"] {{ color: #91a9c5; }}
QLabel#cardStatus[state="failed"],
QLabel#cardStatus[state="denied"],
QLabel#cardStatus[state="cancelled"] {{ color: {theme.BAD}; }}
QLabel#cardStatus[state="waiting"],
QLabel#cardStatus[state="waiting_approval"] {{ color: {theme.WARN}; }}
"""


class AnimatedChevronButton(QPushButton):
    """Small disclosure control whose chevron rotates between > and v."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._angle = 0.0
        self._animation: QPropertyAnimation | None = None
        self.setFixedSize(26, 26)
        self.setText("")
        self.setFlat(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setStyleSheet(
            "QPushButton { background:transparent; border:none; border-radius:7px; }"
            "QPushButton:hover { background:#14171c; }"
            "QPushButton:pressed { background:#101216; }"
        )

    def _get_angle(self) -> float:
        return self._angle

    def _set_angle(self, value: float) -> None:
        value = float(value)
        if value == self._angle:
            return
        self._angle = value
        self.update()

    angle = Property(float, _get_angle, _set_angle)

    def set_expanded(self, expanded: bool, *, animate: bool) -> None:
        target = 90.0 if expanded else 0.0
        if self._animation is not None:
            self._animation.stop()
            self._animation.deleteLater()
            self._animation = None

        if not animate or not theme.motion_enabled() or abs(self._angle - target) < 0.5:
            self._set_angle(target)
            return

        animation = QPropertyAnimation(self, b"angle", self)
        animation.setDuration(150)
        animation.setStartValue(self._angle)
        animation.setEndValue(target)
        animation.setEasingCurve(QEasingCurve.Type.OutCubic)

        def finish() -> None:
            self._set_angle(target)
            self._animation = None
            animation.deleteLater()

        animation.finished.connect(finish)
        self._animation = animation
        animation.start()

    def paintEvent(self, _event: Any) -> None:  # noqa: N802 - Qt override
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.translate(self.width() / 2, self.height() / 2)
        painter.rotate(self._angle)

        color = QColor("#c7cbd2" if self.underMouse() else "#8b93a0")
        if not self.isEnabled():
            color.setAlpha(90)
        painter.setPen(
            QPen(
                color,
                1.45,
                Qt.PenStyle.SolidLine,
                Qt.PenCapStyle.RoundCap,
                Qt.PenJoinStyle.RoundJoin,
            )
        )
        painter.drawLine(QPointF(-2.7, -4.0), QPointF(2.2, 0.0))
        painter.drawLine(QPointF(2.2, 0.0), QPointF(-2.7, 4.0))


class TerminalHighlighter(QSyntaxHighlighter):
    """Very light terminal colouring; enough hierarchy without becoming an IDE."""

    def __init__(self, document: Any) -> None:
        super().__init__(document)
        self._prompt = QTextCharFormat()
        self._prompt.setForeground(QColor("#818894"))
        self._command = QTextCharFormat()
        self._command.setForeground(QColor("#e5e7eb"))
        self._good = QTextCharFormat()
        self._good.setForeground(QColor(theme.GOOD))
        self._bad = QTextCharFormat()
        self._bad.setForeground(QColor(theme.BAD))
        self._muted = QTextCharFormat()
        self._muted.setForeground(QColor("#a2a7ae"))

    def _mark_all(self, text: str, needle: str, fmt: QTextCharFormat) -> None:
        start = 0
        while True:
            index = text.find(needle, start)
            if index < 0:
                return
            self.setFormat(index, len(needle), fmt)
            start = index + len(needle)

    def highlightBlock(self, text: str) -> None:  # noqa: N802 - Qt override
        stripped = text.lstrip()
        offset = len(text) - len(stripped)
        if stripped.startswith("$ "):
            self.setFormat(offset, 1, self._prompt)
            if len(stripped) > 2:
                self.setFormat(offset + 2, len(stripped) - 2, self._command)

        for token in ("FAILED", "ERROR", "Error", "AssertionError", "failed"):
            self._mark_all(text, token, self._bad)
        for token in ("passed", "[100%]", "SUCCESS", "Success", "completed"):
            self._mark_all(text, token, self._good)
        for token in ("skipped", "deselected", "warning"):
            self._mark_all(text, token, self._muted)


class MessageWidget(AnimatedMessageWidget):
    """Assistant output reads as prose directly on the transcript canvas."""

    def __init__(self, role: str, parent: QWidget | None = None) -> None:
        super().__init__(role, parent)
        if role != "assistant":
            return

        self.setStyleSheet(_ASSISTANT_PLAIN_QSS)
        layout = self.layout()
        if layout is not None:
            layout.setContentsMargins(0, 2, 0, 5)
            layout.setSpacing(5)


class FlatActivityCard(base.ActivityCard):
    """Compact inline tool/process presentation inspired by Codex command rows."""

    _STATUS_TEXT = {
        "completed": "✓  Success",
        "running": "•  Running",
        "started": "•  Running",
        "failed": "×  Failed",
        "denied": "×  Denied",
        "cancelled": "×  Cancelled",
        "waiting": "•  Waiting",
        "waiting_approval": "!  Approval required",
    }

    def __init__(self, kind: str, parent: QWidget | None = None) -> None:
        super().__init__(kind, parent)
        self.setStyleSheet(_ACTIVITY_POLISHED_QSS)
        self._last_status = ""
        self._status_fade: QPropertyAnimation | None = None

        # References use a small semantic icon in the command row, not a framed
        # dashboard tile. Keep it crisp and let Runtime state tint/pulse it.
        self.icon.framed = False
        self.icon.setFixedSize(20, 20)
        self.icon.show()

        # Replace the snapping text glyph with a native rotating chevron.
        old_toggle = self.toggle_button
        outer = self.layout()
        header_layout = outer.itemAt(0).layout() if outer is not None and outer.count() else None
        index = header_layout.indexOf(old_toggle) if header_layout is not None else -1
        if header_layout is not None:
            header_layout.removeWidget(old_toggle)
        old_toggle.hide()
        old_toggle.deleteLater()

        self.toggle_button = AnimatedChevronButton(self)
        self.toggle_button.clicked.connect(self._toggle)
        if header_layout is not None and index >= 0:
            header_layout.insertWidget(index, self.toggle_button)
        elif header_layout is not None:
            header_layout.addWidget(self.toggle_button)

        if outer is not None:
            outer.setContentsMargins(0, 4, 0, 5)
            outer.setSpacing(7)

        shell_layout = self.body_shell.layout()
        if shell_layout is not None:
            shell_layout.setContentsMargins(13, 10, 13, 10)
            shell_layout.setSpacing(7)

        # The references label the output surface once. It is useful hierarchy,
        # unlike the removed outer tool-card chrome.
        self.body_title.show()
        if kind == "process":
            self.body_title.setText("Shell")
        elif kind == "diff":
            self.body_title.setText("Changes")
        elif kind == "error":
            self.body_title.setText("Error")
        else:
            self.body_title.setText("Tool output")

        if kind == "process":
            self._terminal_highlighter = TerminalHighlighter(self.body.document())
        else:
            self._terminal_highlighter = None

    def _presented_title(self, title: str, status: str) -> str:
        if self.kind != "process":
            return title
        command = title[2:] if title.startswith("$ ") else title
        if status in {"running", "started"}:
            return f"Running {command}"
        if status == "completed":
            return f"Ran {command}"
        return command

    def _animate_status(self) -> None:
        if not self.status_label.isVisible() or not theme.motion_enabled():
            return
        if self._status_fade is not None:
            self._status_fade.stop()
            self._status_fade.deleteLater()
            self._status_fade = None
        self.status_label.setGraphicsEffect(None)
        effect = QGraphicsOpacityEffect(self.status_label)
        self.status_label.setGraphicsEffect(effect)
        effect.setOpacity(0.28)
        animation = QPropertyAnimation(effect, b"opacity", self.status_label)
        animation.setDuration(170)
        animation.setStartValue(0.28)
        animation.setEndValue(1.0)
        animation.setEasingCurve(QEasingCurve.Type.OutCubic)

        def finish() -> None:
            self.status_label.setGraphicsEffect(None)
            self._status_fade = None
            animation.deleteLater()

        animation.finished.connect(finish)
        self._status_fade = animation
        animation.start()

    def _sync_body(self, *, animate: bool = False) -> None:
        has_body = bool(self._body_text)
        show = has_body and self._expanded
        self.toggle_button.setVisible(has_body)
        self.toggle_button.set_expanded(show, animate=animate)
        self.toggle_button.setToolTip("Hide output" if show else "Show output")
        super()._sync_body(animate=animate)

    def update_card(
        self,
        *,
        title: str,
        subtitle: str = "",
        status: str = "",
        body: str = "",
        auto_expand: bool | None = None,
    ) -> None:
        # A process that was visibly running should not abruptly collapse the
        # instant it succeeds. Completed historical rows remain collapsed until
        # the user opens them, but live rows stay open through completion.
        if (
            self.kind == "process"
            and status == "completed"
            and self._expanded
            and not self._user_toggled
        ):
            auto_expand = True

        full_subtitle = subtitle
        if self.kind == "process":
            # Command + output already contain the useful information. Keep cwd /
            # exit metadata in the tooltip rather than adding another visible line.
            subtitle = ""
        elif subtitle:
            compact = " ".join(subtitle.split())
            subtitle = compact if len(compact) <= 128 else compact[:125] + "…"

        super().update_card(
            title=title,
            subtitle=subtitle,
            status=status,
            body=body,
            auto_expand=auto_expand,
        )

        # Tool icons adapt to familiar semantics when the tool name makes it clear.
        lowered = title.casefold()
        if self.kind == "process" or lowered in {"exec", "exec_write"} or "command" in lowered:
            self.icon.name = "terminal"
        elif "browser" in lowered or "navigate" in lowered or "computer" in lowered:
            self.icon.name = "browser"
        elif self.kind == "diff":
            self.icon.name = "diff"
        elif self.kind == "error":
            self.icon.name = "error"
        else:
            self.icon.name = "tool"
        self.icon.update()

        if full_subtitle:
            self.setToolTip(f"{self._full_title}\n{full_subtitle}")
        else:
            self.setToolTip(self._full_title)

        if self.body_shell.property("state") != status:
            self.body_shell.setProperty("state", status)
            base.repolish(self.body_shell)

        label = self._STATUS_TEXT.get(status, "")
        self.status_label.setText(label)
        self.status_label.setVisible(bool(label))

        if status != self._last_status:
            self._last_status = status
            self._animate_status()

        # Base class may have changed expansion state without animation because
        # the update came from Runtime. Keep chevron geometry in sync immediately.
        self.toggle_button.set_expanded(
            bool(self._body_text and self._expanded),
            animate=False,
        )


class TranscriptView(AnimatedTranscriptView):
    """Main transcript with flat prose and polished inline Runtime activity."""

    def _build(self, entry: TranscriptEntry) -> QWidget:
        if entry.kind in {"user", "assistant"}:
            return MessageWidget(entry.kind, self.canvas)

        # CardListView creates TranscriptView(max_content_width=0) for the Runtime
        # inspector. Keep those inspector cards intact; only the central agent
        # transcript gets the lightweight command/output presentation.
        if self._max_content_width and entry.kind in {"tool", "process", "diff", "error"}:
            return FlatActivityCard(entry.kind, self.canvas)
        return base.ActivityCard(entry.kind, self.canvas)


__all__ = [
    "AnimatedChevronButton",
    "FlatActivityCard",
    "MessageWidget",
    "TerminalHighlighter",
    "TranscriptView",
]
