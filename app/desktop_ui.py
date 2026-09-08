"""Stable import path for the desktop client.

The implementation lives in :mod:`app.desktop`. This module stays so
``loom_desktop`` and existing callers keep a single, version-free entry point.
"""

from __future__ import annotations

from app.desktop import (
    ComposerPanel,
    ComposerTextEdit,
    DesktopEventBridge,
    LoomDesktopWindow,
    ThreadListItemWidget,
)

__all__ = [
    "ComposerPanel",
    "ComposerTextEdit",
    "DesktopEventBridge",
    "LoomDesktopWindow",
    "ThreadListItemWidget",
]
