"""Quiet presentation and motion for the assistant reasoning disclosure.

The thought-process affordance should read like transcript chrome, not a card.
This module keeps the durable message/reasoning behavior in
``message_presentation`` intact and only refines its visual hierarchy and
motion.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QEasingCurve, QParallelAnimationGroup, QPointF, QPropertyAnimation, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QGraphicsOpacityEffect, QPushButton, QSizePolicy, QWidget

from app.desktop import message_presentation as presentation
from app.desktop import theme


_INSTALLED = False
_OPEN_MS = 215
_CLOSE_MS = 165
_CHEVRON_OPEN_MS = 185
_CHEVRON_CLOSE_MS = 145

# Thought process is deliberately integrated into the transcript.  The toggle is
# a quiet disclosure row rather than a pill/card, and the expanded body uses only
# a thin inset rule so reasoning stays visually subordinate to the final answer.
_REASONING_POLISHED_QSS = f"""
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
QPushButton#reasoningToggle[expanded="true"]:hover {{
    background:#101319;
    color:#d4d9e1;
}}
QLabel#reasoningBody {{
    background:transparent;
    border:none;
    border-left:2px solid #3d3a55;
    border-radius:0;
    margin-left:11px;
    padding:5px 10px 6px 11px;
    color:#9ca4b3;
    font-family:{theme.FONT_UI};
    font-size:12px;
}}
QLabel#reasoningBody:disabled {{
    color:#7f8796;
}}
"""


def _repolish(widget: QWidget) -> None:
    style = widget.style()
    style.unpolish(widget)
    style.polish(widget)
    widget.update()


def _toggle_paint(self: Any, event: Any) -> None:
    """Paint a restrained native chevron with a stable optical centre."""
    QPushButton.paintEvent(self, event)
    painter = QPainter(self)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)

    expanded = bool(self.property("expanded"))
    if self.isDown():
        color = QColor("#e5e8ef")
    elif self.underMouse():
        color = QColor("#bcc3cf")
    elif expanded:
        color = QColor("#9098a9")
    else:
        color = QColor("#6f7788")

    painter.translate(11.5, self.height() / 2.0)
    painter.rotate(float(self._angle))
    painter.translate(-0.2, 0.0)
    painter.setPen(
        QPen(
            color,
            1.3,
            Qt.PenStyle.SolidLine,
            Qt.PenCapStyle.RoundCap,
            Qt.PenJoinStyle.RoundJoin,
        )
    )
    painter.setBrush(Qt.BrushStyle.NoBrush)
    path = QPainterPath()
    path.moveTo(QPointF(-2.2, -3.25))
    path.lineTo(QPointF(1.15, 0.0))
    path.lineTo(QPointF(-2.2, 3.25))
    painter.drawPath(path)


def _toggle_set_expanded(self: Any, expanded: bool, *, animate: bool) -> None:
    target = 90.0 if expanded else 0.0
    self._release_animation()
    self.setProperty("expanded", bool(expanded))
    _repolish(self)

    if not animate or not theme.motion_enabled() or abs(float(self._angle) - target) < 0.5:
        self._set_angle(target)
        return

    animation = QPropertyAnimation(self, b"angle", self)
    animation.setDuration(_CHEVRON_OPEN_MS if expanded else _CHEVRON_CLOSE_MS)
    animation.setStartValue(float(self._angle))
    animation.setEndValue(target)
    animation.setEasingCurve(QEasingCurve.Type.OutCubic)

    def finish() -> None:
        self._animation = None
        self._set_angle(target)

    animation.finished.connect(finish)
    self._animation = animation
    animation.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)


def install() -> None:
    """Install reasoning-specific polish once without changing message semantics."""
    global _INSTALLED
    if _INSTALLED:
        return
    _INSTALLED = True

    presentation._REASONING_QSS = _REASONING_POLISHED_QSS

    original_init = presentation.ReasoningBlock.__init__

    def reasoning_init(self: Any, parent: QWidget | None = None) -> None:
        original_init(self, parent)
        layout = self.layout()
        if layout is not None:
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setSpacing(4)
        self.toggle.setMinimumHeight(26)
        self.toggle.setProperty("expanded", False)
        self.toggle.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        self.body.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.body.setMinimumWidth(0)
        _repolish(self.toggle)

    def sync_body(self: Any, *, animate: bool) -> None:
        show = bool(self._expanded)
        self.toggle.set_expanded(show, animate=animate)
        self.toggle.setToolTip("Hide thought process" if show else "Show thought process")

        if self._animation is not None:
            try:
                self._animation.stop()
            except RuntimeError:
                pass
            self._animation = None
        self.body.setGraphicsEffect(None)

        if not animate or not theme.motion_enabled():
            self.body.setMaximumHeight(16777215)
            self.body.setVisible(show)
            self._settle_layout()
            return

        available_width = max(180, self.width() - 18)
        natural = max(self.body.sizeHint().height(), self.body.heightForWidth(available_width), 1)
        start = self.body.height() if self.body.isVisible() else 0
        start = max(0, min(int(start), int(natural)))

        self.body.setVisible(True)
        self.body.setMaximumHeight(start)

        effect = QGraphicsOpacityEffect(self.body)
        self.body.setGraphicsEffect(effect)
        effect.setOpacity(1.0 if start > 0 else 0.0)

        group = QParallelAnimationGroup(self)
        height = QPropertyAnimation(self.body, b"maximumHeight", group)
        height.setDuration(_OPEN_MS if show else _CLOSE_MS)
        height.setStartValue(start)
        height.setEndValue(natural if show else 0)
        height.setEasingCurve(
            QEasingCurve.Type.OutCubic if show else QEasingCurve.Type.InOutCubic
        )

        opacity = QPropertyAnimation(effect, b"opacity", group)
        opacity.setDuration(160 if show else 105)
        opacity.setStartValue(1.0 if start > 0 else 0.0)
        opacity.setEndValue(1.0 if show else 0.0)
        opacity.setEasingCurve(QEasingCurve.Type.OutCubic)

        height.valueChanged.connect(lambda _value: self._settle_layout(again=False))
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

    presentation.ReasoningToggle.paintEvent = _toggle_paint
    presentation.ReasoningToggle.set_expanded = _toggle_set_expanded
    presentation.ReasoningBlock.__init__ = reasoning_init
    presentation.ReasoningBlock._sync_body = sync_body


__all__ = ["install"]
