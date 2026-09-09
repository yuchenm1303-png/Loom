"""Native, layout-driven transcript disclosures.

Thought-process and task-detail expansion share one implementation here. The
animation is intentionally structural: a single progress value changes the real
container height, so every later row in the transcript is laid out at its true
intermediate position. There are no viewport screenshots, FLIP overlays,
parallel height animations, or presentation-layer method patches involved.
"""

from __future__ import annotations

import time
from typing import Any

from PySide6.QtCore import (
    Property,
    QEasingCurve,
    QPointF,
    QPropertyAnimation,
    QTimer,
    Qt,
    Signal,
)
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QFrame,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from app.desktop import format as fmt
from app.desktop import output_presentation as presentation
from app.desktop import theme
from app.desktop import widgets as base


DISCLOSURE_OPEN_MS = 190
DISCLOSURE_CLOSE_MS = 145


class AnimatedReveal(QWidget):
    """Reveal one real widget by animating this container's actual height.

    ``progress`` is the only animated property. Geometry and chevrons both read
    the same value, which prevents drift between unrelated animations.
    """

    progressChanged = Signal(float)
    expandedChanged = Signal(bool)

    def __init__(self, content: QWidget, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("animatedReveal")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMinimumHeight(0)
        self.setMaximumHeight(0)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.content = content
        content.setParent(self)
        content.show()
        layout.addWidget(content)

        self._progress = 0.0
        self._target_height = 0
        self._geometry_height = 0
        self._expanded = False
        self._animation: QPropertyAnimation | None = None
        self._resize_pending = False

    @property
    def expanded(self) -> bool:
        return self._expanded

    @property
    def target_height(self) -> int:
        return self._target_height

    def _get_progress(self) -> float:
        return self._progress

    def _set_progress(self, value: float) -> None:
        value = max(0.0, min(1.0, float(value)))
        self._progress = value
        target = max(0, int(self._target_height))
        height = int(round(target * value))

        # Keep one geometry mutation per visible pixel step. ``minimumHeight``
        # remains zero for the lifetime of the reveal; changing both minimum and
        # maximum height plus calling updateGeometry() used to invalidate the Qt
        # layout several times for every animation tick. ``setMaximumHeight`` is
        # sufficient to clamp the Fixed-size reveal and already invalidates its
        # parent layout. The real transcript layout still advances one pixel at a
        # time, but it now performs only the work needed for that new height.
        if height != self._geometry_height:
            self._geometry_height = height
            self.setMaximumHeight(height)

        # Chevron painting still follows the continuous animation progress even
        # on frames where rounding produced the same integer layout height.
        self.progressChanged.emit(value)

    progress = Property(float, _get_progress, _set_progress, notify=progressChanged)

    def _stop_animation(self) -> None:
        animation, self._animation = self._animation, None
        if animation is None:
            return
        try:
            animation.stop()
            animation.deleteLater()
        except RuntimeError:
            pass

    def _measure_target(self) -> int:
        layout = self.layout()
        if layout is not None:
            layout.invalidate()
            layout.activate()
        width = max(1, self.width())
        hint = self.content.sizeHint().height()
        try:
            if self.content.hasHeightForWidth():
                hint = max(hint, int(self.content.heightForWidth(width)))
        except (AttributeError, RuntimeError, TypeError):
            pass
        if layout is not None:
            hint = max(hint, int(layout.sizeHint().height()))
        return max(1, int(hint))

    def refresh_target(self) -> None:
        target = self._measure_target()
        if target == self._target_height:
            return
        self._target_height = target
        if self._animation is None:
            self._set_progress(1.0 if self._expanded else 0.0)
        else:
            self._set_progress(self._progress)

    def set_expanded(self, expanded: bool, *, animate: bool = True) -> None:
        expanded = bool(expanded)
        self._stop_animation()
        self._expanded = expanded
        self.refresh_target()
        target_progress = 1.0 if expanded else 0.0

        if (
            not animate
            or not theme.motion_enabled()
            or abs(self._progress - target_progress) < 0.001
        ):
            self._set_progress(target_progress)
            self.expandedChanged.emit(expanded)
            return

        animation = QPropertyAnimation(self, b"progress", self)
        remaining = abs(target_progress - self._progress)
        base_duration = DISCLOSURE_OPEN_MS if expanded else DISCLOSURE_CLOSE_MS
        animation.setDuration(max(70, int(round(base_duration * remaining))))
        animation.setStartValue(self._progress)
        animation.setEndValue(target_progress)
        animation.setEasingCurve(
            QEasingCurve.Type.OutCubic if expanded else QEasingCurve.Type.InOutCubic
        )

        def finish() -> None:
            self._animation = None
            self._set_progress(target_progress)
            self.expandedChanged.emit(expanded)
            try:
                animation.deleteLater()
            except RuntimeError:
                pass

        animation.finished.connect(finish)
        self._animation = animation
        animation.start()

    def resizeEvent(self, event: Any) -> None:  # noqa: N802 - Qt override
        super().resizeEvent(event)
        if not self._expanded or self._animation is not None or self._resize_pending:
            return
        self._resize_pending = True

        def settle() -> None:
            self._resize_pending = False
            try:
                self.refresh_target()
            except RuntimeError:
                pass

        QTimer.singleShot(0, settle)


class DisclosureButton(QPushButton):
    """One chevron presentation for both text and icon-only disclosures.

    The button owns no animation. It only renders the same progress value that
    drives :class:`AnimatedReveal`, so arrow and layout cannot drift apart.
    """

    def __init__(
        self,
        text: str = "",
        parent: QWidget | None = None,
        *,
        compact: bool = False,
    ) -> None:
        super().__init__(text, parent)
        self._progress = 0.0
        self._compact = bool(compact)
        self.setProperty("expanded", False)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFlat(True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        if compact:
            self.setObjectName("activityDisclosure")
            self.setFixedSize(20, 20)
            self.setStyleSheet(
                "QPushButton#activityDisclosure {"
                " min-width:20px; max-width:20px; min-height:20px; max-height:20px;"
                " background:transparent; border:none; border-radius:6px; padding:0; margin:0;"
                "}"
                "QPushButton#activityDisclosure:hover { background:#1a1e25; }"
                "QPushButton#activityDisclosure:pressed { background:#20252d; }"
            )
        else:
            self.setObjectName("reasoningToggle")
            self.setMinimumHeight(26)
            self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)

    def _repolish(self) -> None:
        style = self.style()
        style.unpolish(self)
        style.polish(self)
        self.update()

    def set_progress(self, value: float) -> None:
        value = max(0.0, min(1.0, float(value)))
        was_expanded = bool(self.property("expanded"))
        self._progress = value
        is_expanded = value >= 0.999
        if was_expanded != is_expanded:
            self.setProperty("expanded", is_expanded)
            self._repolish()
        else:
            self.update()

    def set_expanded(self, expanded: bool, *, animate: bool = False) -> None:
        """Compatibility for presentation code that only synchronizes state.

        Real animation remains exclusively owned by ``AnimatedReveal``.
        """
        self.set_progress(1.0 if expanded else 0.0)

    @property
    def progress(self) -> float:
        return self._progress

    def paintEvent(self, event: Any) -> None:  # noqa: N802 - Qt override
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        if self.isDown():
            color = QColor("#e4e8ef")
        elif self.underMouse():
            color = QColor("#bdc4cf")
        else:
            color = QColor("#7d8694")
        if not self.isEnabled():
            color.setAlpha(80)

        x = self.width() / 2.0 if self._compact else 11.5
        painter.translate(x, self.height() / 2.0)
        painter.rotate(90.0 * self._progress)
        painter.translate(-0.2, 0.0)
        painter.setPen(
            QPen(
                color,
                1.25,
                Qt.PenStyle.SolidLine,
                Qt.PenCapStyle.RoundCap,
                Qt.PenJoinStyle.RoundJoin,
            )
        )
        painter.setBrush(Qt.BrushStyle.NoBrush)
        path = QPainterPath()
        path.moveTo(QPointF(-2.25, -3.25))
        path.lineTo(QPointF(1.15, 0.0))
        path.lineTo(QPointF(-2.25, 3.25))
        painter.drawPath(path)


_REASONING_QSS = f"""
QFrame#reasoningBlock {{
    background:transparent;
    border:none;
}}
QPushButton#reasoningToggle {{
    background:transparent;
    border:none;
    border-radius:6px;
    padding:2px 8px 2px 24px;
    color:#8b93a3;
    font-family:{theme.FONT_UI};
    font-size:12px;
    font-weight:560;
    text-align:left;
    min-height:22px;
}}
QPushButton#reasoningToggle:hover {{
    background:#101319;
    color:#c3c9d4;
}}
QPushButton#reasoningToggle:pressed {{
    background:#131720;
    color:#e0e4eb;
}}
QPushButton#reasoningToggle[expanded="true"] {{
    background:transparent;
    color:#b8bfca;
}}
QLabel#reasoningBody {{
    background:transparent;
    border:none;
    border-left:2px solid #3d3a55;
    margin-left:11px;
    padding:5px 10px 6px 11px;
    color:#9ca4b3;
    font-family:{theme.FONT_UI};
    font-size:12px;
}}
"""


class ReasoningDisclosure(QFrame):
    """Reasoning disclosure backed directly by :class:`AnimatedReveal`."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("reasoningBlock")
        self.setStyleSheet(_REASONING_QSS)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        self.toggle = DisclosureButton("Thought process", self)
        layout.addWidget(self.toggle, 0, Qt.AlignmentFlag.AlignLeft)

        self.body = QLabel(self)
        self.body.setObjectName("reasoningBody")
        self.body.setWordWrap(True)
        self.body.setTextFormat(Qt.TextFormat.PlainText)
        self.body.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.reveal = AnimatedReveal(self.body, self)
        layout.addWidget(self.reveal)

        self.toggle.clicked.connect(self._toggle)
        self.reveal.progressChanged.connect(self.toggle.set_progress)

        self._text = ""
        self._live = False
        self._expanded = False
        self._started: float | None = None
        self._seconds = 0
        self._clock = QTimer(self)
        self._clock.setInterval(1000)
        self._clock.timeout.connect(self._sync_label)
        self.hide()

    def set_reasoning(self, text: str, *, live: bool) -> None:
        text = text or ""
        if not text:
            self._clock.stop()
            self._started = None
            self.set_expanded(False, animate=False)
            self.hide()
            return

        if self._started is None:
            self._started = time.monotonic()
        if text != self._text:
            self._text = text
            self.body.setText(text)
            QTimer.singleShot(0, self.reveal.refresh_target)
        if live != self._live:
            self._live = live
            if live and theme.motion_enabled():
                self._clock.start()
            elif not live:
                self._clock.stop()
                self._seconds = self._elapsed()
        self.show()
        self._sync_label()

    def finish(self) -> None:
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
            self.toggle.setText(
                f"Thought for {fmt.elapsed_label(seconds)}" if seconds >= 2 else "Thought process"
            )
        self.toggle.setToolTip("Hide thought process" if self._expanded else "Show thought process")

    def set_expanded(self, expanded: bool, *, animate: bool = True) -> None:
        self._expanded = bool(expanded)
        self.reveal.set_expanded(self._expanded, animate=animate)
        self._sync_label()

    def _toggle(self) -> None:
        self.set_expanded(not self._expanded, animate=True)


def _header_layout(card: Any) -> Any | None:
    outer = card.layout()
    if outer is None:
        return None
    for index in range(outer.count()):
        candidate = outer.itemAt(index).layout()
        if candidate is not None and candidate.indexOf(card.toggle_button) >= 0:
            return candidate
    return None


def _replace_activity_toggle(card: Any) -> None:
    old = card.toggle_button
    header = _header_layout(card)
    index = header.indexOf(old) if header is not None else -1
    if header is not None:
        header.removeWidget(old)
    old.hide()
    old.deleteLater()

    button = DisclosureButton("", card, compact=True)
    button.clicked.connect(card._toggle)
    if header is not None and index >= 0:
        header.insertWidget(index, button)
    elif header is not None:
        header.addWidget(button)
    card.toggle_button = button


class FlowActivityCard(presentation.FlatActivityCard):
    """Task row whose details participate in the real transcript layout."""

    def __init__(self, kind: str, parent: QWidget | None = None) -> None:
        super().__init__(kind, parent)
        _replace_activity_toggle(self)

        outer = self.layout()
        body_index = outer.indexOf(self.body_shell) if outer is not None else -1
        if outer is not None:
            outer.removeWidget(self.body_shell)
        self.reveal = AnimatedReveal(self.body_shell, self)
        if outer is not None and body_index >= 0:
            outer.insertWidget(body_index, self.reveal)
        elif outer is not None:
            outer.addWidget(self.reveal)

        self.reveal.progressChanged.connect(self.toggle_button.set_progress)
        self._body_animation = None
        self._sync_body(animate=False)

    def set_expanded(
        self,
        expanded: bool,
        *,
        animate: bool = True,
        user: bool = False,
    ) -> None:
        if user:
            self._user_toggled = True
        self._expanded = bool(expanded)
        self._sync_body(animate=animate)

    def _toggle(self) -> None:
        self.set_expanded(not self._expanded, animate=True, user=True)

    def _sync_body(self, *, animate: bool = False) -> None:
        has_body = bool(self._body_text)
        show = bool(has_body and self._expanded)
        self.toggle_button.setVisible(has_body)
        self.toggle_button.setToolTip("Hide output" if show else "Show output")
        if show:
            super()._sync_height()
        self.reveal.refresh_target()
        self.reveal.set_expanded(show, animate=animate)

    def _sync_height(self) -> None:
        super()._sync_height()
        reveal = getattr(self, "reveal", None)
        if reveal is not None:
            reveal.refresh_target()


class FlowMessageWidget(presentation.MessageWidget):
    """Existing message presentation with one native reasoning disclosure."""

    def __init__(self, role: str, parent: QWidget | None = None) -> None:
        super().__init__(role, parent)
        if role != "assistant":
            return
        old = self.reasoning
        layout = self.layout()
        index = layout.indexOf(old) if layout is not None and old is not None else -1
        if old is not None and layout is not None:
            layout.removeWidget(old)
        if old is not None:
            old.hide()
            old.deleteLater()
        self.reasoning = ReasoningDisclosure(self)
        if layout is not None:
            layout.insertWidget(max(0, index), self.reasoning)


class FlowTranscriptView(presentation.TranscriptView):
    """Transcript that constructs the canonical disclosure-enabled widgets."""

    def _build(self, entry: Any) -> QWidget:
        # Preserve late-installed user-message shells and their action row.
        if entry.kind == "user":
            return super()._build(entry)
        if entry.kind == "assistant":
            return FlowMessageWidget(entry.kind, self.canvas)
        if self._max_content_width and entry.kind in {"tool", "process", "diff", "error"}:
            return FlowActivityCard(entry.kind, self.canvas)
        return base.ActivityCard(entry.kind, self.canvas)


__all__ = [
    "AnimatedReveal",
    "DisclosureButton",
    "FlowActivityCard",
    "FlowMessageWidget",
    "FlowTranscriptView",
    "ReasoningDisclosure",
]
