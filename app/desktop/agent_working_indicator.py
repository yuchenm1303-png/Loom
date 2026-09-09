"""Immediate transcript-level feedback after a user submits a prompt.

The assistant message widget can only show its streaming chrome after the App
Server has emitted an assistant item. There is a small but very visible gap
between pressing Enter and that first item, especially when the agent starts by
running tools. This module fills that gap with a single lightweight row at the
transcript tail:

    •  Working…

The row appears immediately after the optimistic user message, follows tool rows
at the tail while the agent is still working, and disappears as soon as the
assistant starts producing its own message chrome, waits for approval, or the
turn ends.

The indicator is presentation chrome, not transcript data. It may move when a
new structural row is appended, but content-only stream frames must never remove
and reinsert it or force the scrollbar; those jobs belong to the keyed layout and
TranscriptView's native tail-follow policy respectively.
"""

from __future__ import annotations

import math
from typing import Any

from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QHBoxLayout, QLabel, QWidget

from app.desktop import format as fmt
from app.desktop import output_presentation
from app.desktop import theme


_WIDGETS_INSTALLED = False


class AgentWorkingGlyph(QWidget):
    """Small pulsing agent-presence dot; paint-only, so it never reflows text."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._phase = 0.0
        self._active = False
        self.setObjectName("agentWorkingGlyph")
        self.setFixedSize(12, 12)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

        self._timer = QTimer(self)
        self._timer.setInterval(64)
        self._timer.timeout.connect(self._advance)

    def set_active(self, active: bool) -> None:
        self._active = bool(active)
        if self._active and theme.motion_enabled():
            if not self._timer.isActive():
                self._timer.start()
        else:
            self._timer.stop()
        self.update()

    def _advance(self) -> None:
        self._phase = (self._phase + 0.075) % 1.0
        self.update()

    def paintEvent(self, _event: Any) -> None:  # noqa: N802 - Qt override
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)

        wave = (math.sin(self._phase * math.tau) + 1.0) / 2.0
        halo = QColor(theme.ACCENT_SOFT)
        halo.setAlpha(int(18 + 34 * wave) if self._active else 24)
        painter.setBrush(halo)
        painter.drawEllipse(1, 1, 10, 10)

        dot = QColor(theme.ACCENT_SOFT)
        dot.setAlpha(int(145 + 90 * wave) if self._active else 176)
        painter.setBrush(dot)
        painter.drawEllipse(4, 4, 4, 4)


class AgentWorkingIndicator(QWidget):
    """Compact inline status row shown before the first assistant item exists."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("agentWorkingIndicator")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setStyleSheet(
            "QWidget#agentWorkingIndicator, QWidget#agentWorkingGlyph { background:transparent; }"
            "QLabel#agentWorkingLabel {"
            " background:transparent; color:#8f95a7; font-size:12px; font-weight:600;"
            "}"
        )

        layout = QHBoxLayout(self)
        layout.setContentsMargins(2, 3, 0, 5)
        layout.setSpacing(7)

        self.glyph = AgentWorkingGlyph(self)
        layout.addWidget(self.glyph, 0, Qt.AlignmentFlag.AlignVCenter)

        self.label = QLabel("Working…", self)
        self.label.setObjectName("agentWorkingLabel")
        layout.addWidget(self.label, 0, Qt.AlignmentFlag.AlignVCenter)
        layout.addStretch(1)

        self._active = False
        self.hide()

    @property
    def active(self) -> bool:
        return self._active

    def set_active(self, active: bool) -> None:
        active = bool(active)
        self._active = active
        self.glyph.set_active(active)
        self.setVisible(active)


def _move_indicator_to_tail(view: Any) -> bool:
    """Move the indicator only when a structural append displaced it."""
    indicator = getattr(view, "agent_working_indicator", None)
    layout = getattr(view, "_layout", None)
    if indicator is None or layout is None:
        return False

    current = layout.indexOf(indicator)
    if current >= 0:
        # The layout ends in one stretch. The indicator belongs immediately
        # before that stretch; when it is already there, touching the layout is
        # pure churn and caused a visible reflow on every streamed token.
        desired = max(0, layout.count() - 2)
        if current == desired:
            return False
        layout.removeWidget(indicator)

    layout.insertWidget(
        max(0, layout.count() - 1),
        indicator,
        0,
        Qt.AlignmentFlag.AlignLeft,
    )
    return True


def install_widgets() -> None:
    """Add a persistent tail indicator API to the real transcript subclass."""
    global _WIDGETS_INSTALLED
    if _WIDGETS_INSTALLED:
        return
    _WIDGETS_INSTALLED = True

    cls = output_presentation.TranscriptView
    original_init = cls.__init__
    original_render = cls.render
    original_clear = cls.clear

    def transcript_init(self: Any, *args: Any, **kwargs: Any) -> None:
        original_init(self, *args, **kwargs)
        indicator = AgentWorkingIndicator(self.canvas)
        self.agent_working_indicator = indicator
        self._layout.insertWidget(
            max(0, self._layout.count() - 1),
            indicator,
            0,
            Qt.AlignmentFlag.AlignLeft,
        )

    def set_agent_working(self: Any, active: bool) -> None:
        indicator = getattr(self, "agent_working_indicator", None)
        if indicator is None:
            return
        # Showing a new tail row may require one structural move. Hiding it does
        # not: visibility alone reclaims its space without disturbing siblings.
        if active:
            _move_indicator_to_tail(self)
        indicator.set_active(active)
        # Do not call scroll_to_tail here. send_prompt already establishes tail
        # intent, and rangeChanged follows the extra indicator height naturally.

    def render(self: Any, entries: list[Any]) -> None:
        original_render(self, entries)
        indicator = getattr(self, "agent_working_indicator", None)
        if indicator is not None and indicator.active:
            _move_indicator_to_tail(self)

    def clear(self: Any) -> None:
        original_clear(self)
        indicator = getattr(self, "agent_working_indicator", None)
        if indicator is not None:
            indicator.set_active(False)

    cls.__init__ = transcript_init
    cls.set_agent_working = set_agent_working
    cls.render = render
    cls.clear = clear


def install_window(window_cls: type[Any]) -> None:
    """Drive the indicator from the actual turn lifecycle, not a timer guess."""
    if getattr(window_cls, "_loom_agent_working_installed", False):
        return
    window_cls._loom_agent_working_installed = True

    original_send_prompt = window_cls.send_prompt
    original_notification = window_cls._on_notification
    original_rpc_error = window_cls._on_rpc_error
    original_interrupt = window_cls.interrupt_turn

    def send_prompt(self: Any, text: str = "") -> Any:
        candidate = fmt.text(text).strip() or self.composer_panel.text()
        can_submit = bool(candidate) and not self.state.archived and bool(
            self.state.thread_id or getattr(self, "_draft_workspace", None) is not None
        )
        result = original_send_prompt(self, text)
        if can_submit and hasattr(self.transcript, "set_agent_working"):
            self.transcript.set_agent_working(True)
        return result

    def on_notification(self: Any, method: str, params: Any) -> Any:
        hide = False
        if isinstance(params, dict):
            if method == "item/started":
                item = params.get("item")
                hide = isinstance(item, dict) and fmt.text(item.get("type")) == "assistant_message"
            elif method == "item/delta":
                delta = params.get("delta")
                hide = isinstance(delta, dict) and bool(fmt.text(delta.get("text")))
            elif method in {"approval/requested", "turn/completed", "thread/deleted"}:
                hide = True
        if hide and hasattr(self.transcript, "set_agent_working"):
            self.transcript.set_agent_working(False)
        return original_notification(self, method, params)

    def on_rpc_error(self: Any, tag: str, message: str) -> Any:
        if tag.startswith(("turn:", "new:")) and hasattr(self.transcript, "set_agent_working"):
            self.transcript.set_agent_working(False)
        return original_rpc_error(self, tag, message)

    def interrupt_turn(self: Any, *args: Any, **kwargs: Any) -> Any:
        if hasattr(self.transcript, "set_agent_working"):
            self.transcript.set_agent_working(False)
        return original_interrupt(self, *args, **kwargs)

    window_cls.send_prompt = send_prompt
    window_cls._on_notification = on_notification
    window_cls._on_rpc_error = on_rpc_error
    window_cls.interrupt_turn = interrupt_turn


__all__ = [
    "AgentWorkingGlyph",
    "AgentWorkingIndicator",
    "install_widgets",
    "install_window",
]
