"""Keep transcript disclosures responsive without animating document geometry.

Animating ``maximumHeight`` inside the transcript makes Qt recompute the whole
QVBoxLayout on every animation frame. Each canvas-height change also updates the
scroll range, which can restart the transcript's tail-follow animation. The
result is the characteristic "whole page is being pushed" feeling on Windows.

This module is intentionally the final disclosure-motion pass. Earlier visual
polish modules may replace chevrons or styles, but this policy owns geometry:

* commit expanded/collapsed geometry once;
* animate only the tiny fixed-size disclosure chevron (paint-only);
* never put QGraphicsOpacityEffect on a large terminal/reasoning surface while
  disclosing it -- off-screen rasterisation is expensive at HiDPI;
* treat a manual disclosure click as reading intent and suspend automatic tail
  following before the layout changes. Scrolling back to the bottom naturally
  re-enables following through TranscriptView's existing valueChanged handler.

The UI still has motion, but no disclosure animation frame mutates the transcript
layout or its scroll range.
"""

from __future__ import annotations

import weakref
from typing import Any

from PySide6.QtCore import Qt

from app.desktop import message_presentation as messages
from app.desktop import output_presentation as output
from app.desktop import widgets as base


_INSTALLED = False
_UNBOUNDED_HEIGHT = 16_777_215


def _stop_animation(owner: Any, name: str) -> None:
    animation = getattr(owner, name, None)
    setattr(owner, name, None)
    if animation is None:
        return
    try:
        animation.stop()
    except RuntimeError:
        pass
    try:
        animation.deleteLater()
    except RuntimeError:
        pass


def _reasoning_sync_body(self: Any, *, animate: bool) -> None:
    """Reveal reasoning in one layout commit; only the chevron rotates."""

    show = bool(self._expanded)
    self.toggle.set_expanded(show, animate=animate)
    self.toggle.setToolTip("Hide thought process" if show else "Show thought process")
    _stop_animation(self, "_animation")

    # Clear effects left by older motion implementations before changing
    # visibility. The body can be arbitrarily long, so compositing it every frame
    # is exactly the expensive path we want to avoid.
    self.body.setGraphicsEffect(None)
    self.body.setMaximumHeight(_UNBOUNDED_HEIGHT)
    self.body.setVisible(show)

    # Wrapped QLabel geometry occasionally needs one deferred re-measure after
    # becoming visible. This is at most two layout passes total, rather than one
    # full transcript reflow for every animation frame.
    self._settle_layout(again=show)


def _activity_sync_body(self: Any, *, animate: bool = False) -> None:
    """Commit tool/process disclosure geometry once with paint-only chevron motion."""

    has_body = bool(self._body_text)
    show = bool(has_body and self._expanded)
    toggle = self.toggle_button
    toggle.setVisible(has_body)

    # Main-transcript cards use a native rotating chevron. Runtime inspector
    # cards may still have the legacy text button, so keep both implementations
    # compatible without changing layout metrics.
    set_expanded = getattr(toggle, "set_expanded", None)
    if callable(set_expanded):
        set_expanded(show, animate=animate)
    else:
        toggle.setText("⌃" if show else "⌄")
    toggle.setToolTip("Hide output" if show else "Show output")

    _stop_animation(self, "_body_animation")
    self.body_shell.setGraphicsEffect(None)
    self.body_shell.setMaximumHeight(_UNBOUNDED_HEIGHT)
    self.body_shell.setVisible(show)
    if show:
        self._sync_height()
    self.updateGeometry()


def _transcript_for(widget: Any) -> Any | None:
    parent = widget.parentWidget()
    while parent is not None:
        if hasattr(parent, "_follow_tail") and hasattr(parent, "_stop_tail_animation"):
            return parent
        parent = parent.parentWidget()
    return None


def _suspend_tail_for_disclosure(view: Any) -> None:
    """Keep the clicked disclosure anchored instead of chasing the new tail."""

    try:
        view._stop_tail_animation()
    except (AttributeError, RuntimeError):
        pass

    # QPropertyAnimation.stop() does not reliably emit finished(), so clear this
    # explicitly or _on_value_changed can keep treating later user scrolls as
    # programmatic movement.
    view._auto_scrolling = False
    view._follow_tail = False


def _wire_toggle(view: Any, toggle: Any) -> None:
    if toggle is None or bool(toggle.property("loomDisclosureTailGuard")):
        return
    ref = weakref.ref(view)

    def on_pressed() -> None:
        target = ref()
        if target is not None:
            _suspend_tail_for_disclosure(target)

    # pressed fires before clicked -> _toggle, so tail-follow is frozen before
    # visibility changes alter the scroll range.
    toggle.pressed.connect(on_pressed)
    toggle.setProperty("loomDisclosureTailGuard", True)


def _wire_view_disclosures(view: Any) -> None:
    for widget in tuple(getattr(view, "_widgets", {}).values()):
        if isinstance(widget, base.ActivityCard):
            _wire_toggle(view, getattr(widget, "toggle_button", None))

        reasoning = getattr(widget, "reasoning", None)
        if reasoning is not None:
            _wire_toggle(view, getattr(reasoning, "toggle", None))


def install() -> None:
    """Install the low-reflow disclosure policy once for all transcript views."""

    global _INSTALLED
    if _INSTALLED:
        return

    # reasoning_polish and activity_motion intentionally install richer height
    # tweens earlier in the presentation pipeline. Override those geometry
    # methods here, after visual styling/chevron classes have been finalized.
    messages.ReasoningBlock._sync_body = _reasoning_sync_body
    base.ActivityCard._sync_body = _activity_sync_body
    output.FlatActivityCard._sync_body = _activity_sync_body

    original_render = base.TranscriptView.render

    def render(self: Any, entries: Any) -> None:
        original_render(self, entries)
        _wire_view_disclosures(self)

    base.TranscriptView.render = render

    # activity_disclosure makes the whole activity header clickable, not just the
    # chevron. Freeze tail-follow on the row's left press as well so title/icon
    # clicks get the same stable viewport semantics as chevron clicks.
    original_card_press = output.FlatActivityCard.mousePressEvent

    def card_press(self: Any, event: Any) -> None:
        if event.button() == Qt.MouseButton.LeftButton and bool(getattr(self, "_body_text", "")):
            view = _transcript_for(self)
            if view is not None:
                _suspend_tail_for_disclosure(view)
        original_card_press(self, event)

    output.FlatActivityCard.mousePressEvent = card_press
    _INSTALLED = True


__all__ = ["install"]
