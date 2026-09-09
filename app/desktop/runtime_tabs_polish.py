"""Compact, product-grade navigation for the Runtime inspector tabs.

The Runtime panel is intentionally narrow. The default QTabBar overflow buttons
were visually heavy and made a five-tab inspector look like a horizontally
scrolled document. This pass keeps every destination visible at once, uses a
quiet segmented rail, and lets the selected tab carry the accent through both
its surface and vector icon.
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
    margin-top:8px;
}}
QTabWidget#activityTabs::tab-bar {{
    alignment:center;
}}
QTabWidget#activityTabs QTabBar {{
    background:#0c0f15;
    border:1px solid #1e2430;
    border-radius:10px;
    padding:3px;
}}
QTabWidget#activityTabs QTabBar::tab {{
    min-width:0;
    min-height:27px;
    margin:0 1px;
    padding:4px 4px;
    background:transparent;
    border:1px solid transparent;
    border-radius:7px;
    color:#8f97a7;
    font-family:{theme.FONT_UI};
    font-size:10px;
    font-weight:620;
}}
QTabWidget#activityTabs QTabBar::tab:hover:!selected {{
    background:#151923;
    border-color:#202633;
    color:#c9ced8;
}}
QTabWidget#activityTabs QTabBar::tab:selected {{
    background:#1d1a2d;
    border-color:#3b3559;
    color:#f0edff;
    font-weight:700;
}}
QTabWidget#activityTabs QTabBar::tab:pressed {{
    background:#171522;
}}
QTabWidget#activityTabs QToolButton {{
    min-width:24px;
    max-width:24px;
    min-height:24px;
    max-height:24px;
    margin:3px;
    padding:0;
    background:transparent;
    border:1px solid transparent;
    border-radius:7px;
    color:#8f97a7;
}}
QTabWidget#activityTabs QToolButton:hover {{
    background:#171b24;
    border-color:#262d39;
    color:#e1e5ed;
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
