"""Loom's native desktop client.

Public surface for ``loom_desktop`` and the desktop tests. Importing this
package pulls in PySide6; the pure-logic modules (``format``, ``markdown``,
``state``) can be imported directly without a GUI toolkit.
"""

from __future__ import annotations

from app.desktop.rpc import DesktopEventBridge, RpcRunner
from app.desktop.state import ThreadState, TranscriptEntry
from app.desktop.composer import ComposerPanel, ComposerTextEdit
from app.desktop import composer_polish as _composer_polish
from app.desktop import permission_menu_polish as _permission_menu_polish
from app.desktop import composer_menu_motion as _composer_menu_motion
from app.desktop import iconography as _iconography
from app.desktop import sidebar_motion as _sidebar_motion
from app.desktop import sidebar_motion_smooth as _sidebar_motion_smooth
from app.desktop import turn_progress as _turn_progress
from app.desktop import widgets as _widgets
from app.desktop import widget_lifecycle as _widget_lifecycle

# Install the disposal guard before presentation subclasses are imported. This
# prevents QWidget.setParent(None) disposal paths from becoming transient native
# top-level windows while a streamed transcript is being reconciled.
_widget_lifecycle.install()

from app.desktop.output_presentation import MessageWidget, TranscriptView
from app.desktop import message_actions as _message_actions
from app.desktop import message_actions_polish as _message_actions_polish
from app.desktop import user_bubble_alignment as _user_bubble_alignment
from app.desktop import transcript_density as _transcript_density
from app.desktop import activity_hierarchy as _activity_hierarchy
from app.desktop import activity_motion as _activity_motion
from app.desktop import activity_disclosure as _activity_disclosure
from app.desktop import reasoning_polish as _reasoning_polish
from app.desktop import runtime_feedback as _runtime_feedback
from app.desktop import activity_compact_panel as _activity_compact_panel
from app.desktop import disclosure_motion as _disclosure_motion
from app.desktop import agent_working_indicator as _agent_working_indicator
from app.desktop.thread_presentation import ThreadListItemWidget, thread_row_size

# Install the task presentation pipeline in order. The low-reflow disclosure
# policy is deliberately last: earlier passes own visual styling and chevrons,
# while the final pass owns geometry/scroll behavior so no later polish can
# reintroduce per-frame transcript relayout. The working indicator wraps the
# settled transcript last so it always remains the visible tail row.
_transcript_density.install()
_activity_hierarchy.install()
_activity_motion.install()
_activity_disclosure.install()
_reasoning_polish.install()
_runtime_feedback.install()
_activity_compact_panel.install()
_message_actions.install()
_message_actions_polish.install()
_user_bubble_alignment.install()
_disclosure_motion.install()
_agent_working_indicator.install_widgets()
_widgets.MessageWidget = MessageWidget
_widgets.TranscriptView = TranscriptView
_widgets.ThreadListItemWidget = ThreadListItemWidget
_widgets.thread_row_size = thread_row_size
_iconography.install()
_sidebar_motion.install_widgets()
_sidebar_motion_smooth.install()
_composer_polish.install()
_permission_menu_polish.install()
_composer_menu_motion.install()

from app.desktop.widgets import (  # noqa: E402 - presentation is installed first
    ActivityCard,
    ApprovalCard,
    Banner,
    CodeBlock,
    EmptyState,
)
from app.desktop.window import LoomDesktopWindow  # noqa: E402

_sidebar_motion.install_window(LoomDesktopWindow)
_turn_progress.install_window(LoomDesktopWindow)
_agent_working_indicator.install_window(LoomDesktopWindow)

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
