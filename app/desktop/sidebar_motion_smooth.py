"""Continuous, low-reflow motion for Loom's left and right side panels.

The first splitter-native pass removed width-constraint fighting, but it still
called ``QSplitter.setSizes`` on every animation frame while the conversation
pane's full widget tree stayed live.  A transcript can contain hundreds of rich
labels, tool cards and scroll areas, so every one of those width changes caused
Qt to recursively re-run layout/height-for-width work.  On Windows the animation
therefore looked smooth in an empty thread and visibly stuttered in a real one.

This pass keeps QSplitter as the single owner of the moving edge, but temporarily
freezes the *deep child layouts* of the moving panel and the conversation pane.
The top-level frames still resize and paint on every frame, while their children
stay at their last settled geometry and are naturally clipped/revealed by the
moving edge.  At the endpoint the layouts are re-enabled and activated exactly
once.  That turns ~15 expensive transcript reflows into one final reflow without
changing the final geometry or introducing opacity gaps.

Layout freezes are reference-counted because the left and right panels can move
at the same time and share the conversation layout.  Reversing an in-flight
animation keeps the same freeze, so there is no release/reacquire frame or jump.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QEasingCurve, QVariantAnimation
from PySide6.QtWidgets import QLayout, QSplitter, QWidget

from app.desktop import sidebar_motion, theme


# Slightly tighter than the old 248/218 ms timings.  The motion still has enough
# room to read, but spends fewer frames asking the splitter to move on Windows.
_OPEN_DURATION_MS = 210
_CLOSE_DURATION_MS = 185
_MIN_REVERSAL_MS = 90


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
    return max(0, panel_index - 1)


def _apply_splitter_width(
    splitter: QSplitter,
    panel_index: int,
    desired_width: int | float,
) -> int:
    """Move exactly the requested side width and give/take pixels from center.

    Only the two participating top-level splitter sections move.  Their deep
    layouts are suspended by ``_acquire_layout_freeze`` during an animation, so
    this operation remains cheap even when the transcript is large.
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

    delta = desired - current
    sizes[panel_index] = desired
    sizes[center] = max(0, int(sizes[center]) - delta)
    splitter.setSizes(sizes)
    # Do not query/force the entire splitter a second time unless Qt actually
    # had to clamp us.  ``sizes()`` is still cheap here because child layouts are
    # frozen, but this keeps the hot path to one geometry mutation per frame.
    actual_sizes = splitter.sizes()
    return max(0, int(actual_sizes[panel_index])) if panel_index < len(actual_sizes) else desired


def _transition_duration(*, opening: bool, start: int, end: int, full_width: int) -> int:
    """Keep full transitions polished while short reversals stay responsive."""
    base = _OPEN_DURATION_MS if opening else _CLOSE_DURATION_MS
    span = max(1, int(full_width))
    fraction = min(1.0, abs(int(end) - int(start)) / span)
    scaled = int(round(base * max(0.48, fraction ** 0.58)))
    return max(_MIN_REVERSAL_MS, min(base, scaled))


def _freeze_registry(controller: Any) -> dict[int, dict[str, Any]]:
    registry = getattr(controller, "_panel_layout_freezes", None)
    if registry is None:
        registry = {}
        controller._panel_layout_freezes = registry
    return registry


def _freeze_owners(controller: Any) -> dict[str, list[int]]:
    owners = getattr(controller, "_panel_layout_freeze_owners", None)
    if owners is None:
        owners = {}
        controller._panel_layout_freeze_owners = owners
    return owners


def _acquire_layout_freeze(
    controller: Any,
    key: str,
    *widgets: QWidget,
) -> None:
    """Suspend expensive descendant relayout until this transition settles.

    ``QLayout.setEnabled(False)`` does not hide children.  Their last geometry
    keeps painting while the parent frame changes width, which is exactly the
    visual we want: content is clipped/revealed continuously, rather than being
    rewrapped on every animation frame.
    """
    owners = _freeze_owners(controller)
    if key in owners:
        # An in-flight reversal for the same side keeps the existing freeze.
        return

    registry = _freeze_registry(controller)
    acquired: list[int] = []
    seen: set[int] = set()
    for widget in widgets:
        if widget is None:
            continue
        layout = widget.layout()
        if not isinstance(layout, QLayout):
            continue
        token = id(layout)
        if token in seen:
            continue
        seen.add(token)
        record = registry.get(token)
        if record is None:
            record = {
                "layout": layout,
                "count": 0,
                "was_enabled": bool(layout.isEnabled()),
            }
            registry[token] = record
        if int(record["count"]) == 0 and bool(record["was_enabled"]):
            layout.setEnabled(False)
        record["count"] = int(record["count"]) + 1
        acquired.append(token)
    owners[key] = acquired


def _release_layout_freeze(controller: Any, key: str) -> None:
    """Release one side's freeze and perform at most one final deep layout."""
    owners = _freeze_owners(controller)
    tokens = owners.pop(key, [])
    registry = _freeze_registry(controller)
    for token in tokens:
        record = registry.get(token)
        if record is None:
            continue
        count = max(0, int(record["count"]) - 1)
        record["count"] = count
        if count:
            continue
        layout = record.get("layout")
        was_enabled = bool(record.get("was_enabled"))
        registry.pop(token, None)
        if not isinstance(layout, QLayout) or not was_enabled:
            continue
        layout.setEnabled(True)
        # One deterministic endpoint reflow replaces the per-frame recursive
        # relayout that made the previous animation stutter.
        layout.invalidate()
        layout.activate()


def _set_panel_visible(
    self: Any,
    key: str,
    panel: QWidget,
    visible: bool,
) -> None:
    """Reveal/collapse a side panel as one continuous, low-reflow edge move."""
    original_min, original_max = self._panel_constraints[key]
    splitter = getattr(self.window, "main_splitter", None)
    if not isinstance(splitter, QSplitter):
        _release_layout_freeze(self, key)
        _restore_constraints(self, key, panel)
        panel.setVisible(bool(visible))
        return

    index = splitter.indexOf(panel)
    center = _center_index(splitter, index)
    if index < 0 or center < 0:
        _release_layout_freeze(self, key)
        _restore_constraints(self, key, panel)
        panel.setVisible(bool(visible))
        return

    center_widget = splitter.widget(center)

    running = self._panel_animations.pop(key, None)
    reversing = running is not None
    if running is not None:
        # stop() leaves QSplitter exactly where the previous frame put it.  Do
        # not release layouts here: the reverse leg continues with the same
        # frozen geometry and therefore has no one-frame layout spike.
        running.stop()
        try:
            running.deleteLater()
        except RuntimeError:
            pass

    panel.setGraphicsEffect(None)

    if not theme.motion_enabled():
        _release_layout_freeze(self, key)
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

    if not visible and was_visible and current_width >= original_min and not reversing:
        self._panel_widths[key] = min(current_width, original_max)

    target_width = min(
        max(original_min, int(self._panel_widths[key])),
        original_max,
    )

    if visible and not was_visible:
        panel.setMinimumWidth(0)
        panel.setMaximumWidth(original_max)
        splitter.setCollapsible(index, True)
        # Freeze before show() so a hidden Runtime/sidebar does not recursively
        # lay itself out at its temporary zero width.
        _acquire_layout_freeze(self, key, panel, center_widget)
        panel.show()
        _apply_splitter_width(splitter, index, 0)
        current_width = max(0, int(splitter.sizes()[index]))
    elif not visible and not was_visible:
        _release_layout_freeze(self, key)
        _restore_constraints(self, key, panel)
        splitter.setCollapsible(index, False)
        return
    else:
        panel.setMinimumWidth(0)
        panel.setMaximumWidth(original_max)
        splitter.setCollapsible(index, True)
        _acquire_layout_freeze(self, key, panel, center_widget)
        current_width = max(0, int(splitter.sizes()[index]))

    end_width = target_width if visible else 0
    if current_width == end_width:
        if not visible:
            panel.hide()
        else:
            self._panel_widths[key] = current_width
        _restore_constraints(self, key, panel)
        splitter.setCollapsible(index, False)
        _release_layout_freeze(self, key)
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
    # One restrained curve for both directions makes the edge velocity easier
    # to track and avoids the slightly mushy final quarter of OutQuart.
    animation.setEasingCurve(QEasingCurve.Type.OutCubic)

    last_applied = current_width

    def apply_width(value: Any) -> None:
        nonlocal last_applied
        desired = max(0, int(round(float(value))))
        # Qt's animation timer can emit duplicate rounded widths; never run a
        # splitter geometry pass for a frame that does not move the edge.
        if desired == last_applied:
            return
        last_applied = _apply_splitter_width(splitter, index, desired)

    def finish() -> None:
        actual = _apply_splitter_width(splitter, index, end_width)
        if not visible:
            panel.hide()
        else:
            self._panel_widths[key] = max(original_min, min(actual, original_max))
        _restore_constraints(self, key, panel)
        splitter.setCollapsible(index, False)
        if self._panel_animations.get(key) is animation:
            self._panel_animations.pop(key, None)
        # Restore after the splitter is at its final size so descendants only
        # calculate their expensive height-for-width geometry once.
        _release_layout_freeze(self, key)
        animation.deleteLater()

    animation.valueChanged.connect(apply_width)
    animation.finished.connect(finish)
    self._panel_animations[key] = animation
    animation.start()


def install() -> None:
    """Replace only side-panel transitions; Runtime/tab motion stays unchanged."""
    sidebar_motion.SidebarMotionController.set_panel_visible = _set_panel_visible


__all__ = [
    "_acquire_layout_freeze",
    "_apply_splitter_width",
    "_release_layout_freeze",
    "_transition_duration",
    "install",
]
