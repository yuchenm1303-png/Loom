"""Loom's native desktop client.

Public surface for ``loom_desktop`` and the desktop tests. Importing this
package pulls in PySide6; the pure-logic modules (``format``, ``markdown``,
``state``) can be imported directly without a GUI toolkit.
"""

from __future__ import annotations

from app.desktop.rpc import DesktopEventBridge, RpcRunner
from app.desktop.state import ThreadState, TranscriptEntry
from app.desktop.composer import ComposerPanel, ComposerTextEdit
from app.desktop import widgets as _widgets
from app.desktop.message_presentation import MessageWidget, TranscriptView
from app.desktop.thread_presentation import ThreadListItemWidget, thread_row_size

# Install presentation subclasses before ``window`` imports the corresponding
# widget types.  Durable transcript/thread behavior stays in ``widgets`` while
# the public desktop client gets compact chat and conversation-list chrome.
_widgets.MessageWidget = MessageWidget
_widgets.TranscriptView = TranscriptView
_widgets.ThreadListItemWidget = ThreadListItemWidget
_widgets.thread_row_size = thread_row_size

from app.desktop.widgets import (  # noqa: E402 - presentation is installed first
    ActivityCard,
    ApprovalCard,
    Banner,
    CodeBlock,
    EmptyState,
)
from app.desktop.window import LoomDesktopWindow  # noqa: E402

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
