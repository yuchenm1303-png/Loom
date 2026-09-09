"""Viewport policy for native transcript disclosures.

Disclosure geometry belongs to ``transcript_disclosure``.  This module owns the
scrolling side of the interaction: a manual disclosure is anchored to the row the
reader clicked, never to the bottom of the transcript.  While the reveal changes
real layout height, auto-tail is parked so rows above the disclosure stay visually
fixed and only rows below it move.

The viewport also reserves a permanent vertical-scrollbar gutter.  Without that,
opening a disclosure can make the scrollbar appear, shrink the viewport width,
rewrap every word-wrapped message, and make the whole screen look as if it jumped.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QPoint, Qt
from PySide6.QtWidgets import QWidget

from app.desktop.transcript_disclosure import (
    AnimatedReveal,
    FlowActivityCard,
    FlowMessageWidget,
    FlowTranscriptView,
)


class AnchoredTranscriptView(FlowTranscriptView):
    """Transcript whose manual disclosures preserve the reader's visual anchor."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        # Reserve the gutter from the first frame. Switching from no scrollbar
        # to a visible scrollbar during a reveal changes the viewport width and
        # forces every wrapped message above the clicked row to reflow.
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)

        self._manual_anchor_widget: QWidget | None = None
        self._manual_anchor_reveal: AnimatedReveal | None = None
        self._manual_anchor_y = 0
        self._manual_anchor_scroll_value = 0

    def _begin_manual_disclosure(
        self,
        anchor: QWidget,
        reveal: AnimatedReveal,
    ) -> None:
        """Park tail-follow and capture the clicked row before geometry moves."""
        self._stop_tail_animation()
        self._auto_scrolling = False
        self._follow_tail = False

        self._manual_anchor_widget = anchor
        self._manual_anchor_reveal = reveal
        self._manual_anchor_scroll_value = self.verticalScrollBar().value()
        self._manual_anchor_y = anchor.mapTo(self.viewport(), QPoint(0, 0)).y()

    def _stabilize_manual_disclosure(self, _progress: float | None = None) -> None:
        """Keep the clicked header at the same viewport Y throughout the reveal.

        In the normal case the saved scrollbar value is already sufficient. The
        Y correction is a guard for platform/layout edge cases; it only runs when
        the header actually drifted, so the common path does not add another
        competing animation.
        """
        anchor = self._manual_anchor_widget
        if anchor is None:
            return
        try:
            bar = self.verticalScrollBar()
            target_value = min(self._manual_anchor_scroll_value, bar.maximum())
            if bar.value() != target_value:
                self._auto_scrolling = True
                try:
                    bar.setValue(target_value)
                finally:
                    self._auto_scrolling = False

            current_y = anchor.mapTo(self.viewport(), QPoint(0, 0)).y()
            delta = current_y - self._manual_anchor_y
            if delta:
                corrected = max(bar.minimum(), min(bar.maximum(), bar.value() + delta))
                if corrected != bar.value():
                    self._auto_scrolling = True
                    try:
                        bar.setValue(corrected)
                    finally:
                        self._auto_scrolling = False
        except RuntimeError:
            self._clear_manual_disclosure()

    def _clear_manual_disclosure(self, _expanded: bool | None = None) -> None:
        self._stabilize_manual_disclosure()
        self._manual_anchor_widget = None
        self._manual_anchor_reveal = None

    def _wire_reveal(
        self,
        *,
        anchor: QWidget,
        reveal: AnimatedReveal,
    ) -> None:
        anchor.pressed.connect(
            lambda anchor=anchor, reveal=reveal: self._begin_manual_disclosure(anchor, reveal)
        )
        reveal.progressChanged.connect(self._stabilize_manual_disclosure)
        reveal.expandedChanged.connect(self._clear_manual_disclosure)

    def _wire_manual_disclosure(self, widget: QWidget) -> QWidget:
        if isinstance(widget, FlowActivityCard):
            self._wire_reveal(anchor=widget.toggle_button, reveal=widget.reveal)
            return widget

        if isinstance(widget, FlowMessageWidget):
            reasoning = getattr(widget, "reasoning", None)
            toggle = getattr(reasoning, "toggle", None)
            reveal = getattr(reasoning, "reveal", None)
            if isinstance(toggle, QWidget) and isinstance(reveal, AnimatedReveal):
                self._wire_reveal(anchor=toggle, reveal=reveal)
        return widget

    def _build(self, entry: Any) -> QWidget:
        return self._wire_manual_disclosure(super()._build(entry))


__all__ = ["AnchoredTranscriptView"]
