"""Smooth splitter-native side-panel motion for the desktop client.

The first motion pass animated a panel's ``maximumWidth`` and opacity at the
same time. In a ``QSplitter`` that can briefly leave the splitter section
allocated while the child has already faded, producing the large blank flash
visible when the sidebar or Runtime inspector is toggled.

This patch keeps the panel fully painted and animates one exact width instead.
The splitter therefore reallocates that space to the conversation continuously,
frame by frame. It also supports reversing an in-flight animation without a
jump.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QEasingCurve, QVariantAnimation
from PySide6.QtWidgets import QWidget

from app.desktop import sidebar_motion, theme


_PANEL_DURATION_MS = 210
_PANEL_DURATION_LONG_MS = 260


def _set_exact_width(panel: QWidget, width: int | float) -> None:
    value = max(0, int(round(float(width))))
    if panel.minimumWidth() != value:
        panel.setMinimumWidth(value)
    if panel.maximumWidth() != value:
        panel.setMaximumWidth(value)
    panel.updateGeometry()


def _restore_constraints(controller: Any, key: str, panel: QWidget) -> None:
    minimum, maximum = controller._panel_constraints[key]
    panel.setMinimumWidth(minimum)
    panel.setMaximumWidth(maximum)
    panel.updateGeometry()


def _set_panel_visible(
    self: Any,
    key: str,
    panel: QWidget,
    visible: bool,
) -> None:
    """Animate splitter width without fading away allocated panel space."""
    original_min, original_max = self._panel_constraints[key]
    splitter = getattr(self.window, "main_splitter", None)
    index = splitter.indexOf(panel) if splitter is not None else -1

    running = self._panel_animations.pop(key, None)
    reversing = running is not None
    if running is not None:
        running.stop()
        running.deleteLater()

    # A stale graphics effect from the older animation is exactly what can make
    # an allocated splitter section appear empty for a frame. Never carry it
    # into a panel transition.
    panel.setGraphicsEffect(None)

    if not theme.motion_enabled() or splitter is None:
        _restore_constraints(self, key, panel)
        panel.setVisible(bool(visible))
        if index >= 0:
            splitter.setCollapsible(index, False)
        return

    was_visible = panel.isVisible()
    current_width = max(0, panel.width() if was_visible else 0)

    # Remember only a settled, meaningful open width. If the user reverses an
    # expansion while it is still narrower than the panel's normal minimum,
    # keep the previous target so the next open does not permanently shrink.
    if not visible and current_width >= original_min and not reversing:
        self._panel_widths[key] = min(current_width, original_max)

    target_width = min(
        max(original_min, int(self._panel_widths[key])),
        original_max,
    )

    if visible and not was_visible:
        _set_exact_width(panel, 0)
        panel.show()
        current_width = 0
    elif not visible and not was_visible:
        _restore_constraints(self, key, panel)
        if index >= 0:
            splitter.setCollapsible(index, False)
        return
    else:
        # Pin both constraints to the current rendered width. Animating only
        # maximumWidth lets QSplitter briefly keep stale geometry; an exact width
        # makes the center column reclaim/give up the same pixels every frame.
        current_width = max(0, panel.width())
        _set_exact_width(panel, current_width)

    if index >= 0:
        # QSplitter was created with childrenCollapsible(False). Temporarily
        # allow only the panel being animated to reach zero; the conversation
        # section itself remains non-collapsible.
        splitter.setCollapsible(index, True)

    end_width = target_width if visible else 0
    if current_width == end_width:
        if not visible:
            panel.hide()
        _restore_constraints(self, key, panel)
        if index >= 0:
            splitter.setCollapsible(index, False)
        return

    animation = QVariantAnimation(self)
    # Opening breathes into place with a softer curve; closing gets out of
    # the way quickly because the user has dismissed the panel and shouldn't
    # have to wait for it to leave.
    animation.setDuration(_PANEL_DURATION_LONG_MS if visible else _PANEL_DURATION_MS)
    animation.setStartValue(current_width)
    animation.setEndValue(end_width)
    animation.setEasingCurve(
        QEasingCurve.Type.OutQuint if visible else QEasingCurve.Type.InOutQuart
    )

    def apply_width(value: Any) -> None:
        _set_exact_width(panel, value)
        # ``updateGeometry`` is enough — it lets the splitter recompute its
        # section sizes from the new exact width. Calling ``update`` here
        # would repaint the whole splitter every frame, which is the real
        # source of the "dragged through mud" feel during the transition.
        splitter.updateGeometry()

    def finish() -> None:
        _set_exact_width(panel, end_width)
        if not visible:
            panel.hide()
        _restore_constraints(self, key, panel)
        if index >= 0:
            splitter.setCollapsible(index, False)
        if self._panel_animations.get(key) is animation:
            self._panel_animations.pop(key, None)
        splitter.updateGeometry()
        animation.deleteLater()

    animation.valueChanged.connect(apply_width)
    animation.finished.connect(finish)
    self._panel_animations[key] = animation
    animation.start()


def install() -> None:
    """Replace only the side-panel transition; keep the rest of motion intact."""
    sidebar_motion.SidebarMotionController.set_panel_visible = _set_panel_visible


__all__ = ["install"]
