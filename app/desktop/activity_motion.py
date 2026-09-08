"""Stable disclosure motion for transcript activity output.

Animating a large QPlainTextEdit by changing its parent's height every frame is
expensive: Qt repeatedly relays out scrollbars, the transcript scroll range
changes on every frame, and the tail-follow animation then fights that layout.
The result is the visible shake/jank that large diffs used to produce.

This hook keeps motion where it helps:
- the disclosure chevron always gets its small rotation;
- short output surfaces use one cheap maximum-height tween;
- large code/diff surfaces settle their geometry once and reveal immediately;
- transcript tail-follow is suspended while geometry changes, then restored once.

No opacity effect is applied to the code surface, so text stays crisp and large
outputs do not require full-surface compositing on every animation frame.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QEasingCurve, QPropertyAnimation, QTimer, Qt
from PySide6.QtWidgets import QPlainTextEdit

from app.desktop import output_presentation as presentation
from app.desktop import theme


_INSTALLED = False
_SHORT_REVEAL_LIMIT = 180
_BODY_MIN_HEIGHT = 30
_BODY_MAX_HEIGHT = 360
_REVEAL_MS = 125
_COLLAPSE_MS = 105
_UNBOUNDED_HEIGHT = 16_777_215


def _transcript_for(card: Any) -> Any | None:
    parent = card.parentWidget()
    while parent is not None:
        if hasattr(parent, "_follow_tail") and hasattr(parent, "scroll_to_tail"):
            return parent
        parent = parent.parentWidget()
    return None


def _hold_tail(card: Any) -> None:
    """Stop scroll-range animation from fighting disclosure geometry."""
    if getattr(card, "_activity_tail_hold", None) is not None:
        return
    view = _transcript_for(card)
    if view is None:
        card._activity_tail_hold = (None, False)
        return
    following = bool(getattr(view, "_follow_tail", False))
    if following:
        stop = getattr(view, "_stop_tail_animation", None)
        if callable(stop):
            stop()
        view._follow_tail = False
    card._activity_tail_hold = (view, following)


def _release_tail(card: Any) -> None:
    hold = getattr(card, "_activity_tail_hold", None)
    card._activity_tail_hold = None
    if not hold:
        return
    view, following = hold
    if view is None or not following:
        return

    # QScrollArea publishes its new range after the surrounding layout settles.
    # Restore following on the next event-loop turn, then perform one exact tail
    # placement instead of starting/restarting a scroll animation per frame.
    def restore() -> None:
        try:
            view._follow_tail = True
            view.scroll_to_tail()
        except RuntimeError:
            pass

    QTimer.singleShot(0, restore)


def _stop_animation(card: Any) -> None:
    animation = getattr(card, "_body_animation", None)
    card._body_animation = None
    if animation is None:
        return
    try:
        animation.stop()
    except RuntimeError:
        return
    try:
        animation.deleteLater()
    except RuntimeError:
        pass


def _body_height(card: Any) -> int:
    """Measure once, reserving scrollbar space before the panel becomes visible."""
    body = card.body
    document = body.document()
    line_spacing = body.fontMetrics().lineSpacing()
    lines = max(1, document.blockCount())
    raw = (
        lines * line_spacing
        + document.documentMargin() * 2
        + body.frameWidth() * 2
        + 4
    )

    needs_vertical = raw > _BODY_MAX_HEIGHT
    vertical_policy = (
        Qt.ScrollBarPolicy.ScrollBarAlwaysOn
        if needs_vertical
        else Qt.ScrollBarPolicy.ScrollBarAlwaysOff
    )
    if body.verticalScrollBarPolicy() != vertical_policy:
        body.setVerticalScrollBarPolicy(vertical_policy)

    shell_layout = card.body_shell.layout()
    shell_margins = shell_layout.contentsMargins() if shell_layout is not None else None
    horizontal_frame = 0
    if shell_margins is not None:
        horizontal_frame += shell_margins.left() + shell_margins.right()
    horizontal_frame += card.body_shell.frameWidth() * 2
    if needs_vertical:
        horizontal_frame += body.verticalScrollBar().sizeHint().width()

    available = max(80, card.width() - horizontal_frame)
    if (
        body.lineWrapMode() == QPlainTextEdit.LineWrapMode.NoWrap
        and document.size().width() > available
    ):
        raw += body.horizontalScrollBar().sizeHint().height()

    return int(max(_BODY_MIN_HEIGHT, min(float(raw), _BODY_MAX_HEIGHT)))


def _prepare_geometry(card: Any) -> int:
    if getattr(card, "_activity_measure_guard", False):
        return max(0, card.body_shell.sizeHint().height())

    card._activity_measure_guard = True
    try:
        desired_body = _body_height(card)
        if card.body.height() != desired_body:
            card.body.setFixedHeight(desired_body)

        layout = card.body_shell.layout()
        if layout is not None:
            layout.invalidate()
            layout.activate()
            target = layout.sizeHint().height() + card.body_shell.frameWidth() * 2
        else:
            target = desired_body
        return max(desired_body, int(target))
    finally:
        card._activity_measure_guard = False


def _settle(card: Any, show: bool) -> None:
    card.body_shell.setMaximumHeight(_UNBOUNDED_HEIGHT)
    card.body_shell.setVisible(show)
    if show:
        _prepare_geometry(card)
    card.updateGeometry()
    _release_tail(card)


def _stable_sync_height(card: Any) -> None:
    """Keep streamed content stable, but never resize inside an active tween."""
    if card.body_shell.isHidden() or getattr(card, "_activity_measure_guard", False):
        return
    if getattr(card, "_body_animation", None) is not None:
        return
    _prepare_geometry(card)
    card.updateGeometry()


def _stable_sync_body(card: Any, *, animate: bool = False) -> None:
    has_body = bool(card._body_text)
    show = bool(has_body and card._expanded)

    card.toggle_button.setVisible(has_body)
    card.toggle_button.set_expanded(show, animate=animate)
    card.toggle_button.setToolTip("Hide output" if show else "Show output")

    _stop_animation(card)
    _hold_tail(card)

    if not has_body:
        _settle(card, False)
        return

    target = _prepare_geometry(card)
    current = card.body_shell.height() if card.body_shell.isVisible() else 0

    if not animate or not theme.motion_enabled():
        _settle(card, show)
        return

    # Large code/diff surfaces are the pathological case. A 300-400px height
    # tween forces dozens of expensive QPlainTextEdit + scrollbar relayouts.
    # Reveal them in one stable geometry update; the chevron still provides the
    # interaction motion, and the transcript no longer shakes.
    travel = target if show else current
    if travel > _SHORT_REVEAL_LIMIT:
        _settle(card, show)
        return

    if show:
        card.body_shell.setMaximumHeight(max(0, current))
        card.body_shell.show()
        start, end = max(0, current), target
        duration = _REVEAL_MS
    else:
        if not card.body_shell.isVisible():
            _settle(card, False)
            return
        start, end = max(0, current), 0
        duration = _COLLAPSE_MS

    animation = QPropertyAnimation(card.body_shell, b"maximumHeight", card)
    animation.setDuration(duration)
    animation.setStartValue(start)
    animation.setEndValue(end)
    animation.setEasingCurve(QEasingCurve.Type.OutCubic)

    def finish() -> None:
        card._body_animation = None
        card.body_shell.setMaximumHeight(_UNBOUNDED_HEIGHT)
        card.body_shell.setVisible(show)
        if show:
            _prepare_geometry(card)
        card.updateGeometry()
        _release_tail(card)
        animation.deleteLater()

    animation.finished.connect(finish)
    card._body_animation = animation
    animation.start()


def install() -> None:
    """Install the stable disclosure implementation once."""
    global _INSTALLED
    if _INSTALLED:
        return
    _INSTALLED = True

    presentation.FlatActivityCard._sync_body = _stable_sync_body
    presentation.FlatActivityCard._sync_height = _stable_sync_height


__all__ = ["install"]
