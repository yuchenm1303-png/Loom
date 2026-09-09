"""Quiet presentation for the assistant reasoning disclosure.

The thought-process affordance should read like transcript chrome, not a card.
This module owns the visual treatment and chevron.  Transcript geometry/motion
is coordinated later by ``disclosure_motion`` so there is one choreography for
reasoning and task details instead of stacked animation hooks.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QEasingCurve, QPointF, QPropertyAnimation, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QPushButton, QSizePolicy, QWidget

from app.desktop import message_presentation as presentation
from app.desktop import theme
from app.desktop.disclosure_motion_tokens import CHEVRON_CLOSE_MS, CHEVRON_OPEN_MS


_INSTALLED = False

# Thought process is deliberately integrated into the transcript. The toggle is
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
    animation.setDuration(CHEVRON_OPEN_MS if expanded else CHEVRON_CLOSE_MS)
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
    """Install reasoning-specific visual polish once without owning geometry."""
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
        # Atomic fallback for standalone ReasoningBlock users. The public Desktop
        # package installs disclosure_motion after this module and replaces this
        # method with the shared transcript choreography.
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
        self.body.setMaximumHeight(16_777_215)
        self.body.setVisible(show)
        self._settle_layout(again=show)

    presentation.ReasoningToggle.paintEvent = _toggle_paint
    presentation.ReasoningToggle.set_expanded = _toggle_set_expanded
    presentation.ReasoningBlock.__init__ = reasoning_init
    presentation.ReasoningBlock._sync_body = sync_body


__all__ = ["install"]
