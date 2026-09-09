"""Keep the central agent transcript physically ordered like the Runtime timeline.

``ThreadState`` already exposes a chronological list, but the native transcript
reconciler historically only inserted *new* widgets. When a later snapshot
corrected the order of existing live items, ``_order`` changed while the
QVBoxLayout did not. The data said ``assistant -> command -> assistant`` while
Qt still painted ``assistant -> assistant -> command``.

This final presentation guard runs after the other transcript wrappers. It
reorders existing widgets in-place, preserves the user-message alignment, keeps
the working indicator at the tail, and also normalises legacy turn-error rows so
an error can never be shown as the contradictory ``× Completed``.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QTimer, Qt

from app.desktop import format as fmt
from app.desktop import output_presentation as presentation
from app.desktop import widgets as base


_INSTALLED = False


def _entry_alignment(kind: str) -> Qt.AlignmentFlag:
    return Qt.AlignmentFlag.AlignRight if kind == "user" else Qt.AlignmentFlag.AlignTop


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
    """Install physical-order and legacy-error guards once."""
    global _INSTALLED
    if _INSTALLED:
        return
    _INSTALLED = True

    original_render = presentation.TranscriptView.render

    def render(self: Any, entries: list[Any]) -> None:
        original_render(self, entries)
        # Runtime inspector uses the same class with max_content_width=0 and is
        # already a literal event list. Only the central conversation needs this
        # editorial ordering guard.
        if getattr(self, "_max_content_width", 0):
            _reorder_layout(self, entries)

    presentation.TranscriptView.render = render

    original_describe = base.describe_card

    def describe_card(entry: Any) -> dict[str, Any]:
        if getattr(entry, "kind", "") == "error":
            return _normalise_error_card(entry)
        return original_describe(entry)

    base.describe_card = describe_card


__all__ = ["_normalise_error_card", "_reorder_layout", "install"]
