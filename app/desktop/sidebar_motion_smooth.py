"""Continuous splitter-native motion for Loom's left and right side panels.

A side panel lives inside ``QSplitter``.  Animating its minimum/maximum width on
every frame makes two layout systems fight over the same geometry: the child
changes its constraints, then the splitter resolves those constraints and
moves the other sections.  On Windows that reads as tiny stalls, uneven speed
and, on a fast reversal, an occasional one-frame gap.

This pass makes the splitter the single owner of motion.  During a transition
we relax only the moving panel's minimum width once, keep the panel painted,
and transfer pixels directly between that panel and the conversation section
with ``QSplitter.setSizes``.  The opposite side never moves.  No opacity effect
is involved, so there is no frame where space exists but content has vanished.

The implementation also starts every reversal from the *actual* current
splitter sizes and scales its duration to the remaining distance.  Repeated
clicks therefore reverse naturally instead of restarting from an old target.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QEasingCurve, QVariantAnimation
from PySide6.QtWidgets import QSplitter, QWidget

from app.desktop import sidebar_motion, theme


_OPEN_DURATION_MS = 248
_CLOSE_DURATION_MS = 218
_MIN_REVERSAL_MS = 105


def _restore_constraints(controller: Any, key: str, panel: QWidget) -> None:
    minimum, maximum = controller._panel_constraints[key]
    panel.setMinimumWidth(minimum)
    panel.setMaximumWidth(maximum)
    panel.updateGeometry()


def _center_index(splitter: QSplitter, panel_index: int) -> int:
    """Return the conversation section that absorbs side-panel width changes."""
    count = splitter.count()
    if count <= 1:
        return -1
    # Loom's splitter is [sidebar, conversation, runtime].  Keeping this helper
    # structural rather than hard-coding 1 also makes the transition safe if one
    # side is removed in a reduced layout later.
    if panel_index == 0:
        return 1
    if panel_index == count - 1:
        return count - 2
    # A side-panel controller should never target a middle section, but falling
    # back to the adjacent section is still deterministic and avoids a crash.
    return max(0, panel_index - 1)


def _apply_splitter_width(
    splitter: QSplitter,
    panel_index: int,
    desired_width: int | float,
) -> int:
    """Move exactly the requested side width and give/take pixels from center.

    ``QSplitter`` may be resized by the window while an animation is running.
    Reading its current sizes on every frame means that external resize is
    preserved; only the delta needed for this animation is transferred.
    """
    sizes = list(splitter.sizes())
    if panel_index < 0 or panel_index >= len(sizes):
        return 0
    center = _center_index(splitter, panel_index)
    if center < 0 or center >= len(sizes):
        return sizes[panel_index]

    desired = max(0, int(round(float(desired_width))))
    current = max(0, int(sizes[panel_index]))
    if desired == current:
        return current

    # Positive delta means the side opens and the conversation gives up pixels;
    # negative delta means the side closes and the conversation receives them.
    delta = desired - current
    sizes[panel_index] = desired
    sizes[center] = max(0, int(sizes[center]) - delta)
    splitter.setSizes(sizes)
    return max(0, int(splitter.sizes()[panel_index]))


def _transition_duration(*, opening: bool, start: int, end: int, full_width: int) -> int:
    """Keep full transitions luxurious while short reversals stay responsive."""
    base = _OPEN_DURATION_MS if opening else _CLOSE_DURATION_MS
    span = max(1, int(full_width))
    fraction = min(1.0, abs(int(end) - int(start)) / span)
    # sqrt-like falloff keeps a half-finished reversal from suddenly becoming
    # too fast, while a tiny reversal never spends another full quarter second.
    scaled = int(round(base * max(0.48, fraction ** 0.58)))
    return max(_MIN_REVERSAL_MS, min(base, scaled))


def _set_panel_visible(
    self: Any,
    key: str,
    panel: QWidget,
    visible: bool,
) -> None:
    """Reveal/collapse a side panel as one continuous splitter-edge movement."""
    original_min, original_max = self._panel_constraints[key]
    splitter = getattr(self.window, "main_splitter", None)
    if not isinstance(splitter, QSplitter):
        _restore_constraints(self, key, panel)
        panel.setVisible(bool(visible))
        return

    index = splitter.indexOf(panel)
    center = _center_index(splitter, index)
    if index < 0 or center < 0:
        _restore_constraints(self, key, panel)
        panel.setVisible(bool(visible))
        return

    running = self._panel_animations.pop(key, None)
    reversing = running is not None
    if running is not None:
        # stop() leaves QSplitter exactly where the previous frame put it.  The
        # new animation samples that real geometry below, so a reversal has no
        # discontinuity and no stale target-width jump.
        running.stop()
        try:
            running.deleteLater()
        except RuntimeError:
            pass

    # The old animation used opacity; never let a stale effect survive a hot
    # reload or a transition reversal.  Panels remain fully painted throughout.
    panel.setGraphicsEffect(None)

    if not theme.motion_enabled():
        if visible:
            panel.show()
        else:
            panel.hide()
        _restore_constraints(self, key, panel)
        splitter.setCollapsible(index, False)
        return

    was_visible = panel.isVisible()
    sizes_before = list(splitter.sizes())
    current_width = max(0, int(sizes_before[index])) if index < len(sizes_before) else 0

    # Remember a user's manually resized settled width, but never overwrite it
    # with an intermediate width from a reversed animation.
    if not visible and was_visible and current_width >= original_min and not reversing:
        self._panel_widths[key] = min(current_width, original_max)

    target_width = min(
        max(original_min, int(self._panel_widths[key])),
        original_max,
    )

    if visible and not was_visible:
        # Relax constraints before show(), then immediately give any width Qt
        # assigned on show back to the conversation.  All of this happens in the
        # same event-loop turn, so the first painted frame is genuinely width 0.
        panel.setMinimumWidth(0)
        panel.setMaximumWidth(original_max)
        splitter.setCollapsible(index, True)
        panel.show()
        _apply_splitter_width(splitter, index, 0)
        current_width = max(0, int(splitter.sizes()[index]))
    elif not visible and not was_visible:
        _restore_constraints(self, key, panel)
        splitter.setCollapsible(index, False)
        return
    else:
        # Do this once per transition, not every frame.  It is the critical
        # difference from the previous implementation: QSplitter now owns every
        # intermediate geometry instead of reacting to changing child limits.
        panel.setMinimumWidth(0)
        panel.setMaximumWidth(original_max)
        splitter.setCollapsible(index, True)
        current_width = max(0, int(splitter.sizes()[index]))

    end_width = target_width if visible else 0
    if current_width == end_width:
        if not visible:
            panel.hide()
        else:
            self._panel_widths[key] = current_width
        _restore_constraints(self, key, panel)
        splitter.setCollapsible(index, False)
        return

    animation = QVariantAnimation(self)
    animation.setStartValue(current_width)
    animation.setEndValue(end_width)
    animation.setDuration(
        _transition_duration(
            opening=bool(visible),
            start=current_width,
            end=end_width,
            full_width=target_width,
        )
    )
    # No bounce/overshoot: panel chrome should feel like a native desktop edge,
    # not a drawer.  OutQuart gives opening a soft settle; closing is slightly
    # tighter while still decelerating into the edge.
    animation.setEasingCurve(
        QEasingCurve.Type.OutQuart if visible else QEasingCurve.Type.OutCubic
    )

    last_applied = current_width

    def apply_width(value: Any) -> None:
        nonlocal last_applied
        desired = max(0, int(round(float(value))))
        if desired == last_applied:
            return
        last_applied = _apply_splitter_width(splitter, index, desired)

    def finish() -> None:
        # Commit the exact endpoint before restoring normal drag constraints.
        actual = _apply_splitter_width(splitter, index, end_width)
        if not visible:
            panel.hide()
        else:
            self._panel_widths[key] = max(original_min, min(actual, original_max))
        _restore_constraints(self, key, panel)
        splitter.setCollapsible(index, False)
        if self._panel_animations.get(key) is animation:
            self._panel_animations.pop(key, None)
        animation.deleteLater()

    animation.valueChanged.connect(apply_width)
    animation.finished.connect(finish)
    self._panel_animations[key] = animation
    animation.start()


def install() -> None:
    """Replace only side-panel transitions; Runtime/tab motion stays unchanged."""
    sidebar_motion.SidebarMotionController.set_panel_visible = _set_panel_visible


__all__ = [
    "_apply_splitter_width",
    "_transition_duration",
    "install",
]
