"""Keep the central agent transcript ordered like a real agent conversation.

The App Server timeline is deliberately lossless, but a finished turn can still
arrive in a presentation-hostile order after live/snapshot reconciliation:
``final assistant -> tools/processes/diffs``.  A normal agent transcript reads
``work -> final assistant``.  This module is the final presentation integrity
pass and fixes both the semantic entry order and the physical Qt layout order.

Rules are intentionally conservative:
- active turns keep literal live order;
- failed/error turns keep literal order;
- in a completed historical/settled turn, only the *last* assistant message can
  be moved, and only when activity rows incorrectly trail it;
- earlier assistant narration remains interleaved with the work it introduced.

It also normalises legacy turn-error rows so an error can never be shown as the
contradictory ``× Completed``.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QTimer, Qt

from app.desktop import format as fmt
from app.desktop import output_presentation as presentation
from app.desktop import widgets as base


_INSTALLED = False
_ACTIVITY_KINDS = {"tool", "process", "diff"}


def _entry_alignment(kind: str) -> Qt.AlignmentFlag:
    return Qt.AlignmentFlag.AlignRight if kind == "user" else Qt.AlignmentFlag.AlignTop


def _settle_segment(segment: list[Any], *, active: bool) -> list[Any]:
    """Put trailing work before the final assistant message of a settled turn."""
    if active or not segment:
        return segment
    if any(getattr(entry, "kind", "") == "error" for entry in segment):
        return segment

    assistant_indexes = [
        index
        for index, entry in enumerate(segment)
        if getattr(entry, "kind", "") == "assistant"
    ]
    if not assistant_indexes:
        return segment

    final_index = assistant_indexes[-1]
    final_entry = segment[final_index]
    if bool(getattr(final_entry, "streaming", False)):
        return segment

    trailing = segment[final_index + 1 :]
    if not any(getattr(entry, "kind", "") in _ACTIVITY_KINDS for entry in trailing):
        return segment

    # Preserve everything before the final assistant exactly as emitted.  Only
    # lift the terminal reply over activity rows that were incorrectly appended
    # after it by reconciliation.  This yields the familiar agent flow:
    # narration -> command -> narration -> command -> final answer.
    return segment[:final_index] + trailing + [final_entry]


def _semantic_entries(entries: list[Any], *, turn_active: bool) -> list[Any]:
    """Return presentation order without flattening intermediate narration."""
    if not entries:
        return []

    # User rows delimit turns.  A user row belongs to the segment it starts, so
    # previous completed turns can still be repaired while the newest one runs.
    segments: list[list[Any]] = []
    current: list[Any] = []
    for entry in entries:
        if getattr(entry, "kind", "") == "user" and current:
            segments.append(current)
            current = [entry]
        else:
            current.append(entry)
    if current:
        segments.append(current)

    settled: list[Any] = []
    last_index = len(segments) - 1
    for index, segment in enumerate(segments):
        settled.extend(
            _settle_segment(
                segment,
                active=bool(turn_active and index == last_index),
            )
        )
    return settled


def _reorder_layout(view: Any, entries: list[Any]) -> None:
    """Make QVBoxLayout order match the reconciler's canonical ``_order``."""
    layout = getattr(view, "_layout", None)
    widgets = getattr(view, "_widgets", None)
    order = list(getattr(view, "_order", ()) or ())
    if layout is None or not isinstance(widgets, dict) or not order:
        return

    kind_by_key = {
        str(getattr(entry, "key", "") or ""): str(getattr(entry, "kind", "") or "")
        for entry in entries
    }

    # Moving several existing widgets can otherwise expose intermediate layout
    # states for one paint cycle. Freeze only the canvas paint, not event
    # processing, and commit the corrected order as one visual update.
    canvas = getattr(view, "canvas", None)
    if canvas is not None:
        canvas.setUpdatesEnabled(False)
    try:
        for target_index, key in enumerate(order):
            widget = widgets.get(key)
            if widget is None:
                continue
            current_index = layout.indexOf(widget)
            if current_index == target_index:
                continue
            layout.removeWidget(widget)
            layout.insertWidget(
                target_index,
                widget,
                0,
                _entry_alignment(kind_by_key.get(key, "")),
            )

        # The working indicator is presentation chrome, not a transcript entry.
        # Keep it immediately before the trailing stretch after every reorder.
        indicator = getattr(view, "agent_working_indicator", None)
        if indicator is not None:
            layout.removeWidget(indicator)
            layout.insertWidget(
                max(0, layout.count() - 1),
                indicator,
                0,
                Qt.AlignmentFlag.AlignLeft,
            )
        layout.invalidate()
    finally:
        if canvas is not None:
            canvas.setUpdatesEnabled(True)
            canvas.update()

    # If the reader was following the live tail, a reorder must not strand the
    # viewport a few rows above the current action.
    if bool(getattr(view, "_follow_tail", False)):
        QTimer.singleShot(0, view.scroll_to_tail)


def _normalise_error_card(entry: Any) -> dict[str, Any]:
    item = getattr(entry, "item", {}) or {}
    detail = (
        fmt.text(item.get("error"))
        or fmt.text(item.get("message"))
        or fmt.text(item.get("reason"))
        or fmt.text(item.get("text"))
    ).strip()
    raw_status = fmt.text(getattr(entry, "status", "")).strip().casefold()

    # Older app-server snapshots marked turn-error items as "completed" because
    # the *item* finished recording. That is not the semantic outcome of the
    # turn. Infer the user-facing state from the error payload instead.
    status = raw_status
    if status in {"", "completed"}:
        lowered = detail.casefold()
        if "cancel" in lowered or "stopped" in lowered:
            status = "cancelled"
        elif "interrupt" in lowered:
            status = "interrupted"
        else:
            status = "failed"

    title = {
        "cancelled": "Stopped",
        "interrupted": "Interrupted",
        "limit_reached": "Limit reached",
        "failed": "Task failed",
    }.get(status, fmt.human_status(status) or "Task failed")
    return {
        "title": title,
        "subtitle": "",
        "status": status,
        "body": detail,
        "auto_expand": False,
    }


def install() -> None:
    """Install semantic/physical order and legacy-error guards once."""
    global _INSTALLED
    if _INSTALLED:
        return
    _INSTALLED = True

    original_render = presentation.TranscriptView.render

    def render(self: Any, entries: list[Any]) -> None:
        # Runtime inspector uses the same class with max_content_width=0 and is
        # already a literal event list. Only the central conversation gets the
        # semantic turn presentation pass.
        if getattr(self, "_max_content_width", 0):
            entries = _semantic_entries(
                entries,
                turn_active=bool(self.property("turnActive")),
            )
        original_render(self, entries)
        if getattr(self, "_max_content_width", 0):
            _reorder_layout(self, entries)

    presentation.TranscriptView.render = render

    original_describe = base.describe_card

    def describe_card(entry: Any) -> dict[str, Any]:
        if getattr(entry, "kind", "") == "error":
            return _normalise_error_card(entry)
        return original_describe(entry)

    base.describe_card = describe_card


__all__ = [
    "_normalise_error_card",
    "_reorder_layout",
    "_semantic_entries",
    "_settle_segment",
    "install",
]
