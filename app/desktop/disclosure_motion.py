"""Smooth transcript disclosures without per-frame layout work.

Animating ``maximumHeight`` inside the transcript is expensive: every frame
invalidates the conversation QVBoxLayout, changes the scroll range and can make
scroll-to-tail fight the disclosure. The previous safety pass therefore made
geometry atomic, but that also made opening/closing feel like a hard jump.

This module keeps the atomic geometry commit and adds motion with a FLIP-like
viewport snapshot:

* capture the pixels below the disclosure edge before changing layout;
* commit the real expanded/collapsed geometry exactly once;
* slide the captured pixels to their new position over the already-settled UI;
* animate the tiny chevron normally;
* never tween message/card height and never rasterise a live terminal widget on
  every frame.

The result reads as a real push/reveal animation while Qt only lays the transcript
out once per click.
"""

from __future__ import annotations

import weakref
from typing import Any, Callable

from PySide6.QtCore import (
    QEasingCurve,
    QPoint,
    QPointF,
    Property,
    QPropertyAnimation,
    QRect,
    Qt,
)
from PySide6.QtGui import QPainter, QPixmap
from PySide6.QtWidgets import QWidget

from app.desktop import message_presentation as messages
from app.desktop import output_presentation as output
from app.desktop import theme
from app.desktop import widgets as base


_INSTALLED = False
_UNBOUNDED_HEIGHT = 16_777_215
_MIN_TRAVEL_PX = 3


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


class _ViewportSlide(QWidget):
    """Paint a frozen viewport slice while its Y offset animates.

    It is an ordinary child of the scroll area's viewport, not a layout item, so
    moving it never changes transcript geometry. The pixmap is captured once at
    click time; animation frames only repaint this lightweight overlay.
    """

    def __init__(
        self,
        view: Any,
        pixmap: QPixmap,
        *,
        anchor_y: int,
    ) -> None:
        viewport = view.viewport()
        super().__init__(viewport)
        self._view_ref = weakref.ref(view)
        self._pixmap = pixmap
        self._offset = 0.0
        self._animation: QPropertyAnimation | None = None
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setGeometry(
            0,
            anchor_y,
            max(1, viewport.width()),
            max(1, viewport.height() - anchor_y),
        )
        self.show()
        self.raise_()

    def _get_offset(self) -> float:
        return self._offset

    def _set_offset(self, value: float) -> None:
        value = float(value)
        if abs(value - self._offset) < 0.01:
            return
        self._offset = value
        self.update()

    offset = Property(float, _get_offset, _set_offset)

    def paintEvent(self, _event: Any) -> None:  # noqa: N802 - Qt override
        painter = QPainter(self)
        # No scaling is involved. Drawing the cached pixmap at a translated Y is
        # substantially cheaper than applying an opacity effect to live terminal
        # or rich-text widgets.
        painter.drawPixmap(QPointF(0.0, self._offset), self._pixmap)

    def play(self, distance: int, *, opening: bool) -> None:
        distance = int(distance)
        if abs(distance) < _MIN_TRAVEL_PX:
            self.finish()
            return

        # Long output should not feel slower just because the panel is taller.
        # Distance only adds a small amount of time, capped tightly.
        travel = min(abs(distance), 360)
        if opening:
            duration = int(min(220, max(155, 150 + travel * 0.18)))
            easing = QEasingCurve.Type.OutCubic
        else:
            duration = int(min(185, max(130, 125 + travel * 0.15)))
            easing = QEasingCurve.Type.InOutCubic

        animation = QPropertyAnimation(self, b"offset", self)
        animation.setDuration(duration)
        animation.setStartValue(0.0)
        animation.setEndValue(float(distance))
        animation.setEasingCurve(easing)
        animation.finished.connect(self.finish)
        self._animation = animation
        animation.start()

    def finish(self) -> None:
        animation, self._animation = self._animation, None
        if animation is not None:
            try:
                animation.stop()
            except RuntimeError:
                pass
            try:
                animation.deleteLater()
            except RuntimeError:
                pass

        view = self._view_ref()
        if view is not None and getattr(view, "_loom_disclosure_overlay", None) is self:
            view._loom_disclosure_overlay = None
        self.hide()
        self.deleteLater()


def _reasoning_sync_body(self: Any, *, animate: bool) -> None:
    """Commit reasoning geometry once; the viewport overlay supplies motion."""

    show = bool(self._expanded)
    self.toggle.set_expanded(show, animate=animate)
    self.toggle.setToolTip("Hide thought process" if show else "Show thought process")
    _stop_animation(self, "_animation")

    self.body.setGraphicsEffect(None)
    self.body.setMaximumHeight(_UNBOUNDED_HEIGHT)
    self.body.setVisible(show)
    self._settle_layout(again=show)


def _activity_sync_body(self: Any, *, animate: bool = False) -> None:
    """Commit activity geometry once; never tween the terminal/card height."""

    has_body = bool(self._body_text)
    show = bool(has_body and self._expanded)
    toggle = self.toggle_button
    toggle.setVisible(has_body)

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
    view._auto_scrolling = False
    view._follow_tail = False


def _dispose_overlay(view: Any) -> None:
    overlay = getattr(view, "_loom_disclosure_overlay", None)
    view._loom_disclosure_overlay = None
    if overlay is None:
        return
    try:
        overlay.finish()
    except RuntimeError:
        pass


def _layout_extent(widget: Any) -> int:
    layout = widget.layout()
    if layout is not None:
        hint = layout.sizeHint()
        if hint.isValid():
            return max(0, int(hint.height()))
    hint = widget.sizeHint()
    return max(0, int(hint.height()))


def _activate_layout_chain(widget: Any, view: Any) -> None:
    """Settle the new geometry synchronously once, before the overlay moves."""

    current = widget
    canvas = getattr(view, "canvas", None)
    while current is not None:
        layout = current.layout()
        if layout is not None:
            layout.invalidate()
            layout.activate()
        current.updateGeometry()
        if current is canvas:
            break
        current = current.parentWidget()
    if canvas is not None and canvas.layout() is not None:
        canvas.layout().invalidate()
        canvas.layout().activate()
    view.viewport().update()


def _capture_overlay(view: Any, anchor_y: int) -> _ViewportSlide | None:
    viewport = view.viewport()
    width = viewport.width()
    height = viewport.height()
    if width <= 1 or height <= 2:
        return None

    anchor_y = max(0, min(int(anchor_y), height - 1))
    crop_height = height - anchor_y
    if crop_height <= 1:
        return None

    _dispose_overlay(view)
    pixmap = viewport.grab(QRect(0, anchor_y, width, crop_height))
    if pixmap.isNull():
        return None

    overlay = _ViewportSlide(view, pixmap, anchor_y=anchor_y)
    view._loom_disclosure_overlay = overlay
    return overlay


def _activity_anchor(card: Any, view: Any) -> int:
    """Viewport Y where tool/process detail begins."""

    local_y: int | None = None
    shell = card.body_shell
    if shell.isVisible() and shell.geometry().height() > 0:
        local_y = int(shell.geometry().top())

    outer = card.layout()
    if local_y is None and outer is not None and outer.count():
        header = outer.itemAt(0).layout()
        if header is not None and header.geometry().height() > 0:
            local_y = int(header.geometry().bottom() + 1 + max(0, outer.spacing()))

    if local_y is None:
        margins = outer.contentsMargins() if outer is not None else None
        bottom = margins.bottom() if margins is not None else 0
        local_y = max(0, card.height() - bottom)

    return int(card.mapTo(view.viewport(), QPoint(0, local_y)).y())


def _reasoning_anchor(block: Any, view: Any) -> int:
    """Viewport Y where thought-process detail begins."""

    if block.body.isVisible() and block.body.geometry().height() > 0:
        local_y = int(block.body.geometry().top())
    else:
        layout = block.layout()
        spacing = max(0, layout.spacing()) if layout is not None else 0
        local_y = int(block.toggle.geometry().bottom() + 1 + spacing)
    return int(block.mapTo(view.viewport(), QPoint(0, local_y)).y())


def _run_snapshot_transition(
    owner: Any,
    *,
    opening: bool,
    anchor: Callable[[Any, Any], int],
    commit: Callable[[], None],
) -> None:
    """Commit layout once, then animate the old pixels to their new Y position."""

    view = _transcript_for(owner)
    if view is None or not theme.motion_enabled():
        commit()
        return

    _suspend_tail_for_disclosure(view)
    old_extent = _layout_extent(owner)
    anchor_y = anchor(owner, view)
    overlay = _capture_overlay(view, anchor_y)

    # Real widgets move to the final layout in this one call. The snapshot still
    # paints the old state above them, so users never see the atomic jump.
    commit()
    _activate_layout_chain(owner, view)
    new_extent = _layout_extent(owner)
    distance = int(new_extent - old_extent)

    if overlay is None or abs(distance) < _MIN_TRAVEL_PX:
        if overlay is not None:
            overlay.finish()
        return

    overlay.play(distance, opening=opening)


def _wire_toggle(view: Any, toggle: Any) -> None:
    if toggle is None or bool(toggle.property("loomDisclosureTailGuard")):
        return
    ref = weakref.ref(view)

    def on_pressed() -> None:
        target = ref()
        if target is not None:
            _suspend_tail_for_disclosure(target)

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
    """Install the snapshot-driven low-reflow disclosure policy once."""

    global _INSTALLED
    if _INSTALLED:
        return

    # Earlier presentation passes may install maximumHeight tweens. Geometry is
    # final here so no later visual polish can reintroduce per-frame transcript
    # layout work.
    messages.ReasoningBlock._sync_body = _reasoning_sync_body
    base.ActivityCard._sync_body = _activity_sync_body
    output.FlatActivityCard._sync_body = _activity_sync_body

    original_reasoning_toggle = messages.ReasoningBlock._toggle
    original_activity_toggle = output.FlatActivityCard._toggle

    def reasoning_toggle(self: Any) -> None:
        opening = not bool(self._expanded)
        _run_snapshot_transition(
            self,
            opening=opening,
            anchor=_reasoning_anchor,
            commit=lambda: original_reasoning_toggle(self),
        )

    def activity_toggle(self: Any) -> None:
        opening = not bool(self._expanded)
        _run_snapshot_transition(
            self,
            opening=opening,
            anchor=_activity_anchor,
            commit=lambda: original_activity_toggle(self),
        )

    messages.ReasoningBlock._toggle = reasoning_toggle
    output.FlatActivityCard._toggle = activity_toggle

    original_render = base.TranscriptView.render

    def render(self: Any, entries: Any) -> None:
        original_render(self, entries)
        _wire_view_disclosures(self)

    base.TranscriptView.render = render

    # The whole activity header is clickable. Freeze auto-tail on press so title
    # and icon clicks have the same viewport semantics as the chevron itself.
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
