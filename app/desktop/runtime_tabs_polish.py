"""Compact, restrained navigation for the Runtime inspector tabs.

The Runtime panel is intentionally narrow. All destinations stay visible at
once, but the navigation now behaves like a lightweight tool rail rather than a
segmented control nested inside another card. The selected destination is
carried by typography, its vector icon and one thin underline.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QSize, Qt

from app.desktop import theme
from app.desktop.widgets import vector_icon


_TAB_ICONS = ("model", "terminal", "diff", "browser", "agents")

_RUNTIME_TABS_QSS = f"""
QTabWidget#activityTabs {{
    background:transparent;
    border:none;
}}
QTabWidget#activityTabs::pane {{
    background:transparent;
    border:none;
    border-top:1px solid #1d1f26;
    margin-top:5px;
}}
QTabWidget#activityTabs::tab-bar {{
    alignment:center;
}}
QTabWidget#activityTabs QTabBar {{
    background:transparent;
    border:none;
    border-radius:0;
    padding:0;
}}
QTabWidget#activityTabs QTabBar::tab {{
    min-width:0;
    min-height:28px;
    margin:0 2px;
    padding:5px 5px 6px;
    background:transparent;
    border:none;
    border-bottom:1px solid transparent;
    border-radius:0;
    color:#858c98;
    font-family:{theme.FONT_UI};
    font-size:10px;
    font-weight:580;
}}
QTabWidget#activityTabs QTabBar::tab:hover:!selected {{
    background:transparent;
    color:#c2c6ce;
}}
QTabWidget#activityTabs QTabBar::tab:selected {{
    background:transparent;
    border-bottom:1px solid #7771d5;
    color:#e4e5ea;
    font-weight:650;
}}
QTabWidget#activityTabs QTabBar::tab:pressed {{
    background:#17181e;
}}
QTabWidget#activityTabs QToolButton {{
    min-width:24px;
    max-width:24px;
    min-height:24px;
    max-height:24px;
    margin:2px;
    padding:0;
    background:transparent;
    border:1px solid transparent;
    border-radius:6px;
    color:#858c98;
}}
QTabWidget#activityTabs QToolButton:hover {{
    background:#1a1c22;
    border-color:transparent;
    color:#d5d8de;
}}
"""


def _sync_icons(tabs: Any, selected: int) -> None:
    """Use the same vector geometry, with accent only on the active destination."""
    count = min(tabs.count(), len(_TAB_ICONS))
    for index in range(count):
        tabs.setTabIcon(
            index,
            vector_icon(
                _TAB_ICONS[index],
                size=14,
                tone="accent" if index == selected else "muted",
            ),
        )


def polish_runtime_tabs(tabs: Any) -> None:
    """Apply the compact Runtime tab rail to an already-built QTabWidget."""
    tabs.setDocumentMode(True)
    # Five compact destinations fit inside the Runtime panel's 330 px minimum,
    # so native left/right overflow boxes are unnecessary chrome.
    tabs.setUsesScrollButtons(False)
    tabs.setStyleSheet(_RUNTIME_TABS_QSS)

    bar = tabs.tabBar()
    bar.setUsesScrollButtons(False)
    bar.setDrawBase(False)
    bar.setExpanding(False)
    bar.setElideMode(Qt.TextElideMode.ElideNone)
    bar.setIconSize(QSize(14, 14))
    bar.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    bar.setCursor(Qt.CursorShape.PointingHandCursor)

    _sync_icons(tabs, tabs.currentIndex())
    tabs.currentChanged.connect(lambda index: _sync_icons(tabs, index))


def install_window(window_cls: type[Any]) -> None:
    """Polish Runtime navigation immediately after the window builds the panel."""
    if getattr(window_cls, "_loom_runtime_tabs_polish_installed", False):
        return
    window_cls._loom_runtime_tabs_polish_installed = True

    original = window_cls._build_runtime_panel

    def build_runtime_panel(self: Any) -> None:
        original(self)
        polish_runtime_tabs(self.activity_tabs)

    window_cls._build_runtime_panel = build_runtime_panel


__all__ = ["install_window", "polish_runtime_tabs"]
