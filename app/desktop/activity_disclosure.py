"""Refined disclosure affordance for compact transcript activity rows.

The transcript uses dense, semantic action rows. Their disclosure control should
feel like part of the row rather than a tiny independent target: the whole row
header toggles details, while a small native chevron provides quiet state and
motion feedback.

Only header clicks toggle. Expanded output remains fully interactive for text
selection and scrolling.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QEasingCurve, QPointF, Property, QPropertyAnimation, Qt
from PySide6.QtGui import QColor, QMouseEvent, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QPushButton, QWidget

from app.desktop import output_presentation as presentation
from app.desktop import theme
from app.desktop.disclosure_motion_tokens import CHEVRON_CLOSE_MS, CHEVRON_OPEN_MS


_INSTALLED = False


class DisclosureChevron(QPushButton):
    """A precise, low-contrast disclosure chevron with native rotation motion."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._progress = 0.0
        self._animation: QPropertyAnimation | None = None
        self.setObjectName("activityDisclosure")
        self.setText("")
        self.setFlat(True)
        self.setFixedSize(22, 22)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setStyleSheet(
            "QPushButton#activityDisclosure {"
            " background:transparent; border:none; border-radius:6px; padding:0;"
            "}"
            "QPushButton#activityDisclosure:hover { background:#181c23; }"
            "QPushButton#activityDisclosure:pressed { background:#20252e; }"
        )

    def _get_progress(self) -> float:
        return self._progress

    def _set_progress(self, value: float) -> None:
        value = max(0.0, min(1.0, float(value)))
        if abs(value - self._progress) < 0.001:
            return
        self._progress = value
        self.update()

    progress = Property(float, _get_progress, _set_progress)

    def _stop_animation(self) -> None:
        animation, self._animation = self._animation, None
        if animation is None:
            return
        try:
            animation.stop()
            animation.deleteLater()
        except RuntimeError:
            pass

    def set_expanded(self, expanded: bool, *, animate: bool) -> None:
        target = 1.0 if expanded else 0.0
        self._stop_animation()
        if not animate or not theme.motion_enabled() or abs(self._progress - target) < 0.01:
            self._set_progress(target)
            return

        animation = QPropertyAnimation(self, b"progress", self)
        animation.setDuration(CHEVRON_OPEN_MS if expanded else CHEVRON_CLOSE_MS)
        animation.setStartValue(self._progress)
        animation.setEndValue(target)
        animation.setEasingCurve(QEasingCurve.Type.OutCubic)

        def finish() -> None:
            self._animation = None
            self._set_progress(target)
            try:
                animation.deleteLater()
            except RuntimeError:
                pass

        animation.finished.connect(finish)
        self._animation = animation
        animation.start()

    def paintEvent(self, event: Any) -> None:  # noqa: N802 - Qt override
        # Let QPushButton paint only its restrained hover/pressed surface.
        super().paintEvent(event)

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        if self.isDown():
            color = QColor("#e4e8ef")
        elif self.underMouse():
            color = QColor("#bdc4cf")
        else:
            color = QColor("#7d8694")
        if not self.isEnabled():
            color.setAlpha(80)

        painter.translate(self.width() / 2.0, self.height() / 2.0)
        painter.rotate(90.0 * self._progress)
        # Keep the visual centre optically stable while the right-facing
        # chevron rotates down. The glyph itself is deliberately smaller than
        # its hit target so dense rows stay calm.
        painter.translate(-0.25, 0.0)

        pen = QPen(
            color,
            1.20,
            Qt.PenStyle.SolidLine,
            Qt.PenCapStyle.RoundCap,
            Qt.PenJoinStyle.RoundJoin,
        )
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)

        path = QPainterPath()
        path.moveTo(QPointF(-2.35, -3.35))
        path.lineTo(QPointF(1.15, 0.0))
        path.lineTo(QPointF(-2.35, 3.35))
        painter.drawPath(path)


def _header_layout(card: Any) -> Any | None:
    outer = card.layout()
    if outer is None:
        return None
    for index in range(outer.count()):
        layout = outer.itemAt(index).layout()
        if layout is not None and layout.indexOf(card.toggle_button) >= 0:
            return layout
    return None


def _replace_chevron(card: Any) -> None:
    old = card.toggle_button
    header = _header_layout(card)
    index = header.indexOf(old) if header is not None else -1
    if header is not None:
        header.removeWidget(old)
    old.hide()
    old.deleteLater()

    button = DisclosureChevron(card)
    button.clicked.connect(card._toggle)
    if header is not None and index >= 0:
        header.insertWidget(index, button)
    elif header is not None:
        header.addWidget(button)
    card.toggle_button = button


def _header_contains(card: Any, point: Any) -> bool:
    header = _header_layout(card)
    if header is None:
        return False
    rect = header.geometry().adjusted(-2, -2, 2, 2)
    return rect.contains(point)


def install() -> None:
    """Install the refined chevron and full-row disclosure interaction once."""
    global _INSTALLED
    if _INSTALLED:
        return
    _INSTALLED = True

    original_init = presentation.FlatActivityCard.__init__
    original_update = presentation.FlatActivityCard.update_card
    original_press = presentation.FlatActivityCard.mousePressEvent
    original_release = presentation.FlatActivityCard.mouseReleaseEvent

    def card_init(self: Any, kind: str, parent: QWidget | None = None) -> None:
        original_init(self, kind, parent)
        # Inline transcript activity should stay compact until the reader asks
        # for details. Base cards historically pre-opened errors/diffs, which
        # made a busy agent turn expand into a wall of output by default.
        self._expanded = False
        _replace_chevron(self)
        # Title/icon clicks should land on the row itself. Expanded body widgets
        # stay normal so code selection and scrollbars never toggle the card.
        self.title_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.subtitle_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setProperty("disclosurePressed", False)
        self.setStyleSheet(
            self.styleSheet()
            + "\nQFrame#activityCard[disclosureInteractive=\"true\"]:hover {"
              " background:#12161d; border:none; border-radius:6px;"
              "}"
              "QFrame#activityCard[disclosurePressed=\"true\"] {"
              " background:#171c24; border:none; border-radius:6px;"
              "}"
        )

    def card_update(
        self: Any,
        *,
        title: str,
        subtitle: str = "",
        status: str = "",
        body: str = "",
        auto_expand: bool | None = None,
    ) -> None:
        # Running/failed tools and processes used to request automatic
        # expansion from the renderer. Ignore those requests until the user has
        # explicitly opened this row; after that, the card's own toggle state is
        # authoritative across streaming/status updates.
        if not self._user_toggled:
            auto_expand = False
        original_update(
            self,
            title=title,
            subtitle=subtitle,
            status=status,
            body=body,
            auto_expand=auto_expand,
        )
        interactive = bool(self._body_text)
        self.setProperty("disclosureInteractive", interactive)
        self.setCursor(
            Qt.CursorShape.PointingHandCursor if interactive else Qt.CursorShape.ArrowCursor
        )
        self.style().unpolish(self)
        self.style().polish(self)
        self.update()
        self.toggle_button.set_expanded(
            bool(self._body_text and self._expanded),
            animate=False,
        )

    def mouse_press(self: Any, event: QMouseEvent) -> None:
        if (
            event.button() == Qt.MouseButton.LeftButton
            and bool(self._body_text)
            and _header_contains(self, event.position().toPoint())
        ):
            self.setProperty("disclosurePressed", True)
            self.style().unpolish(self)
            self.style().polish(self)
            self.update()
            event.accept()
            return
        original_press(self, event)

    def mouse_release(self: Any, event: QMouseEvent) -> None:
        was_pressed = bool(self.property("disclosurePressed"))
        if was_pressed:
            self.setProperty("disclosurePressed", False)
            self.style().unpolish(self)
            self.style().polish(self)
            self.update()

        if (
            was_pressed
            and event.button() == Qt.MouseButton.LeftButton
            and bool(self._body_text)
            and _header_contains(self, event.position().toPoint())
        ):
            self._toggle()
            event.accept()
            return
        original_release(self, event)

    presentation.FlatActivityCard.__init__ = card_init
    presentation.FlatActivityCard.update_card = card_update
    presentation.FlatActivityCard.mousePressEvent = mouse_press
    presentation.FlatActivityCard.mouseReleaseEvent = mouse_release


__all__ = ["DisclosureChevron", "install"]
