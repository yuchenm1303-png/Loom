"""Anchored motion for the permission and model composer pickers.

The composer controls sit at the bottom edge of the window. Their menus should
feel attached to the control that opened them rather than appearing as a native
popup that simply snaps into place.  This pass keeps the motion deliberately
small and cheap: the menu starts a few pixels toward its source control, fades
in, and settles to the already-bounded final position.

No size animation is used.  Large model menus can contain many actions, and
animating their geometry would force repeated native-menu relayout.  Position +
window opacity gives the visual connection without the jank.
"""

from __future__ import annotations

from typing import Any, Callable

from PySide6.QtCore import (
    QEasingCurve,
    QEvent,
    QObject,
    QPoint,
    QParallelAnimationGroup,
    QPropertyAnimation,
    QTimer,
)
from PySide6.QtWidgets import QApplication, QMenu, QPushButton

from app.desktop import theme
from app.desktop.composer import ComposerPanel
from app.desktop.widgets import repolish


_INSTALLED = False
_POPUP_TRAVEL = 7
_POSITION_MS = 145
_OPACITY_MS = 110

_CONTROL_OPEN_QSS = """
QPushButton#composerControl[popupOpen="true"] {
    background:#171b24;
    border-color:#3a4251;
    color:#eef1f6;
}
QPushButton#composerControl[popupRole="permission"][mode="full-access"][popupOpen="true"] {
    background:#21180a;
    border-color:#75571d;
    color:#ffd078;
}
QPushButton#composerControl[popupRole="permission"][mode="workspace"][popupOpen="true"] {
    background:#1c1a2b;
    border-color:#47415f;
    color:#cec8ff;
}
QPushButton#composerControl[popupRole="permission"][mode="read-only"][popupOpen="true"] {
    background:#121c25;
    border-color:#36506a;
    color:#a9d1ef;
}
QPushButton#composerControl[popupRole="model"][popupOpen="true"] {
    background:#181824;
    border-color:#403b5b;
    color:#d6d1ff;
}
"""


def _set_open(button: QPushButton | None, opened: bool) -> None:
    if button is None:
        return
    if bool(button.property("popupOpen")) == bool(opened):
        return
    button.setProperty("popupOpen", bool(opened))
    repolish(button)


def _menu_parent_panel(menu: QMenu) -> ComposerPanel | None:
    parent = menu.parentWidget()
    while parent is not None:
        if isinstance(parent, ComposerPanel):
            return parent
        parent = parent.parentWidget()
    return None


def _slide_direction(menu: QMenu, button: QPushButton, final_pos: QPoint) -> int:
    """Return +1 when an above-menu should start lower, -1 for below-menu."""
    button_mid = button.mapToGlobal(QPoint(0, button.height() // 2)).y()
    menu_mid = final_pos.y() + max(1, menu.height()) // 2
    return 1 if menu_mid < button_mid else -1


class _ComposerMenuEventFilter(QObject):
    """Animate only menus opened by the two composer picker controls."""

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802 - Qt override
        if not isinstance(watched, QMenu):
            return False

        panel = _menu_parent_panel(watched)
        if panel is None:
            return False

        if event.type() == QEvent.Type.Show:
            button = getattr(panel, "_loom_popup_anchor", None)
            if not isinstance(button, QPushButton):
                return False
            watched._loom_popup_anchor = button
            _set_open(button, True)

            # Hide the native snap before the first paint, then start after Qt has
            # resolved the menu's final bounded geometry.
            if theme.motion_enabled():
                try:
                    watched.setWindowOpacity(0.0)
                except (RuntimeError, TypeError):
                    pass
                QTimer.singleShot(0, lambda menu=watched, anchor=button: self._animate_open(menu, anchor))
            return False

        if event.type() in {QEvent.Type.Hide, QEvent.Type.Close}:
            button = getattr(watched, "_loom_popup_anchor", None)
            animation = getattr(watched, "_loom_popup_animation", None)
            if animation is not None:
                try:
                    animation.stop()
                except RuntimeError:
                    pass
                watched._loom_popup_animation = None
            try:
                watched.setWindowOpacity(1.0)
            except (RuntimeError, TypeError):
                pass
            if isinstance(button, QPushButton):
                _set_open(button, False)
            return False

        return False

    def _animate_open(self, menu: QMenu, button: QPushButton) -> None:
        try:
            if not menu.isVisible():
                return
            final_pos = menu.pos()
            direction = _slide_direction(menu, button, final_pos)
            start_pos = QPoint(final_pos.x(), final_pos.y() + direction * _POPUP_TRAVEL)
            menu.move(start_pos)
            menu.setWindowOpacity(0.0)

            position = QPropertyAnimation(menu, b"pos", menu)
            position.setDuration(_POSITION_MS)
            position.setStartValue(start_pos)
            position.setEndValue(final_pos)
            position.setEasingCurve(QEasingCurve.Type.OutCubic)

            opacity = QPropertyAnimation(menu, b"windowOpacity", menu)
            opacity.setDuration(_OPACITY_MS)
            opacity.setStartValue(0.0)
            opacity.setEndValue(1.0)
            opacity.setEasingCurve(QEasingCurve.Type.OutCubic)

            group = QParallelAnimationGroup(menu)
            group.addAnimation(position)
            group.addAnimation(opacity)

            def finish() -> None:
                try:
                    menu.move(final_pos)
                    menu.setWindowOpacity(1.0)
                except RuntimeError:
                    pass
                menu._loom_popup_animation = None
                group.deleteLater()

            group.finished.connect(finish)
            menu._loom_popup_animation = group
            group.start()
        except RuntimeError:
            return


_FILTER: _ComposerMenuEventFilter | None = None


def _ensure_filter() -> None:
    global _FILTER
    app = QApplication.instance()
    if app is None or _FILTER is not None:
        return
    _FILTER = _ComposerMenuEventFilter(app)
    app.installEventFilter(_FILTER)


def _wrap_opener(
    original: Callable[..., Any],
    button_name: str,
) -> Callable[..., Any]:
    def wrapped(self: ComposerPanel, *args: Any, **kwargs: Any) -> Any:
        _ensure_filter()
        button = getattr(self, button_name, None)
        self._loom_popup_anchor = button
        _set_open(button if isinstance(button, QPushButton) else None, True)
        try:
            return original(self, *args, **kwargs)
        finally:
            _set_open(button if isinstance(button, QPushButton) else None, False)
            if getattr(self, "_loom_popup_anchor", None) is button:
                self._loom_popup_anchor = None

    return wrapped


def install() -> None:
    """Install anchored opening motion after the picker presentation hooks."""
    global _INSTALLED
    if _INSTALLED:
        return
    _INSTALLED = True

    original_init = ComposerPanel.__init__
    original_permission = ComposerPanel._open_permission_menu
    original_model = ComposerPanel._open_model_menu

    def init(self: ComposerPanel, *args: Any, **kwargs: Any) -> None:
        original_init(self, *args, **kwargs)
        _ensure_filter()
        self.permission_button.setProperty("popupRole", "permission")
        self.model_button.setProperty("popupRole", "model")
        for button in (self.permission_button, self.model_button):
            button.setProperty("popupOpen", False)
            existing = button.styleSheet()
            if _CONTROL_OPEN_QSS not in existing:
                button.setStyleSheet(existing + "\n" + _CONTROL_OPEN_QSS)
            repolish(button)

    ComposerPanel.__init__ = init
    ComposerPanel._open_permission_menu = _wrap_opener(original_permission, "permission_button")
    ComposerPanel._open_model_menu = _wrap_opener(original_model, "model_button")


__all__ = ["install"]
