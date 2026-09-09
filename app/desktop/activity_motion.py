"""Stable, visible disclosure motion for transcript activity output.

The first disclosure pass intentionally skipped height animation once a body was
larger than 180px. That kept huge diffs stable, but it also made ordinary tool
rows such as ``exec`` snap open because their argument JSON often crosses that
threshold.

This pass keeps the large-diff protection while giving normal tool/process rows
a real disclosure transition:
- semantic chevron rotation remains the primary state cue;
- normal tool/process bodies use a short height reveal;
- small bodies also get a restrained opacity fade;
- very large documents still settle in one layout pass to avoid scrollbar jank;
- transcript tail-follow stays suspended while geometry changes.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import (
    QEasingCurve,
    QParallelAnimationGroup,
    QPropertyAnimation,
    QTimer,
    Qt,
)
from PySide6.QtWidgets import QGraphicsOpacityEffect, QPlainTextEdit

from app.desktop import output_presentation as presentation
from app.desktop import theme


_INSTALLED = False
_BODY_MIN_HEIGHT = 30
_BODY_MAX_HEIGHT = 360
_REVEAL_MS = 165
_COLLAPSE_MS = 130
_FADE_IN_MS = 115
_FADE_OUT_MS = 90
_UNBOUNDED_HEIGHT = 16_777_215

# Animate ordinary command/tool output much more generously than a diff. Large
# diffs are the expensive pathological case because they combine a big document,
# two scrollbars and a changing transcript scroll range.
_TWEEN_HEIGHT_LIMIT = {
    "tool": 430,
    "process": 330,
    "error": 270,
    "diff": 155,
}
_TWEEN_CHAR_LIMIT = {
    "tool": 7_000,
    "process": 6_000,
    "error": 4_500,
    "diff": 2_400,
}
_TWEEN_BLOCK_LIMIT = {
    "tool": 52,
    "process": 44,
    "error": 34,
    "diff": 22,
}
_FADE_HEIGHT_LIMIT = 250
_FADE_CHAR_LIMIT = 3_200
_FADE_BLOCK_LIMIT = 26


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

    def restore() -> None:
        try:
            view._follow_tail = True
            view.scroll_to_tail()
        except RuntimeError:
            pass

    QTimer.singleShot(0, restore)


def _clear_effect(card: Any) -> None:
    try:
        card.body_shell.setGraphicsEffect(None)
    except RuntimeError:
        pass


def _stop_animation(card: Any) -> None:
    animation = getattr(card, "_body_animation", None)
    card._body_animation = None
    if animation is None:
        _clear_effect(card)
        return
    try:
        animation.stop()
    except RuntimeError:
        _clear_effect(card)
        return
    try:
        animation.deleteLater()
    except RuntimeError:
        pass
    _clear_effect(card)


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


def _document_metrics(card: Any) -> tuple[int, int]:
    text = str(getattr(card, "_body_text", "") or "")
    try:
        blocks = max(1, int(card.body.document().blockCount()))
    except (AttributeError, RuntimeError, TypeError, ValueError):
        blocks = max(1, text.count("\n") + 1)
    return len(text), blocks


def _can_tween(card: Any, travel: int) -> bool:
    kind = str(getattr(card, "kind", "tool") or "tool")
    chars, blocks = _document_metrics(card)
    return (
        travel <= _TWEEN_HEIGHT_LIMIT.get(kind, 280)
        and chars <= _TWEEN_CHAR_LIMIT.get(kind, 4_000)
        and blocks <= _TWEEN_BLOCK_LIMIT.get(kind, 32)
    )


def _can_fade(card: Any, target: int) -> bool:
    chars, blocks = _document_metrics(card)
    return (
        target <= _FADE_HEIGHT_LIMIT
        and chars <= _FADE_CHAR_LIMIT
        and blocks <= _FADE_BLOCK_LIMIT
    )


def _settle(card: Any, show: bool) -> None:
    _clear_effect(card)
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

    travel = target if show else current
    if not _can_tween(card, travel):
        # Large outputs deliberately do not tween geometry. The chevron still
        # rotates, so the interaction has feedback without reintroducing the
        # large-diff shake this module exists to prevent.
        _settle(card, show)
        return

    if show:
        start, end = max(0, current), target
        card.body_shell.setMaximumHeight(start)
        card.body_shell.show()
        duration = _REVEAL_MS
        easing = QEasingCurve.Type.OutCubic
    else:
        if not card.body_shell.isVisible():
            _settle(card, False)
            return
        start, end = max(0, current), 0
        duration = _COLLAPSE_MS
        easing = QEasingCurve.Type.InCubic

    group = QParallelAnimationGroup(card)

    height = QPropertyAnimation(card.body_shell, b"maximumHeight", group)
    height.setDuration(duration)
    height.setStartValue(start)
    height.setEndValue(end)
    height.setEasingCurve(easing)
    group.addAnimation(height)

    # Small command/tool details can afford a very light fade. It makes the
    # disclosure feel deliberate without compositing a huge code/diff surface.
    if _can_fade(card, target):
        effect = QGraphicsOpacityEffect(card.body_shell)
        card.body_shell.setGraphicsEffect(effect)
        opacity = QPropertyAnimation(effect, b"opacity", group)
        if show:
            effect.setOpacity(0.18)
            opacity.setDuration(_FADE_IN_MS)
            opacity.setStartValue(0.18)
            opacity.setEndValue(1.0)
            opacity.setEasingCurve(QEasingCurve.Type.OutCubic)
        else:
            effect.setOpacity(1.0)
            opacity.setDuration(_FADE_OUT_MS)
            opacity.setStartValue(1.0)
            opacity.setEndValue(0.35)
            opacity.setEasingCurve(QEasingCurve.Type.InCubic)
        group.addAnimation(opacity)

    def finish() -> None:
        card._body_animation = None
        _clear_effect(card)
        card.body_shell.setMaximumHeight(_UNBOUNDED_HEIGHT)
        card.body_shell.setVisible(show)
        if show:
            _prepare_geometry(card)
        card.updateGeometry()
        _release_tail(card)
        group.deleteLater()

    group.finished.connect(finish)
    card._body_animation = group
    group.start()


def install() -> None:
    """Install the stable disclosure implementation once."""
    global _INSTALLED
    if _INSTALLED:
        return
    _INSTALLED = True

    presentation.FlatActivityCard._sync_body = _stable_sync_body
    presentation.FlatActivityCard._sync_height = _stable_sync_height


__all__ = ["install"]
