"""Viewport policy for native transcript disclosures.

Disclosure geometry belongs to ``transcript_disclosure``. This module owns the
scrolling side of the interaction: a manual disclosure is anchored to the row the
reader clicked, never to the bottom of the transcript.

Two invariants matter here:

* opening or closing a disclosure never changes the clicked header's viewport Y;
* the vertical scrollbar gutter and scroll range stay stable while geometry moves.

The second rule is what makes the first one possible near the bottom of a thread.
When a disclosure collapses, the document gets shorter. If the scrollbar maximum
shrinks with it, Qt clamps the current scroll value and physically drags the
clicked header downward. Trying to correct that on every animation frame only
creates a fight between layout and scrolling. Instead, the canvas keeps a temporary
minimum-height floor equal to its pre-collapse height. The trailing transcript
stretch absorbs the reclaimed space, rows below move upward, and the clicked row
never has to move at all.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QPoint, QTimer, Qt
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

        # A collapse close to the tail needs scrollable slack after the body has
        # disappeared, otherwise QScrollArea clamps the scrollbar and moves the
        # header down. Keep the canvas at least as tall as it was when that
        # collapse started; the existing trailing stretch owns the spare space.
        self._base_canvas_minimum_height = self.canvas.minimumHeight()
        self._scroll_floor_height = self._base_canvas_minimum_height
        self._floor_release_pending = False

    def _natural_canvas_height(self) -> int:
        """Return the layout's current natural height without the scroll floor."""
        width = max(1, self.canvas.width())
        try:
            return max(0, int(self.canvas.heightForWidth(width)))
        except (AttributeError, RuntimeError, TypeError):
            layout = self.canvas.layout()
            return max(0, int(layout.sizeHint().height())) if layout is not None else 0

    def _hold_scroll_floor(self, height: int) -> None:
        """Prevent the document's scroll range from shrinking below ``height``."""
        floor = max(
            self._base_canvas_minimum_height,
            self._scroll_floor_height,
            int(height),
        )
        if floor == self._scroll_floor_height:
            return
        self._scroll_floor_height = floor
        self.canvas.setMinimumHeight(floor)

    def _release_scroll_floor_if_redundant(self) -> None:
        """Drop an old floor once real content naturally occupies that height."""
        if self._scroll_floor_height <= self._base_canvas_minimum_height:
            return
        if self._natural_canvas_height() < self._scroll_floor_height:
            return
        self._scroll_floor_height = self._base_canvas_minimum_height
        self.canvas.setMinimumHeight(self._base_canvas_minimum_height)

    def _schedule_floor_release_if_redundant(self) -> None:
        if self._floor_release_pending:
            return
        self._floor_release_pending = True

        def settle() -> None:
            self._floor_release_pending = False
            try:
                self._release_scroll_floor_if_redundant()
            except RuntimeError:
                pass

        QTimer.singleShot(0, settle)

    def _drop_scroll_floor(self) -> None:
        """Forget retained anchor slack when the view explicitly goes to tail."""
        if self._scroll_floor_height == self._base_canvas_minimum_height:
            return
        self._scroll_floor_height = self._base_canvas_minimum_height
        self.canvas.setMinimumHeight(self._base_canvas_minimum_height)

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
        bar = self.verticalScrollBar()
        self._manual_anchor_scroll_value = bar.value()
        self._manual_anchor_y = anchor.mapTo(self.viewport(), QPoint(0, 0)).y()

        # ``pressed`` fires before the disclosure toggles. A non-zero reveal is
        # therefore about to collapse. Freeze the current canvas height now so
        # the scrollbar maximum cannot shrink as the body gets shorter. This is
        # a single geometry decision at interaction start, not a per-frame scroll
        # correction.
        if reveal.progress > 0.001 or reveal.expanded:
            self._hold_scroll_floor(self.canvas.height())

    def _finish_manual_disclosure(self, expanded: bool | None = None) -> None:
        """Verify the anchor once after motion; never chase it during motion."""
        anchor = self._manual_anchor_widget
        if anchor is not None:
            try:
                bar = self.verticalScrollBar()

                # With a collapse floor, the saved value remains legal for the
                # whole transition. Expansion only increases the range, so the
                # same is true there. Restore it once if a platform event changed
                # it, then apply one final pixel correction for rounding/layout
                # edge cases.
                saved = max(bar.minimum(), min(bar.maximum(), self._manual_anchor_scroll_value))
                if bar.value() != saved:
                    self._auto_scrolling = True
                    try:
                        bar.setValue(saved)
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
                pass

        self._manual_anchor_widget = None
        self._manual_anchor_reveal = None

        # Reopening content may naturally consume an old collapse floor. Clear it
        # only when doing so cannot change canvas geometry.
        if expanded:
            self._schedule_floor_release_if_redundant()

    def _wire_reveal(
        self,
        *,
        anchor: QWidget,
        reveal: AnimatedReveal,
    ) -> None:
        anchor.pressed.connect(
            lambda anchor=anchor, reveal=reveal: self._begin_manual_disclosure(anchor, reveal)
        )
        # Deliberately no progressChanged -> scrollbar connection here. Layout
        # owns the real reveal motion; scrolling remains inert until one final
        # verification after the transition.
        reveal.expandedChanged.connect(self._finish_manual_disclosure)

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

    def render(self, entries: list[Any]) -> None:
        super().render(entries)
        # New transcript content can make retained collapse slack unnecessary.
        # Releasing it is safe only once natural content is already at least as
        # tall as the floor, so this never changes the visible geometry.
        self._schedule_floor_release_if_redundant()

    def clear(self) -> None:
        self._drop_scroll_floor()
        self._manual_anchor_widget = None
        self._manual_anchor_reveal = None
        super().clear()

    def scroll_to_tail(self) -> None:
        # An explicit return to the tail ends the old reading anchor. Remove any
        # retained blank slack before asking the base view to jump to the actual
        # end of the transcript.
        self._drop_scroll_floor()
        super().scroll_to_tail()


__all__ = ["AnchoredTranscriptView"]
