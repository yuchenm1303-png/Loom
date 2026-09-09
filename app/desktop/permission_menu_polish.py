"""Polish and bound the composer permission menu.

The permission picker sits next to the bottom composer controls, so letting Qt
place it from a raw local coordinate can push the popup below the Loom window
(and even over the taskbar on short screens).  This presentation hook keeps the
menu compact, positions it with the same bounded helper as the model picker, and
preserves the runtime-backed permission semantics.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtWidgets import QMenu

from app.desktop.composer import (
    MODEL_MENU_EDGE_GAP,
    PERMISSION_DETAIL,
    ComposerPanel,
    _bounded_menu_position,
    _menu_caption,
)


_INSTALLED = False
PERMISSION_MENU_WIDTH = 270
PERMISSION_MENU_MIN_WIDTH = 188

_PERMISSION_MENU_QSS = """
QMenu#permissionMenu {
    background:#101319;
    border:1px solid #2d3440;
    border-radius:10px;
    padding:5px;
}
QMenu#permissionMenu::item {
    min-width:0px;
    padding:6px 14px 6px 11px;
    margin:1px 0px;
    border-radius:6px;
    color:#d5d9e1;
}
QMenu#permissionMenu::item:selected {
    background:#1b2029;
    color:#ffffff;
}
QMenu#permissionMenu::item:checked {
    background:#191725;
    color:#ddd8ff;
}
QMenu#permissionMenu::separator {
    height:1px;
    background:#252b36;
    margin:5px 7px;
}
QMenu#permissionMenu QLabel#menuCaption {
    color:#8b93a3;
    font-size:10px;
    background:transparent;
}
"""


def _menu_width(panel: ComposerPanel) -> int:
    """Choose a readable width that can still fit inside a narrow Loom window."""
    available = max(1, panel.window().width() - MODEL_MENU_EDGE_GAP * 2)
    return max(PERMISSION_MENU_MIN_WIDTH, min(PERMISSION_MENU_WIDTH, available))


def _open_permission_menu(self: ComposerPanel) -> None:
    width = _menu_width(self)
    caption_width = max(150, width - 28)

    menu = QMenu(self)
    menu.setObjectName("permissionMenu")
    menu.setToolTipsVisible(True)
    menu.setStyleSheet(_PERMISSION_MENU_QSS)
    menu.addAction(
        _menu_caption(
            "What Loom may do without asking",
            menu,
            max_width=caption_width,
            compact=True,
        )
    )

    current = self._thread_permission or self._pending_permission
    actions: dict[Any, str] = {}
    for mode in self._permission_modes:
        action = menu.addAction(mode)
        action.setCheckable(True)
        action.setChecked(mode == current)
        action.setToolTip(PERMISSION_DETAIL.get(mode, ""))
        actions[action] = mode

    if self._thread_permission:
        menu.addSeparator()
        menu.addAction(
            _menu_caption(
                "This conversation keeps its current mode. Changes apply to new conversations.",
                menu,
                max_width=caption_width,
                compact=True,
            )
        )

    # Composer controls live at the bottom of the window. Prefer opening upward,
    # and clamp both axes to Loom's own window instead of relying on native menu
    # placement, which can extend below the app on short screens.
    chosen = menu.exec(_bounded_menu_position(menu, self.permission_button, width=width))
    mode = actions.get(chosen)
    if mode and mode != current:
        self.permissionChosen.emit(mode)


def install() -> None:
    """Install the bounded permission picker once."""
    global _INSTALLED
    if _INSTALLED:
        return
    _INSTALLED = True
    ComposerPanel._open_permission_menu = _open_permission_menu


__all__ = ["PERMISSION_MENU_WIDTH", "install"]
