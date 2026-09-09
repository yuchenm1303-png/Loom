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
from app.desktop import model_selector_polish as _model_selector_polish
from app.desktop import permission_menu_polish as _permission_menu_polish
from app.desktop import composer_menu_motion as _composer_menu_motion
from app.desktop import iconography as _iconography
from app.desktop import tool_icon_polish as _tool_icon_polish
from app.desktop import sidebar_motion as _sidebar_motion
from app.desktop import sidebar_motion_smooth as _sidebar_motion_smooth
from app.desktop import sidebar_group_polish as _sidebar_group_polish
from app.desktop import turn_progress as _turn_progress
from app.desktop import widgets as _widgets
from app.desktop import widget_lifecycle as _widget_lifecycle

# Install the disposal guard before presentation subclasses are imported. This
# prevents QWidget.setParent(None) disposal paths from becoming transient native
# top-level windows while a streamed transcript is being reconciled.
_widget_lifecycle.install()

# Presentation layers may refine density, hierarchy and runtime state, but they
# no longer own disclosure animation. Thought process and task details are
# constructed later from one native AnimatedReveal component.
from app.desktop import output_presentation as _output_presentation
from app.desktop import message_actions as _message_actions
from app.desktop import message_actions_polish as _message_actions_polish
from app.desktop import message_actions_turn_boundary as _message_actions_turn_boundary
from app.desktop import user_bubble_alignment as _user_bubble_alignment
from app.desktop import user_message_actions as _user_message_actions
from app.desktop import transcript_density as _transcript_density
from app.desktop import activity_hierarchy as _activity_hierarchy
from app.desktop import runtime_feedback as _runtime_feedback
from app.desktop import activity_compact_panel as _activity_compact_panel
from app.desktop import agent_working_indicator as _agent_working_indicator
from app.desktop import transcript_flow as _transcript_flow
from app.desktop import stream_render_pipeline as _stream_render_pipeline
from app.desktop.thread_presentation import ThreadListItemWidget, thread_row_size

_transcript_density.install()
_activity_hierarchy.install()
_runtime_feedback.install()
_activity_compact_panel.install()
_message_actions.install()
_message_actions_polish.install()
_user_bubble_alignment.install()
_agent_working_indicator.install_widgets()
_message_actions_turn_boundary.install_view()
# The keyed reconciler may change canonical order after a live snapshot refresh,
# so physically move existing Qt widgets to that order. Content-only stream
# frames bypass this structural lane entirely.
_transcript_flow.install()
_stream_render_pipeline.install_view()
# User controls are installed after the final transcript reconciler so each user
# entry remains one canonical keyed shell: bubble first, actions directly below.
_user_message_actions.install()

# Canonical transcript widgets are defined only after presentation/runtime
# refinements above are installed. Their disclosure behavior is class-owned,
# rather than monkey-patched by a chain of animation passes.
from app.desktop.transcript_disclosure import FlowMessageWidget as MessageWidget  # noqa: E402
from app.desktop.transcript_viewport import AnchoredTranscriptView as TranscriptView  # noqa: E402

_widgets.MessageWidget = MessageWidget
_widgets.TranscriptView = TranscriptView
_widgets.ThreadListItemWidget = ThreadListItemWidget
_widgets.thread_row_size = thread_row_size
_iconography.install()
_tool_icon_polish.install()
_sidebar_motion.install_widgets()
_sidebar_motion_smooth.install()
_sidebar_group_polish.install()
_composer_polish.install()
_model_selector_polish.install()
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
_message_actions_turn_boundary.install_window(LoomDesktopWindow)
# Install last so high-frequency item/delta traffic enters the bounded streaming
# lane after all structural window wrappers have established their behavior.
_stream_render_pipeline.install_window(LoomDesktopWindow)

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
