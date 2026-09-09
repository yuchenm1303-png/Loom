"""One restrained motion system for transcript disclosures.

The transcript is a live Qt layout. Tweening card heights every frame makes the
scroll range, tail-follow and every row below the disclosure fight the animation.
A full viewport screenshot avoids that reflow, but it can also make the page feel
like one large image is being dragged around.

This coordinator keeps the good part of FLIP and makes the actual disclosure the
visual subject:

* the clicked title/header never moves;
* real detail content is committed to its final geometry exactly once;
* opening content gets a tiny opacity/3px settle, never scale or spring motion;
* only the content column below the disclosure edge is snapshotted and displaced;
* opening and closing use fixed, product-wide timings instead of distance-based
  durations;
* chevrons use the same shared timing tokens as both reasoning and task rows;
* reduced-motion users get the final state immediately.

The result is a short top-reveal: content appears to have been sitting directly
under the row all along, while the rows beneath it simply make room.
"""

from __future__ import annotations

import weakref
from typing import Any, Callable

from PySide6.QtCore import (
    QEasingCurve,
    QPoint,
    QPointF,
    Property,
    QParallelAnimationGroup,
    QPropertyAnimation,
    QRect,
    Qt,
)
from PySide6.QtGui import QPainter, QPixmap
from PySide6.QtWidgets import QGraphicsOpacityEffect, QWidget

from app.desktop import message_presentation as messages
from app.desktop import output_presentation as output
from app.desktop import theme
from app.desktop import widgets as base
from app.desktop.disclosure_motion_tokens import (
    CONTENT_OFFSET_PX,
    CONTENT_REVEAL_MS,
    DISCLOSURE_CLOSE_MS,
    DISCLOSURE_OPEN_MS,
)


_INSTALLED = False
_UNBOUNDED_HEIGHT = 16_777_215
_MIN_TRAVEL_PX = 3
_COLUMN_GUTTER_PX = 6


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


def _clear_content_motion(owner: Any) -> None:
    """Return a previously animated live surface to its exact layout geometry."""
    animation = getattr(owner, "_loom_content_animation", None)
    surface = getattr(owner, "_loom_content_surface", None)
    target_pos = getattr(owner, "_loom_content_target_pos", None)
    owner._loom_content_animation = None
    owner._loom_content_surface = None
    owner._loom_content_target_pos = None

    if animation is not None:
        try:
            animation.stop()
        except RuntimeError:
            pass
        try:
            animation.deleteLater()
        except RuntimeError:
            pass
    if surface is not None:
        try:
            if target_pos is not None:
                surface.move(target_pos)
            surface.setGraphicsEffect(None)
        except RuntimeError:
            pass


class _ViewportSlide(QWidget):
    """Move only the old content column below a disclosure edge.

    The snapshot is intentionally narrow: blank viewport gutters are never part
    of the moving object.  That small distinction keeps the interaction reading
    as nearby rows making room instead of the whole application being dragged.
    """

    def __init__(
        self,
        view: Any,
        pixmap: QPixmap,
        *,
        x: int,
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
            x,
            anchor_y,
            max(1, pixmap.width()),
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
        painter.drawPixmap(QPointF(0.0, self._offset), self._pixmap)

    def play(self, distance: int, *, opening: bool) -> None:
        distance = int(distance)
        if abs(distance) < _MIN_TRAVEL_PX:
            self.finish()
            return

        animation = QPropertyAnimation(self, b"offset", self)
        animation.setDuration(DISCLOSURE_OPEN_MS if opening else DISCLOSURE_CLOSE_MS)
        animation.setStartValue(0.0)
        animation.setEndValue(float(distance))
        # Direct response is more important than physically accurate inertia in
        # a high-frequency disclosure. OutCubic moves immediately and settles
        # quietly, with no bounce or spring tail.
        animation.setEasingCurve(QEasingCurve.Type.OutCubic)
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
    """Commit reasoning geometry once; the coordinator supplies visual motion."""
    show = bool(self._expanded)
    self.toggle.set_expanded(show, animate=animate)
    self.toggle.setToolTip("Hide thought process" if show else "Show thought process")
    _stop_animation(self, "_animation")

    self.body.setGraphicsEffect(None)
    self.body.setMaximumHeight(_UNBOUNDED_HEIGHT)
    self.body.setVisible(show)
    self._settle_layout(again=show)


def _activity_sync_body(self: Any, *, animate: bool = False) -> None:
    """Commit task-detail geometry once; never tween a live terminal/card height."""
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
    """Settle the new geometry synchronously once, before displacement moves."""
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


def _column_bounds(owner: Any, view: Any) -> tuple[int, int]:
    viewport = view.viewport()
    try:
        left = owner.mapTo(viewport, QPoint(0, 0)).x() - _COLUMN_GUTTER_PX
        right = left + owner.width() + _COLUMN_GUTTER_PX * 2
    except RuntimeError:
        return 0, viewport.width()
    left = max(0, int(left))
    right = min(viewport.width(), max(left + 1, int(right)))
    return left, max(1, right - left)


def _capture_overlay(view: Any, owner: Any, anchor_y: int) -> _ViewportSlide | None:
    viewport = view.viewport()
    height = viewport.height()
    if viewport.width() <= 1 or height <= 2:
        return None

    anchor_y = max(0, min(int(anchor_y), height - 1))
    crop_height = height - anchor_y
    if crop_height <= 1:
        return None

    x, width = _column_bounds(owner, view)
    _dispose_overlay(view)
    pixmap = viewport.grab(QRect(x, anchor_y, width, crop_height))
    if pixmap.isNull():
        return None

    overlay = _ViewportSlide(view, pixmap, x=x, anchor_y=anchor_y)
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


def _animate_open_surface(owner: Any, surface: QWidget, *, quiet: bool) -> None:
    """Give the real revealed content a tiny settle while geometry stays fixed."""
    _clear_content_motion(owner)
    try:
        target_pos = surface.pos()
        effect = QGraphicsOpacityEffect(surface)
        surface.setGraphicsEffect(effect)
        # Reasoning is subordinate prose and can enter more softly. Task panels
        # are structural surfaces, so start them closer to their final opacity.
        effect.setOpacity(0.18 if quiet else 0.52)
        surface.move(target_pos + QPoint(0, CONTENT_OFFSET_PX))
    except RuntimeError:
        return

    group = QParallelAnimationGroup(owner)
    opacity = QPropertyAnimation(effect, b"opacity", group)
    opacity.setDuration(CONTENT_REVEAL_MS)
    opacity.setStartValue(effect.opacity())
    opacity.setEndValue(1.0)
    opacity.setEasingCurve(QEasingCurve.Type.OutCubic)
    group.addAnimation(opacity)

    position = QPropertyAnimation(surface, b"pos", group)
    position.setDuration(DISCLOSURE_OPEN_MS)
    position.setStartValue(surface.pos())
    position.setEndValue(target_pos)
    position.setEasingCurve(QEasingCurve.Type.OutCubic)
    group.addAnimation(position)

    owner._loom_content_animation = group
    owner._loom_content_surface = surface
    owner._loom_content_target_pos = target_pos

    def finish() -> None:
        if getattr(owner, "_loom_content_animation", None) is not group:
            return
        owner._loom_content_animation = None
        owner._loom_content_surface = None
        owner._loom_content_target_pos = None
        try:
            surface.move(target_pos)
            surface.setGraphicsEffect(None)
        except RuntimeError:
            pass
        group.deleteLater()

    group.finished.connect(finish)
    group.start()


def _run_transition(
    owner: Any,
    *,
    opening: bool,
    anchor: Callable[[Any, Any], int],
    surface: Callable[[Any], QWidget],
    quiet: bool,
    commit: Callable[[], None],
) -> None:
    """Top-reveal live content while only displaced rows use cached pixels."""
    _clear_content_motion(owner)
    view = _transcript_for(owner)
    if view is None or not theme.motion_enabled():
        commit()
        return

    _suspend_tail_for_disclosure(view)
    old_extent = _layout_extent(owner)
    anchor_y = anchor(owner, view)
    overlay = _capture_overlay(view, owner, anchor_y)

    # Commit the real final state in one layout pass. The cached old rows cover
    # the atomic displacement; opening then reveals the live surface underneath.
    commit()
    _activate_layout_chain(owner, view)
    new_extent = _layout_extent(owner)
    distance = int(new_extent - old_extent)

    if opening:
        try:
            revealed = surface(owner)
            if revealed.isVisible():
                _animate_open_surface(owner, revealed, quiet=quiet)
        except (AttributeError, RuntimeError):
            pass

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
    """Install the shared low-reflow disclosure choreography once."""
    global _INSTALLED
    if _INSTALLED:
        return

    # Earlier presentation passes own styling and component semantics. Geometry
    # ends here so no later hook can reintroduce per-frame transcript relayout.
    messages.ReasoningBlock._sync_body = _reasoning_sync_body
    base.ActivityCard._sync_body = _activity_sync_body
    output.FlatActivityCard._sync_body = _activity_sync_body

    original_reasoning_toggle = messages.ReasoningBlock._toggle
    original_activity_toggle = output.FlatActivityCard._toggle

    def reasoning_toggle(self: Any) -> None:
        opening = not bool(self._expanded)
        _run_transition(
            self,
            opening=opening,
            anchor=_reasoning_anchor,
            surface=lambda owner: owner.body,
            quiet=True,
            commit=lambda: original_reasoning_toggle(self),
        )

    def activity_toggle(self: Any) -> None:
        opening = not bool(self._expanded)
        _run_transition(
            self,
            opening=opening,
            anchor=_activity_anchor,
            surface=lambda owner: owner.body_shell,
            quiet=False,
            commit=lambda: original_activity_toggle(self),
        )

    messages.ReasoningBlock._toggle = reasoning_toggle
    output.FlatActivityCard._toggle = activity_toggle

    original_render = base.TranscriptView.render

    def render(self: Any, entries: Any) -> None:
        original_render(self, entries)
        _wire_view_disclosures(self)

    base.TranscriptView.render = render

    # The entire activity header is clickable. Freeze auto-tail on press so an
    # icon/title click and a chevron click have identical viewport semantics.
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
