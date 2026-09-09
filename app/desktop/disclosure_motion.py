"""Keep transcript disclosures responsive without animating document geometry.

Animating ``maximumHeight`` inside the transcript makes Qt recompute the whole
QVBoxLayout on every animation frame.  Each canvas-height change also updates the
scroll range, which can restart the transcript's tail-follow animation.  The
result is the characteristic "whole page is being pushed" feeling on Windows.

This module installs one motion policy for collapsible transcript content:

* commit expanded/collapsed geometry once;
* animate only the tiny disclosure chevron (paint-only, fixed-size);
* never put QGraphicsOpacityEffect on a large terminal/reasoning surface during
  disclosure -- off-screen rasterisation is noticeably expensive at HiDPI;
* treat a manual disclosure click as reading intent and suspend automatic tail
  following before the layout changes.  Scrolling back to the bottom naturally
  re-enables following through TranscriptView's existing valueChanged handler.

The UI still has motion, but no animation frame mutates the transcript's layout.
"""

from __future__ import annotations

import weakref
from typing import Any

from app.desktop import widgets as base
from app.desktop import message_presentation as messages


_INSTALLED = False


def _stop_animation(owner: Any, name: str) -> None:
    animation = getattr(owner, name, None)
    if animation is None:
        return
    try:
        animation.stop()
    except RuntimeError:
        pass
    setattr(owner, name, None)


def _reasoning_sync_body(self: Any, *, animate: bool) -> None:
    """Reveal reasoning in one layout commit; only the chevron rotates."""

    show = bool(self._expanded)
    self.toggle.set_expanded(show, animate=animate)
    _stop_animation(self, "_animation")

    # A stale opacity effect from an older build must not survive a hot reload
    # or a rapid second click.
    self.body.setGraphicsEffect(None)
    self.body.setMaximumHeight(16777215)
    self.body.setVisible(show)

    # Wrapped QLabel geometry needs one deferred settle after becoming visible,
    # but unlike maximumHeight animation this is at most two layout passes total,
    # not one full transcript reflow per animation frame.
    self._settle_layout(again=show)


def _activity_sync_body(self: Any, *, animate: bool = False) -> None:
    """Commit tool/process disclosure geometry once with paint-only chevron motion."""

    has_body = bool(self._body_text)
    show = bool(has_body and self._expanded)
    self.toggle_button.setVisible(has_body)
    self.toggle_button.setText("⌃" if show else "⌄")
    self.toggle_button.setToolTip("Hide output" if show else "Show output")

    _stop_animation(self, "_body_animation")
    self.body_shell.setGraphicsEffect(None)
    self.body_shell.setVisible(show)
    if show:
        self._sync_height()


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

    # pressed fires before the widget's clicked -> _toggle path, so tail-follow
    # is frozen before visibility changes alter the scroll range.
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

    messages.ReasoningBlock._sync_body = _reasoning_sync_body
    base.ActivityCard._sync_body = _activity_sync_body

    original_render = base.TranscriptView.render

    def render(self: Any, entries: Any) -> None:
        original_render(self, entries)
        _wire_view_disclosures(self)

    base.TranscriptView.render = render
    _INSTALLED = True


__all__ = ["install"]
