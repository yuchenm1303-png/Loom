"""High-signal runtime feedback for live desktop turns.

The transcript intentionally stays compact once work is finished, but live work
must never look frozen. This presentation layer makes the current action obvious
without turning the conversation into a dashboard:

- active command rows get a restrained highlighted surface and a visible timer;
- live process output opens automatically while it is streaming;
- completed historical rows remain compact by default;
- duplicate transport-level exec/file tool rows disappear once their richer
  process/diff row exists;
- the persistent turn indicator above the composer becomes a real status surface
  and names the current action when Runtime provides enough information.
"""

from __future__ import annotations

import re
import time
from typing import Any

from PySide6.QtCore import QRectF, QTimer, Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QSizePolicy, QWidget

from app.desktop import activity_hierarchy as hierarchy
from app.desktop import format as fmt
from app.desktop import output_presentation as presentation
from app.desktop import theme
from app.desktop import turn_progress as progress
from app.desktop import widgets as base
from app.desktop.state import TranscriptEntry


_INSTALLED = False
_ACTIVE = {"running", "started"}
_WAITING = {"waiting", "waiting_approval"}
_FAILED = {"failed", "denied", "cancelled"}

_CARD_LIVE_QSS = """
QFrame#activityCard[runtimeState="running"],
QFrame#activityCard[runtimeState="running"]:hover {
    background:#121722;
    border:1px solid #293349;
    border-radius:7px;
}
QFrame#activityCard[runtimeState="waiting"],
QFrame#activityCard[runtimeState="waiting"]:hover {
    background:#18160f;
    border:1px solid #403720;
    border-radius:7px;
}
QFrame#activityCard[runtimeState="failed"],
QFrame#activityCard[runtimeState="failed"]:hover {
    background:#1a1216;
    border:1px solid #452833;
    border-radius:7px;
}
QFrame#activityLiveBadge {
    background:#191e2a;
    border:1px solid #30394f;
    border-radius:8px;
}
QFrame#activityLiveBadge[tone="waiting"] {
    background:#211c12;
    border-color:#4a3c22;
}
QFrame#activityLiveBadge[tone="failed"] {
    background:#24161a;
    border-color:#52303a;
}
QLabel#activityLiveText {
    background:transparent;
    border:none;
    color:#b9c2ef;
    font-size:10px;
    font-weight:700;
    padding:0;
}
QFrame#activityLiveBadge[tone="waiting"] QLabel#activityLiveText { color:#ddbf87; }
QFrame#activityLiveBadge[tone="failed"] QLabel#activityLiveText { color:#dda0aa; }
"""

_TURN_PROGRESS_QSS = """
QFrame#turnProgress {
    background:#11151d;
    border:1px solid #293142;
    border-radius:10px;
}
QLabel#turnProgressDot {
    min-width:8px; max-width:8px; min-height:8px; max-height:8px;
    border-radius:4px; background:#70798a;
}
QLabel#turnProgressText {
    background:transparent;
    color:#aeb5c4;
    font-size:12px;
    font-weight:650;
    padding:0 1px;
}
QFrame#turnProgress[tone="working"] { border-color:#303a56; background:#121722; }
QFrame#turnProgress[tone="working"] QLabel#turnProgressDot { background:#939cff; }
QFrame#turnProgress[tone="waiting"] { border-color:#493d24; background:#19160f; }
QFrame#turnProgress[tone="waiting"] QLabel#turnProgressDot { background:#d1a75e; }
QFrame#turnProgress[tone="done"] { border-color:#294237; background:#101813; }
QFrame#turnProgress[tone="done"] QLabel#turnProgressDot { background:#70c39d; }
QFrame#turnProgress[tone="failed"] { border-color:#4a2d36; background:#1a1216; }
QFrame#turnProgress[tone="failed"] QLabel#turnProgressDot { background:#cf7e8b; }
QFrame#turnProgressRule { min-height:0; max-height:0; background:transparent; border:none; }
"""


class RuntimeSpinner(QWidget):
    """Small native spinner used only while an activity row is genuinely live."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._phase = 0
        self._active = False
        self._tone = "working"
        self.setFixedSize(12, 12)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._timer = QTimer(self)
        self._timer.setInterval(65)
        self._timer.timeout.connect(self._advance)

    def set_state(self, active: bool, *, tone: str = "working") -> None:
        self._active = bool(active)
        self._tone = tone
        if self._active and theme.motion_enabled():
            if not self._timer.isActive():
                self._timer.start()
        else:
            self._timer.stop()
        self.update()

    def _advance(self) -> None:
        self._phase = (self._phase + 1) % 24
        self.update()

    def paintEvent(self, _event: Any) -> None:  # noqa: N802 - Qt override
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(2.0, 2.0, 8.0, 8.0)
        base_color = {
            "working": QColor("#47506a"),
            "waiting": QColor("#655435"),
            "failed": QColor("#65404a"),
        }.get(self._tone, QColor("#47506a"))
        live_color = {
            "working": QColor("#aab2ff"),
            "waiting": QColor("#ddb971"),
            "failed": QColor("#d9919d"),
        }.get(self._tone, QColor("#aab2ff"))

        painter.setPen(QPen(base_color, 1.35))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(rect)
        painter.setPen(QPen(live_color, 1.65, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        if self._active and theme.motion_enabled():
            start = int((-90 - self._phase * 15) * 16)
            painter.drawArc(rect, start, int(105 * 16))
        else:
            painter.drawArc(rect, int(-90 * 16), int(105 * 16))


class ActivityLiveBadge(QFrame):
    """Persistent, readable state chip for the currently active transcript row."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("activityLiveBadge")
        self.setProperty("tone", "working")
        self.setStyleSheet(_CARD_LIVE_QSS)
        self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(7, 2, 7, 2)
        layout.setSpacing(5)
        self.spinner = RuntimeSpinner(self)
        layout.addWidget(self.spinner, 0, Qt.AlignmentFlag.AlignVCenter)
        self.label = QLabel("", self)
        self.label.setObjectName("activityLiveText")
        self.label.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.label, 0, Qt.AlignmentFlag.AlignVCenter)

        self._status = ""
        self._started_at: float | None = None
        self._clock = QTimer(self)
        self._clock.setInterval(500)
        self._clock.timeout.connect(self._sync_text)
        self.hide()

    def _set_tone(self, tone: str) -> None:
        if self.property("tone") == tone:
            return
        self.setProperty("tone", tone)
        base.repolish(self)

    def _sync_text(self) -> None:
        status = self._status
        if status in _ACTIVE:
            elapsed = max(0, int(time.monotonic() - (self._started_at or time.monotonic())))
            self.label.setText(f"Running · {fmt.elapsed_label(elapsed)}")
        elif status == "waiting_approval":
            self.label.setText("Approval required")
        elif status == "waiting":
            self.label.setText("Waiting")
        elif status == "failed":
            self.label.setText("Failed")
        elif status == "denied":
            self.label.setText("Denied")
        elif status == "cancelled":
            self.label.setText("Stopped")

    def set_status(self, status: str) -> None:
        status = fmt.text(status).strip()
        was_active = self._status in _ACTIVE
        self._status = status

        if status in _ACTIVE:
            if not was_active or self._started_at is None:
                self._started_at = time.monotonic()
            self._set_tone("working")
            self.spinner.set_state(True, tone="working")
            if not self._clock.isActive():
                self._clock.start()
            self._sync_text()
            self.show()
            return

        self._clock.stop()
        self._started_at = None
        if status in _WAITING:
            self._set_tone("waiting")
            self.spinner.set_state(False, tone="waiting")
            self._sync_text()
            self.show()
            return
        if status in _FAILED:
            self._set_tone("failed")
            self.spinner.set_state(False, tone="failed")
            self._sync_text()
            self.show()
            return

        self.spinner.set_state(False)
        self.hide()


def _header_layout(card: Any) -> Any | None:
    outer = card.layout()
    if outer is None:
        return None
    for index in range(outer.count()):
        layout = outer.itemAt(index).layout()
        if layout is not None and layout.indexOf(card.toggle_button) >= 0:
            return layout
    return None


def _runtime_state(status: str) -> str:
    if status in _ACTIVE:
        return "running"
    if status in _WAITING:
        return "waiting"
    if status in _FAILED:
        return "failed"
    return ""


def _compact_command(value: Any) -> str:
    command = fmt.command_line(value).strip()
    if not command:
        return ""
    command = re.sub(r"\s+", " ", command)
    presented = hierarchy._compact_process_title(f"Running {command}")
    if presented.casefold().startswith("running "):
        command = presented[8:].strip()
    if len(command) > 76:
        command = command[:73].rstrip() + "…"
    return command


def describe_action(params: Any) -> str:
    """Best-effort human label for the current Runtime item."""
    if not isinstance(params, dict):
        return ""
    item = params.get("item")
    if not isinstance(item, dict):
        return ""

    kind = fmt.text(item.get("type")).strip()
    if kind == "process":
        command = _compact_command(item.get("argv") or item.get("command"))
        return f"Running {command}" if command else "Running command"
    if kind == "file_edit":
        paths = item.get("paths") or []
        if isinstance(paths, (list, tuple)) and len(paths) == 1:
            name = re.split(r"[\\/]", fmt.text(paths[0]))[-1]
            return f"Updating {name}" if name else "Updating file"
        return "Updating files"
    if kind == "tool_call":
        name = fmt.text(item.get("toolName") or item.get("name")).strip()
        if name:
            friendly = name.replace("_", " ").strip()
            return f"Using {friendly}"
        return "Using tool"
    return ""


def _coalesce_activity(entries: list[TranscriptEntry]) -> list[TranscriptEntry]:
    """Prefer the richer live process/diff row over duplicate transport plumbing."""
    visible: list[TranscriptEntry] = []
    exceptional = {"waiting", "waiting_approval", "failed", "denied", "cancelled"}
    for index, entry in enumerate(entries):
        if entry.kind != "tool":
            visible.append(entry)
            continue

        name = hierarchy._tool_name(entry)
        duplicate = False
        if name in hierarchy._PROCESS_BACKED_TOOLS:
            duplicate = hierarchy._has_following_kind(entries, index, "process")
        elif name in hierarchy._FILE_BACKED_TOOLS:
            duplicate = hierarchy._has_following_kind(entries, index, "diff")

        if duplicate and entry.status not in exceptional:
            continue
        visible.append(entry)
    return visible


def install() -> None:
    """Install runtime feedback after activity/disclosure presentation hooks."""
    global _INSTALLED
    if _INSTALLED:
        return
    _INSTALLED = True

    # The hierarchy renderer resolves this module global at render time, so the
    # stronger duplicate suppression also applies to already-created views.
    hierarchy._coalesce_activity = _coalesce_activity

    original_card_init = presentation.FlatActivityCard.__init__
    original_card_update = presentation.FlatActivityCard.update_card

    def card_init(self: Any, kind: str, parent: QWidget | None = None) -> None:
        original_card_init(self, kind, parent)
        self._runtime_feedback_status = ""
        self._runtime_auto_opened = False
        self.setProperty("runtimeState", "")
        self.setStyleSheet(self.styleSheet() + "\n" + _CARD_LIVE_QSS)

        self.runtime_badge = ActivityLiveBadge(self)
        header = _header_layout(self)
        if header is not None:
            index = header.indexOf(self.toggle_button)
            header.insertWidget(max(0, index), self.runtime_badge, 0, Qt.AlignmentFlag.AlignVCenter)

    def card_update(
        self: Any,
        *,
        title: str,
        subtitle: str = "",
        status: str = "",
        body: str = "",
        auto_expand: bool | None = None,
    ) -> None:
        previous = fmt.text(getattr(self, "_runtime_feedback_status", ""))
        original_card_update(
            self,
            title=title,
            subtitle=subtitle,
            status=status,
            body=body,
            auto_expand=auto_expand,
        )
        self._runtime_feedback_status = status
        self.runtime_badge.set_status(status)

        state = _runtime_state(status)
        if self.property("runtimeState") != state:
            self.setProperty("runtimeState", state)
            base.repolish(self)

        # The previous disclosure pass deliberately kept every row collapsed.
        # That is excellent for history, but disastrous for trust while a command
        # is live. Auto-open only the live process, and remember that this widget
        # was live so its final output does not vanish the instant it succeeds.
        if (
            self.kind == "process"
            and status in _ACTIVE
            and bool(body)
            and not self._user_toggled
        ):
            first_open = not bool(getattr(self, "_runtime_auto_opened", False))
            self._runtime_auto_opened = True
            self._expanded = True
            self._sync_body(animate=bool(first_open and previous not in _ACTIVE))
        elif (
            bool(getattr(self, "_runtime_auto_opened", False))
            and status in {"completed", "failed", "cancelled"}
            and bool(body)
            and not self._user_toggled
        ):
            self._expanded = True
            self._sync_body(animate=False)
        elif status in {"failed", "denied"} and bool(body) and not self._user_toggled:
            self._expanded = True
            self._sync_body(animate=previous not in _FAILED)

    presentation.FlatActivityCard.__init__ = card_init
    presentation.FlatActivityCard.update_card = card_update

    # Make the conversation-level indicator substantial enough to be noticed at
    # the bottom of a long transcript. It remains compact and does not compete
    # with the answer itself.
    original_progress_init = progress.TurnProgressLine.__init__

    def progress_init(self: Any, parent: QWidget | None = None) -> None:
        original_progress_init(self, parent)
        self.setStyleSheet(_TURN_PROGRESS_QSS)
        self.setMinimumHeight(38)
        layout = self.layout()
        if layout is not None:
            layout.setContentsMargins(11, 7, 11, 7)
            layout.setSpacing(0)
        self.rule.hide()

    progress.TurnProgressLine.__init__ = progress_init

    original_phase_text = progress._phase_text

    def phase_text(window: Any) -> str:
        phase = fmt.text(getattr(window, "_turn_phase", "") or "working")
        action = fmt.text(getattr(window, "_turn_progress_action", "")).strip()
        if phase == "working" and action:
            label = action
        else:
            label = {
                "starting": "Starting",
                "thinking": "Thinking",
                "working": "Working",
                "responding": "Responding",
            }.get(phase, "Working")
        elapsed = progress._elapsed(window)
        return f"{label} · {fmt.elapsed_label(elapsed)}" if elapsed >= 1 else f"{label}…"

    progress._phase_text = phase_text

    # Wrap the window installer before __init__.py invokes it. The normal turn
    # progress lifecycle stays authoritative; this only keeps a readable current
    # action alongside the existing phase/status state machine.
    original_install_window = progress.install_window

    def install_window(window_cls: type[Any]) -> None:
        original_install_window(window_cls)
        base_build_ui = window_cls._build_ui
        base_notification = window_cls._on_notification

        def build_ui(self: Any) -> None:
            base_build_ui(self)
            self._turn_progress_action = ""

        def on_notification(self: Any, method: str, params: Any) -> None:
            if method in {"turn/started", "turn/completed"}:
                self._turn_progress_action = ""
            elif method == "item/started":
                self._turn_progress_action = describe_action(params)
            base_notification(self, method, params)
            progress._sync_progress(self)

        window_cls._build_ui = build_ui
        window_cls._on_notification = on_notification

    progress.install_window = install_window


__all__ = [
    "ActivityLiveBadge",
    "RuntimeSpinner",
    "describe_action",
    "install",
]
