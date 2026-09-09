"""Keep response action footers tied to real turn boundaries.

An assistant item can finish streaming while the turn itself is still running
because the model may immediately request tools, wait for them, then continue
with another assistant message. Treating ``item/completed`` as "the response is
finished" makes copy/feedback/time chrome appear in the middle of an active
turn. This module makes the footer a turn-level affordance instead:

* historical turns keep one footer on their last assistant message;
* the currently active turn shows no footer at all;
* once ``turn/completed`` arrives, only that turn's last assistant message gains
  the footer.

The rule is presentation-only. It does not change durable thread state or the
Runtime event model.
"""

from __future__ import annotations

from typing import Any

from app.desktop import format as fmt
from app.desktop import message_actions
from app.desktop import output_presentation


_VIEW_INSTALLED = False
_WINDOW_INSTALLED = False


def _terminal_assistant_keys(entries: list[Any], *, turn_active: bool) -> set[str]:
    """Return the one footer-owning assistant item for each completed turn.

    Transcript entries are already in durable turn order. A user message starts
    the next turn, so the assistant immediately preceding that boundary is the
    terminal reply for the previous turn. The final segment is eligible only
    after the thread reports a terminal turn state.
    """
    terminal: set[str] = set()
    segment_assistants: list[str] = []

    for entry in entries:
        kind = getattr(entry, "kind", "")
        if kind == "user":
            if segment_assistants:
                terminal.add(segment_assistants[-1])
            segment_assistants = []
            continue
        if kind == "assistant":
            key = str(getattr(entry, "key", "") or "")
            if key:
                segment_assistants.append(key)

    if segment_assistants and not turn_active:
        terminal.add(segment_assistants[-1])

    return terminal


def install_view() -> None:
    """Make the action bar obey the transcript's turn-boundary decision."""
    global _VIEW_INSTALLED
    if _VIEW_INSTALLED:
        return
    _VIEW_INSTALLED = True

    original_sync = message_actions.MessageActionBar.sync

    def sync(self: Any) -> None:
        # Standalone MessageWidget tests/previews do not set this property and
        # retain the old item-level behavior. Real TranscriptView rows always do.
        if self.property("terminalResponse") is False:
            self.hide()
            return
        original_sync(self)

    message_actions.MessageActionBar.sync = sync

    original_render = output_presentation.TranscriptView.render

    def render(self: Any, entries: list[Any]) -> None:
        original_render(self, entries)

        turn_active = bool(self.property("turnActive"))
        terminal_keys = _terminal_assistant_keys(entries, turn_active=turn_active)

        for entry in entries:
            if getattr(entry, "kind", "") != "assistant":
                continue
            widget = getattr(self, "_widgets", {}).get(getattr(entry, "key", ""))
            bar = getattr(widget, "message_actions", None)
            if bar is None:
                continue
            bar.setProperty(
                "terminalResponse",
                str(getattr(entry, "key", "") or "") in terminal_keys,
            )
            bar.sync()

    output_presentation.TranscriptView.render = render


def install_window(window_cls: type[Any]) -> None:
    """Publish the durable turn state to TranscriptView before each reconcile."""
    global _WINDOW_INSTALLED
    if _WINDOW_INSTALLED:
        return
    _WINDOW_INSTALLED = True

    original_render_transcript = window_cls._render_transcript

    def _render_transcript(self: Any) -> None:
        self.transcript.setProperty(
            "turnActive",
            fmt.text(self.state.status) in fmt.ACTIVE_STATUSES,
        )
        original_render_transcript(self)

    window_cls._render_transcript = _render_transcript


__all__ = ["_terminal_assistant_keys", "install_view", "install_window"]
