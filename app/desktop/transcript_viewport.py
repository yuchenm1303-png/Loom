"""Viewport policy for native transcript disclosures.

Disclosure geometry belongs to ``transcript_disclosure``.  This module owns the
scrolling side of the interaction: a manual disclosure is anchored to the row the
reader clicked, never to the bottom of the transcript.  While the reveal changes
real layout height, auto-tail is parked so rows above the disclosure stay visually
fixed and only rows below it move.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtWidgets import QWidget

from app.desktop.transcript_disclosure import (
    FlowActivityCard,
    FlowMessageWidget,
    FlowTranscriptView,
)


class AnchoredTranscriptView(FlowTranscriptView):
    """Transcript whose manual disclosures preserve the reader's visual anchor."""

    def _park_for_manual_disclosure(self) -> None:
        """Stop tail-follow before disclosure geometry starts changing.

        ``TranscriptView._on_range_changed`` normally follows the bottom whenever
        the canvas grows.  A reveal also grows the canvas on every frame, so if
        tail-follow remains enabled the scrollbar moves upward at the same time
        the disclosure pushes rows downward.  The two motions fight each other
        and make the whole viewport appear to jump.

        A click on a disclosure is explicit reading intent.  Park the viewport at
        its current scroll value and leave follow-tail disabled until the reader
        naturally returns to the bottom (the existing valueChanged policy already
        re-enables it there).
        """
        self._stop_tail_animation()
        self._auto_scrolling = False
        self._follow_tail = False

    def _wire_manual_disclosure(self, widget: QWidget) -> QWidget:
        if isinstance(widget, FlowActivityCard):
            widget.toggle_button.pressed.connect(self._park_for_manual_disclosure)
            return widget

        if isinstance(widget, FlowMessageWidget):
            reasoning = getattr(widget, "reasoning", None)
            toggle = getattr(reasoning, "toggle", None)
            if toggle is not None:
                toggle.pressed.connect(self._park_for_manual_disclosure)
        return widget

    def _build(self, entry: Any) -> QWidget:
        return self._wire_manual_disclosure(super()._build(entry))


__all__ = ["AnchoredTranscriptView"]
