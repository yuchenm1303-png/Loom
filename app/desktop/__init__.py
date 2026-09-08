"""Loom's native desktop client.

Public surface for ``loom_desktop`` and the desktop tests. Importing this
package pulls in PySide6; the pure-logic modules (``format``, ``markdown``,
``state``) can be imported directly without a GUI toolkit.
"""

from __future__ import annotations

from app.desktop.rpc import DesktopEventBridge, RpcRunner
from app.desktop.state import ThreadState, TranscriptEntry
from app.desktop.composer import ComposerPanel, ComposerTextEdit
from app.desktop import iconography as _iconography
from app.desktop import sidebar_motion as _sidebar_motion
from app.desktop import widgets as _widgets
from app.desktop.message_presentation import MessageWidget, TranscriptView
from app.desktop.thread_presentation import ThreadListItemWidget, thread_row_size

# Install presentation, icon, and motion hooks before ``window`` imports the
# corresponding widget types and native icon renderer.
_widgets.MessageWidget = MessageWidget
_widgets.TranscriptView = TranscriptView
_widgets.ThreadListItemWidget = ThreadListItemWidget
_widgets.thread_row_size = thread_row_size
_iconography.install()
_sidebar_motion.install_widgets()

from app.desktop.widgets import (  # noqa: E402 - presentation is installed first
    ActivityCard,
    ApprovalCard,
    Banner,
    CodeBlock,
    EmptyState,
)
from app.desktop.window import LoomDesktopWindow  # noqa: E402

_sidebar_motion.install_window(LoomDesktopWindow)

__all__ = [
    "ActivityCard",
    "ApprovalCard",
    "Banner",
    "CodeBlock",
    "ComposerPanel",
    "ComposerTextEdit",
    "DesktopEventBridge",
    "EmptyState",
    "LoomDesktopWindow",
    "MessageWidget",
    "RpcRunner",
    "ThreadListItemWidget",
    "ThreadState",
    "TranscriptEntry",
    "TranscriptView",
]
