"""Persistent conversation-level progress feedback for a live Loom turn."""

from __future__ import annotations

import time
from typing import Any

from PySide6.QtCore import QEasingCurve, QPropertyAnimation, Qt
from PySide6.QtWidgets import (
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from app.desktop import format as fmt
from app.desktop import theme
from app.desktop.widgets import CenteredColumn, TranscriptView, repolish


_INSTALLED = False

_PROGRESS_QSS = """
QFrame#turnProgress { background:transparent; border:none; }
QLabel#turnProgressDot {
    min-width:6px; max-width:6px; min-height:6px; max-height:6px;
    border-radius:3px; background:#6f7786;
}
QLabel#turnProgressText {
    background:transparent; color:#8d94a2; font-size:11px; font-weight:600;
}
QFrame#turnProgressRule {
    min-height:1px; max-height:1px; background:#20252e; border:none;
}
QFrame#turnProgress[tone="working"] QLabel#turnProgressDot { background:#8f97ea; }
QFrame#turnProgress[tone="working"] QLabel#turnProgressText { color:#a7add8; }
QFrame#turnProgress[tone="waiting"] QLabel#turnProgressDot { background:#c89c55; }
QFrame#turnProgress[tone="waiting"] QLabel#turnProgressText { color:#bd9d69; }
QFrame#turnProgress[tone="done"] QLabel#turnProgressDot { background:#69b694; }
QFrame#turnProgress[tone="done"] QLabel#turnProgressText { color:#82bea5; }
QFrame#turnProgress[tone="failed"] QLabel#turnProgressDot { background:#c87984; }
QFrame#turnProgress[tone="failed"] QLabel#turnProgressText { color:#d1979f; }
"""


class TurnProgressLine(QFrame):
    """A compact status line shown above the composer while a turn is alive."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("turnProgress")
        self.setProperty("tone", "")
        self.setStyleSheet(_PROGRESS_QSS)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(4, 0, 4, 0)
        outer.setSpacing(7)

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)

        self.dot = QLabel(self)
        self.dot.setObjectName("turnProgressDot")
        self.dot.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        row.addWidget(self.dot, 0, Qt.AlignmentFlag.AlignVCenter)

        self.label = QLabel("", self)
        self.label.setObjectName("turnProgressText")
        self.label.setTextFormat(Qt.TextFormat.PlainText)
        row.addWidget(self.label, 0, Qt.AlignmentFlag.AlignVCenter)
        row.addStretch(1)
        outer.addLayout(row)

        self.rule = QFrame(self)
        self.rule.setObjectName("turnProgressRule")
        outer.addWidget(self.rule)

        self._pulse: QPropertyAnimation | None = None
        self.hide()

    def text(self) -> str:
        return self.label.text()

    def _host(self) -> QWidget | None:
        parent = self.parentWidget()
        return parent if isinstance(parent, CenteredColumn) else None

    def _set_visible(self, visible: bool) -> None:
        self.setVisible(visible)
        host = self._host()
        if host is not None:
            host.setVisible(visible)

    def _stop_pulse(self) -> None:
        pulse, self._pulse = self._pulse, None
        if pulse is not None:
            try:
                pulse.stop()
            except RuntimeError:
                pass
            pulse.deleteLater()
        self.dot.setGraphicsEffect(None)

    def _start_pulse(self) -> None:
        if not theme.motion_enabled() or self._pulse is not None:
            return
        effect = QGraphicsOpacityEffect(self.dot)
        self.dot.setGraphicsEffect(effect)
        pulse = QPropertyAnimation(effect, b"opacity", self.dot)
        pulse.setDuration(920)
        pulse.setStartValue(0.38)
        pulse.setKeyValueAt(0.5, 1.0)
        pulse.setEndValue(0.38)
        pulse.setLoopCount(-1)
        pulse.setEasingCurve(QEasingCurve.Type.InOutSine)
        self._pulse = pulse
        pulse.start()

    def set_state(self, text: str, *, tone: str = "", active: bool = False) -> None:
        text = fmt.text(text).strip()
        if not text:
            self._stop_pulse()
            self.label.clear()
            self._set_visible(False)
            return

        self.label.setText(text)
        tone = tone if tone in {"working", "waiting", "done", "failed"} else ""
        if self.property("tone") != tone:
            self.setProperty("tone", tone)
            repolish(self)
            repolish(self.dot)
            repolish(self.label)
        self._set_visible(True)
        if active:
            self._start_pulse()
        else:
            self._stop_pulse()


def _elapsed(window: Any) -> int:
    started = getattr(window, "_turn_started_at", None)
    if started is None:
        return 0
    return max(0, int(time.monotonic() - float(started)))


def _phase_text(window: Any) -> str:
    phase = str(getattr(window, "_turn_phase", "") or "working")
    label = {
        "starting": "Processing",
        "thinking": "Thinking",
        "working": "Working",
        "responding": "Responding",
    }.get(phase, "Working")
    elapsed = _elapsed(window)
    return f"{label} · {fmt.elapsed_label(elapsed)}" if elapsed >= 1 else f"{label}…"


def _sync_progress(window: Any, status: str | None = None) -> None:
    progress = getattr(window, "turn_progress", None)
    if not isinstance(progress, TurnProgressLine):
        return

    status = fmt.text(status or window.status_label.property("state") or window.state.status) or "idle"
    if getattr(window.state, "archived", False):
        progress.set_state("")
        return
    if status == "waiting_approval":
        progress.set_state("Waiting for approval", tone="waiting")
        return
    if bool(getattr(window, "_stopping", False)) and status in {"running", "starting"}:
        elapsed = _elapsed(window)
        text = f"Stopping · {fmt.elapsed_label(elapsed)}" if elapsed >= 1 else "Stopping…"
        progress.set_state(text, tone="waiting", active=True)
        return
    if status in fmt.ACTIVE_STATUSES:
        progress.set_state(_phase_text(window), tone="working", active=True)
        return
    if status == "failed":
        elapsed = int(getattr(window, "_last_turn_elapsed", 0) or 0)
        text = f"Failed · {fmt.elapsed_label(elapsed)}" if elapsed >= 1 else "Failed"
        progress.set_state(text, tone="failed")
        return
    if status == "cancelled":
        elapsed = int(getattr(window, "_last_turn_elapsed", 0) or 0)
        text = f"Stopped · {fmt.elapsed_label(elapsed)}" if elapsed >= 1 else "Stopped"
        progress.set_state(text)
        return

    summary = fmt.text(getattr(window, "_turn_progress_summary", ""))
    progress.set_state(summary, tone="done" if summary else "")


def _item_phase(params: Any) -> str | None:
    if not isinstance(params, dict):
        return None
    item = params.get("item")
    if not isinstance(item, dict):
        return None
    kind = fmt.text(item.get("type"))
    if kind == "assistant_message":
        return "thinking"
    if kind in {"tool_call", "process", "file_edit"}:
        return "working"
    return None


def install_window(window_cls: type[Any]) -> None:
    """Attach the progress line without changing Runtime or App Server behavior."""
    global _INSTALLED
    if _INSTALLED:
        return

    original_build_ui = window_cls._build_ui
    original_send_prompt = window_cls.send_prompt
    original_set_status = window_cls._set_status
    original_tick_turn_clock = window_cls._tick_turn_clock
    original_finish_turn_clock = window_cls._finish_turn_clock
    original_clear_turn_summary = window_cls._clear_turn_summary
    original_on_notification = window_cls._on_notification
    original_load_thread = window_cls.load_thread
    original_server_exit = window_cls._on_server_exit

    def _build_ui(self: Any) -> None:
        original_build_ui(self)
        self._turn_phase = ""
        self._last_turn_elapsed = 0
        self._turn_progress_summary = ""

        composer_host = self.composer_frame.parentWidget()
        conversation = composer_host.parentWidget() if composer_host is not None else None
        layout = conversation.layout() if conversation is not None else None
        if composer_host is None or layout is None:
            return

        self.turn_progress = TurnProgressLine()
        self.turn_progress_column = CenteredColumn(
            self.turn_progress, TranscriptView.MAX_CONTENT_WIDTH + 18
        )
        index = layout.indexOf(composer_host)
        layout.insertWidget(index if index >= 0 else layout.count(), self.turn_progress_column)
        self.turn_progress_column.hide()

    def send_prompt(self: Any, text: str = "") -> None:
        self._turn_phase = "starting"
        self._last_turn_elapsed = 0
        self._turn_progress_summary = ""
        original_send_prompt(self, text)
        # Draft thread creation happens before the base window has a thread id,
        # so begin feedback/elapsed time at the Send click rather than waiting
        # for the first server notification.
        if fmt.text(self.status_label.property("state")) == "starting":
            if self._turn_started_at is None:
                self._turn_started_at = time.monotonic()
            if not self._turn_clock.isActive():
                self._turn_clock.start()
            _sync_progress(self, "starting")

    def _set_status(self: Any, status: str) -> None:
        original_set_status(self, status)
        self.composer_state_label.hide()
        _sync_progress(self, status)

    def _tick_turn_clock(self: Any) -> None:
        original_tick_turn_clock(self)
        self.composer_state_label.hide()
        _sync_progress(self)

    def _finish_turn_clock(self: Any, status: str) -> None:
        elapsed = _elapsed(self)
        self._last_turn_elapsed = elapsed
        original_finish_turn_clock(self, status)
        # Keep the base window's "Done · Ns" summary untouched for compatibility;
        # the new visible product surface can use the clearer "Completed" wording.
        if fmt.text(status) == "completed":
            self._turn_progress_summary = (
                f"Completed · {fmt.elapsed_label(elapsed)}" if elapsed >= 1 else "Completed"
            )
        else:
            self._turn_progress_summary = ""
        self._turn_phase = ""

    def _clear_turn_summary(self: Any) -> None:
        original_clear_turn_summary(self)
        self._turn_progress_summary = ""
        self.composer_state_label.hide()
        _sync_progress(self)

    def _on_notification(self: Any, method: str, params: Any) -> None:
        if method == "turn/started":
            self._turn_phase = "thinking"
        elif method == "item/started":
            phase = _item_phase(params)
            if phase:
                self._turn_phase = phase
        elif method == "item/delta" and isinstance(params, dict):
            delta = params.get("delta")
            if isinstance(delta, dict) and fmt.text(delta.get("text")):
                self._turn_phase = "responding"
        elif method == "approval/requested":
            self._turn_phase = "waiting"
        elif method == "turn/completed":
            self._turn_phase = ""

        original_on_notification(self, method, params)
        self.composer_state_label.hide()
        _sync_progress(self)

    def load_thread(self: Any, thread_id: str) -> None:
        if fmt.text(thread_id) != fmt.text(self.state.thread_id):
            self._turn_phase = ""
            self._last_turn_elapsed = 0
            self._turn_progress_summary = ""
        original_load_thread(self, thread_id)

    def _on_server_exit(self: Any, message: str) -> None:
        was_active = fmt.text(self.status_label.property("state")) in fmt.ACTIVE_STATUSES
        original_server_exit(self, message)
        if was_active and hasattr(self, "turn_progress"):
            self.turn_progress.set_state("Connection stopped", tone="failed")

    window_cls._build_ui = _build_ui
    window_cls.send_prompt = send_prompt
    window_cls._set_status = _set_status
    window_cls._tick_turn_clock = _tick_turn_clock
    window_cls._finish_turn_clock = _finish_turn_clock
    window_cls._clear_turn_summary = _clear_turn_summary
    window_cls._on_notification = _on_notification
    window_cls.load_thread = load_thread
    window_cls._on_server_exit = _on_server_exit
    _INSTALLED = True


__all__ = ["TurnProgressLine", "install_window"]
