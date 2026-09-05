from __future__ import annotations

import html
import json
import threading
from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import QObject, QSize, Qt, Signal
from PySide6.QtGui import QKeyEvent, QTextCursor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QTabWidget,
    QTextBrowser,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)


_THREAD_ROLE = Qt.ItemDataRole.UserRole
_TERMINAL_STATUSES = {"completed", "failed", "cancelled", "interrupted", "limit_reached"}


def _pretty(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)


def _text(value: Any) -> str:
    return str(value or "")


def _short_path(value: Any) -> str:
    raw = _text(value).strip()
    if not raw:
        return "No workspace"
    path = Path(raw)
    return path.name or raw


def _human_status(value: Any) -> str:
    status = _text(value).strip() or "idle"
    return status.replace("_", " ").title()


def _short_time(value: Any) -> str:
    raw = _text(value).strip()
    if "T" in raw:
        clock = raw.split("T", 1)[1]
        return clock[:8]
    return raw[-8:] if len(raw) >= 8 else raw


def _format_tokens(value: Any) -> str:
    try:
        tokens = int(value or 0)
    except (TypeError, ValueError):
        tokens = 0
    if tokens >= 1_000_000:
        return f"{tokens / 1_000_000:.1f}m"
    if tokens >= 1_000:
        compact = f"{tokens / 1_000:.1f}".rstrip("0").rstrip(".")
        return f"{compact}k"
    return str(tokens)


def _event_summary(event: dict[str, Any]) -> str:
    kind = _text(event.get("kind")) or "event"
    data = event.get("data") or {}
    if not isinstance(data, dict):
        data = {}

    if kind == "session_created":
        return "Session started"
    if kind == "user_message":
        return "Prompt received"
    if kind == "turn_started":
        return "Turn started"
    if kind == "turn_completed":
        return "Turn completed"
    if kind == "turn_failed":
        return "Turn failed"
    if kind == "model_requested":
        return f"Asked model · step {data.get('step', '?')}"
    if kind == "model_response":
        usage = data.get("usage") or {}
        total = usage.get("total_tokens") if isinstance(usage, dict) else None
        return f"Model replied{f' · {_format_tokens(total)} tokens' if total else ''}"
    if kind == "turn_diff_updated":
        paths = data.get("paths") or []
        return f"Changed {len(paths)} file{'s' if len(paths) != 1 else ''}"
    if kind == "tool_approval_required":
        tool = _text(data.get("tool")) or "tool"
        return f"Approval needed · {tool}"
    if kind.startswith("tool_"):
        tool = _text(data.get("tool")) or "tool"
        if kind == "tool_started":
            return f"Running {tool}"
        if kind == "tool_completed":
            return f"Finished {tool}"
        if kind == "tool_failed":
            return f"Tool failed · {tool}"
        return f"{kind.replace('_', ' ')} · {tool}"
    if kind.startswith("process_"):
        process_id = _text(data.get("process_id"))[:10]
        if kind == "process_started":
            return f"Process started{f' · {process_id}' if process_id else ''}"
        if kind == "process_exited":
            return f"Process finished{f' · {process_id}' if process_id else ''}"
        return f"{kind.replace('_', ' ')}{f' · {process_id}' if process_id else ''}"
    return kind.replace("_", " ").capitalize()


def _event_marker(kind: Any) -> str:
    value = _text(kind)
    if value in {"turn_completed", "tool_completed", "process_exited"}:
        return "✓"
    if value in {"turn_failed", "tool_failed"}:
        return "!"
    if value.startswith("model_"):
        return "◆"
    if value.startswith("tool_"):
        return "◇"
    if value.startswith("process_"):
        return "$"
    if value == "turn_diff_updated":
        return "Δ"
    if value == "turn_started":
        return "→"
    return "•"


def _notification_summary(method: str, params: dict[str, Any]) -> str:
    if method == "item/delta":
        delta = params.get("delta") or {}
        if "text" in delta:
            return "Loom is responding"
        if "stdout" in delta or "stderr" in delta:
            return "Process output"
        if delta.get("kind") == "tool_call_argument":
            return f"Preparing {_text(delta.get('toolName')) or 'tool'}"
        if delta.get("status"):
            return f"Item · {delta.get('status')}"
    if method == "approval/requested":
        approval = params.get("approval") or {}
        return f"Approval needed · {_text(approval.get('toolName')) or 'tool'}"
    if method == "turn/completed":
        turn = params.get("turn") or {}
        return f"Turn {_text(turn.get('status')) or 'completed'}"
    if method == "turn/started":
        return "Turn started"
    return method.replace("/", " · ")


def _repolish(widget: QWidget) -> None:
    style = widget.style()
    style.unpolish(widget)
    style.polish(widget)
    widget.update()


class DesktopEventBridge(QObject):
    """Marshal App Server callbacks and background RPC results onto Qt's UI thread."""

    notification = Signal(str, object)
    stderr = Signal(str)
    serverExited = Signal(str)
    rpcResult = Signal(str, object)
    rpcError = Signal(str, str)


class ComposerTextEdit(QTextEdit):
    sendRequested = Signal()

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802 - Qt override
        if (
            event.key() in {Qt.Key.Key_Return, Qt.Key.Key_Enter}
            and not (event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
        ):
            self.sendRequested.emit()
            event.accept()
            return
        super().keyPressEvent(event)


class ThreadListItemWidget(QWidget):
    """Compact durable-thread row with project context only when it adds information."""

    def __init__(
        self,
        record: dict[str, Any],
        parent: QWidget | None = None,
        *,
        active_workspace: str | Path | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("threadItemWidget")
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(11, 8, 11, 8)
        layout.setSpacing(5)

        title = QLabel(_text(record.get("title")).strip() or "New thread")
        title.setObjectName("threadItemTitle")
        title.setTextFormat(Qt.TextFormat.PlainText)
        title.setToolTip(_text(record.get("title")).strip())
        layout.addWidget(title)

        meta = QHBoxLayout()
        meta.setContentsMargins(0, 0, 0, 0)
        meta.setSpacing(7)

        record_workspace = _text(record.get("workspace"))
        show_workspace = True
        if active_workspace and record_workspace:
            try:
                show_workspace = Path(record_workspace).resolve() != Path(active_workspace).resolve()
            except OSError:
                show_workspace = record_workspace != str(active_workspace)
        if show_workspace and record_workspace:
            workspace = QLabel(_short_path(record_workspace))
            workspace.setObjectName("threadItemMeta")
            meta.addWidget(workspace)

        usage = record.get("usage") or {}
        token_total = usage.get("totalTokens") if isinstance(usage, dict) else None
        if token_total:
            token_label = QLabel(f"{_format_tokens(token_total)} tok")
            token_label.setObjectName("threadItemMeta")
            meta.addWidget(token_label)

        meta.addStretch(1)
        status_value = _text(record.get("status")) or "idle"
        status = QLabel(_human_status(status_value))
        status.setObjectName("threadStatus")
        status.setProperty("state", status_value)
        meta.addWidget(status)
        layout.addLayout(meta)


class LoomDesktopWindow(QMainWindow):
    """Native Loom client over the stable App Server protocol."""

    def __init__(
        self,
        *,
        client: Any,
        initialization: dict[str, Any],
        default_workspace: str | Path,
        default_permission_mode: str | None = None,
    ) -> None:
        super().__init__()
        self.client = client
        self.initialization = dict(initialization or {})
        self.default_workspace = Path(default_workspace).expanduser().resolve()
        runtime_info = self.initialization.get("runtime") or {}
        self.default_permission_mode = str(
            default_permission_mode
            or runtime_info.get("defaultPermissionMode")
            or "approval"
        )
        self.current_thread_id = ""
        self.current_turn_id = ""
        self.current_workspace = str(self.default_workspace)
        self.current_snapshot: dict[str, Any] = {}
        self._threads_by_id: dict[str, dict[str, Any]] = {}
        self._durable_messages: list[dict[str, Any]] = []
        self._live_assistant: dict[str, str] = {}
        self._optimistic_user: str | None = None
        self._pending_rpc: set[str] = set()
        self._startup_autocreate = True
        self._closed = False
        self._activity_tail: list[tuple[str, str, str]] = []
        self._sidebar_visible = True
        self._runtime_visible = True

        self.bridge = DesktopEventBridge(self)
        self.bridge.notification.connect(self._on_notification)
        self.bridge.stderr.connect(self._on_server_stderr)
        self.bridge.serverExited.connect(self._on_server_exit)
        self.bridge.rpcResult.connect(self._on_rpc_result)
        self.bridge.rpcError.connect(self._on_rpc_error)
        self._subscribe_client()

        self._build_ui()
        self._apply_style()
        self._apply_initialization()
        self.refresh_threads()

    def _subscribe_client(self) -> None:
        subscribe = getattr(self.client, "subscribe_notifications", None)
        if callable(subscribe):
            subscribe(lambda method, params: self.bridge.notification.emit(method, params))
        subscribe = getattr(self.client, "subscribe_stderr", None)
        if callable(subscribe):
            subscribe(self.bridge.stderr.emit)
        subscribe = getattr(self.client, "subscribe_exit", None)
        if callable(subscribe):
            subscribe(self.bridge.serverExited.emit)

    def _build_ui(self) -> None:
        self.setObjectName("loomDesktopWindow")
        self.setWindowTitle("Loom")
        self.resize(1600, 980)
        self.setMinimumSize(1080, 700)

        root = QWidget(self)
        root.setObjectName("appRoot")
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)
        self.setCentralWidget(root)

        self.main_splitter = QSplitter(Qt.Orientation.Horizontal, root)
        self.main_splitter.setObjectName("mainSplitter")
        self.main_splitter.setChildrenCollapsible(False)
        root_layout.addWidget(self.main_splitter)

        self._build_sidebar()
        self._build_conversation_panel()
        self._build_activity_panel()
        self.main_splitter.setSizes([282, 990, 328])
        self.main_splitter.setStretchFactor(0, 0)
        self.main_splitter.setStretchFactor(1, 1)
        self.main_splitter.setStretchFactor(2, 0)

    def _build_sidebar(self) -> None:
        self.sidebar_panel = QFrame()
        self.sidebar_panel.setObjectName("sidebar")
        self.sidebar_panel.setMinimumWidth(250)
        self.sidebar_panel.setMaximumWidth(340)
        layout = QVBoxLayout(self.sidebar_panel)
        layout.setContentsMargins(15, 17, 13, 14)
        layout.setSpacing(13)

        brand_row = QHBoxLayout()
        brand_row.setSpacing(10)
        mark = QLabel("L")
        mark.setObjectName("brandMark")
        mark.setAlignment(Qt.AlignmentFlag.AlignCenter)
        mark.setFixedSize(34, 34)
        brand_row.addWidget(mark)

        brand_text = QVBoxLayout()
        brand_text.setSpacing(0)
        self.brand_label = QLabel("Loom")
        self.brand_label.setObjectName("brandLabel")
        brand_text.addWidget(self.brand_label)
        subtitle = QLabel("Local coding agent")
        subtitle.setObjectName("brandSubtitle")
        brand_text.addWidget(subtitle)
        brand_row.addLayout(brand_text, 1)
        layout.addLayout(brand_row)

        actions = QHBoxLayout()
        actions.setSpacing(7)
        self.new_thread_button = QPushButton("+  New Thread")
        self.new_thread_button.setObjectName("newThreadButton")
        self.new_thread_button.setToolTip("Create a new thread in the current workspace")
        self.new_thread_button.clicked.connect(self.new_thread_in_current_workspace)
        actions.addWidget(self.new_thread_button, 1)
        self.open_project_button = QPushButton("Open")
        self.open_project_button.setObjectName("openProjectButton")
        self.open_project_button.setToolTip("Open another project or workspace")
        self.open_project_button.clicked.connect(self.choose_workspace)
        actions.addWidget(self.open_project_button)
        layout.addLayout(actions)

        thread_header = QHBoxLayout()
        thread_header.setContentsMargins(2, 3, 0, 0)
        self.thread_section_label = QLabel("THREADS")
        self.thread_section_label.setObjectName("sectionLabel")
        thread_header.addWidget(self.thread_section_label)
        thread_header.addStretch(1)
        self.refresh_button = QPushButton("↻")
        self.refresh_button.setObjectName("iconButton")
        self.refresh_button.setToolTip("Refresh durable threads")
        self.refresh_button.clicked.connect(self.refresh_threads)
        thread_header.addWidget(self.refresh_button)
        layout.addLayout(thread_header)

        self.thread_list = QListWidget()
        self.thread_list.setObjectName("threadList")
        self.thread_list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.thread_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.thread_list.setSpacing(2)
        self.thread_list.currentItemChanged.connect(self._thread_selection_changed)
        layout.addWidget(self.thread_list, 1)

        connection_row = QHBoxLayout()
        connection_row.setContentsMargins(5, 3, 5, 1)
        connection_row.setSpacing(7)
        self.connection_dot = QLabel("●")
        self.connection_dot.setObjectName("connectionDot")
        self.connection_dot.setProperty("state", "connected")
        connection_row.addWidget(self.connection_dot)
        self.protocol_label = QLabel("App Server · disconnected")
        self.protocol_label.setObjectName("protocolLabel")
        self.protocol_label.setWordWrap(True)
        connection_row.addWidget(self.protocol_label, 1)
        layout.addLayout(connection_row)

        self.main_splitter.addWidget(self.sidebar_panel)

    def _build_conversation_panel(self) -> None:
        panel = QFrame()
        panel.setObjectName("conversationPanel")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(26, 18, 26, 18)
        layout.setSpacing(12)

        header = QFrame()
        header.setObjectName("workspaceHeader")
        header_layout = QVBoxLayout(header)
        header_layout.setContentsMargins(1, 0, 1, 13)
        header_layout.setSpacing(6)

        first_line = QHBoxLayout()
        first_line.setSpacing(8)
        self.sidebar_toggle_button = QPushButton("Threads")
        self.sidebar_toggle_button.setObjectName("panelToggle")
        self.sidebar_toggle_button.setToolTip("Show or hide the thread sidebar")
        self.sidebar_toggle_button.clicked.connect(self.toggle_sidebar)
        first_line.addWidget(self.sidebar_toggle_button)
        self.thread_title_label = QLabel("New thread")
        self.thread_title_label.setObjectName("threadTitle")
        self.thread_title_label.setTextFormat(Qt.TextFormat.PlainText)
        first_line.addWidget(self.thread_title_label, 1)
        self.status_label = QLabel("Idle")
        self.status_label.setObjectName("statusChip")
        self.status_label.setProperty("state", "idle")
        first_line.addWidget(self.status_label)
        self.runtime_toggle_button = QPushButton("Runtime")
        self.runtime_toggle_button.setObjectName("panelToggle")
        self.runtime_toggle_button.setToolTip("Show or hide the Runtime inspector")
        self.runtime_toggle_button.clicked.connect(self.toggle_runtime)
        first_line.addWidget(self.runtime_toggle_button)
        header_layout.addLayout(first_line)

        project_line = QHBoxLayout()
        project_line.setSpacing(8)
        self.workspace_label = QLabel(_short_path(self.default_workspace))
        self.workspace_label.setObjectName("projectName")
        project_line.addWidget(self.workspace_label)
        self.workspace_path_label = QLabel(str(self.default_workspace))
        self.workspace_path_label.setObjectName("mutedLabel")
        self.workspace_path_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        project_line.addWidget(self.workspace_path_label, 1)
        header_layout.addLayout(project_line)
        layout.addWidget(header)

        self.empty_state = self._build_empty_state()
        layout.addWidget(self.empty_state, 1)

        self.transcript = QTextBrowser()
        self.transcript.setObjectName("transcript")
        self.transcript.setOpenExternalLinks(False)
        self.transcript.setFrameShape(QFrame.Shape.NoFrame)
        self.transcript.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.transcript.hide()
        layout.addWidget(self.transcript, 1)

        self.approval_frame = QFrame()
        self.approval_frame.setObjectName("approvalCard")
        approval_layout = QVBoxLayout(self.approval_frame)
        approval_layout.setContentsMargins(15, 13, 15, 13)
        approval_layout.setSpacing(9)
        approval_header = QHBoxLayout()
        approval_icon = QLabel("!")
        approval_icon.setObjectName("approvalIcon")
        approval_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        approval_icon.setFixedSize(24, 24)
        approval_header.addWidget(approval_icon)
        self.approval_title = QLabel("Approval required")
        self.approval_title.setObjectName("approvalTitle")
        approval_header.addWidget(self.approval_title, 1)
        approval_layout.addLayout(approval_header)

        self.approval_details = QLabel("")
        self.approval_details.setObjectName("approvalDetails")
        self.approval_details.setWordWrap(True)
        self.approval_details.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        approval_layout.addWidget(self.approval_details)
        approval_actions = QHBoxLayout()
        approval_actions.addStretch(1)
        self.deny_button = QPushButton("Deny")
        self.deny_button.setObjectName("denyButton")
        self.deny_button.clicked.connect(lambda: self.respond_approval(False))
        approval_actions.addWidget(self.deny_button)
        self.allow_button = QPushButton("Allow once")
        self.allow_button.setObjectName("allowButton")
        self.allow_button.clicked.connect(lambda: self.respond_approval(True))
        approval_actions.addWidget(self.allow_button)
        approval_layout.addLayout(approval_actions)
        self.approval_frame.hide()
        layout.addWidget(self.approval_frame)

        composer_frame = QFrame()
        composer_frame.setObjectName("composerFrame")
        composer_layout = QVBoxLayout(composer_frame)
        composer_layout.setContentsMargins(14, 12, 12, 11)
        composer_layout.setSpacing(7)

        self.composer = ComposerTextEdit()
        self.composer.setObjectName("composer")
        self.composer.setPlaceholderText("Ask Loom anything about this project…")
        self.composer.setMaximumHeight(126)
        self.composer.setMinimumHeight(68)
        self.composer.sendRequested.connect(self.send_prompt)
        composer_layout.addWidget(self.composer)

        compose_actions = QHBoxLayout()
        compose_actions.setSpacing(7)
        hint = QLabel("Enter to send  ·  Shift+Enter for newline")
        hint.setObjectName("composerHint")
        compose_actions.addWidget(hint)
        compose_actions.addStretch(1)

        self.permission_label = QLabel(self.default_permission_mode)
        self.permission_label.setObjectName("composerChip")
        compose_actions.addWidget(self.permission_label)
        self.usage_label = QLabel("0 tokens")
        self.usage_label.setObjectName("composerChip")
        compose_actions.addWidget(self.usage_label)

        self.composer_state_label = QLabel("Ready")
        self.composer_state_label.setObjectName("composerState")
        compose_actions.addWidget(self.composer_state_label)

        self.stop_button = QPushButton("Stop")
        self.stop_button.setObjectName("stopButton")
        self.stop_button.clicked.connect(self.interrupt_turn)
        self.stop_button.setEnabled(False)
        compose_actions.addWidget(self.stop_button)
        self.send_button = QPushButton("Send  ↵")
        self.send_button.setObjectName("sendButton")
        self.send_button.setMinimumWidth(88)
        self.send_button.clicked.connect(self.send_prompt)
        compose_actions.addWidget(self.send_button)
        composer_layout.addLayout(compose_actions)
        layout.addWidget(composer_frame)

        self.main_splitter.addWidget(panel)

    def _build_empty_state(self) -> QWidget:
        frame = QFrame()
        frame.setObjectName("emptyState")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(30, 20, 30, 24)
        layout.setSpacing(0)
        layout.addStretch(1)

        content = QFrame()
        content.setObjectName("emptyStateContent")
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(18, 18, 18, 18)
        content_layout.setSpacing(11)

        kicker = QLabel("LOOM")
        kicker.setObjectName("emptyKicker")
        content_layout.addWidget(kicker)
        title = QLabel("What should we work on?")
        title.setObjectName("emptyTitle")
        content_layout.addWidget(title)
        body = QLabel(
            "Loom can inspect this codebase, edit files, run commands, browse, debug, or coordinate a longer task."
        )
        body.setObjectName("emptyBody")
        body.setWordWrap(True)
        content_layout.addWidget(body)

        row_one = QHBoxLayout()
        row_one.setSpacing(8)
        row_two = QHBoxLayout()
        row_two.setSpacing(8)
        prompts = [
            ("Inspect this project", "Inspect this project and summarize its architecture. Do not modify files."),
            ("Find a bug", "Inspect the current project, identify one meaningful bug or risk, and explain the root cause before changing anything."),
            ("Run tests", "Run the relevant test suite, summarize failures, and do not modify code yet."),
            ("Explain the codebase", "Explain this codebase from the entry points down to the main runtime and tool layers."),
        ]
        for index, (label, prompt) in enumerate(prompts):
            button = QPushButton(label)
            button.setObjectName("promptSuggestion")
            button.clicked.connect(lambda _checked=False, value=prompt: self._fill_composer(value))
            (row_one if index < 2 else row_two).addWidget(button, 1)
        content_layout.addLayout(row_one)
        content_layout.addLayout(row_two)

        layout.addWidget(content, 0, Qt.AlignmentFlag.AlignHCenter)
        layout.addStretch(1)
        return frame

    def _fill_composer(self, text: str) -> None:
        self.composer.setPlainText(text)
        self.composer.setFocus()
        cursor = self.composer.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        self.composer.setTextCursor(cursor)

    def _build_activity_panel(self) -> None:
        self.activity_panel = QFrame()
        self.activity_panel.setObjectName("activityPanel")
        self.activity_panel.setMinimumWidth(300)
        self.activity_panel.setMaximumWidth(470)
        layout = QVBoxLayout(self.activity_panel)
        layout.setContentsMargins(15, 18, 15, 14)
        layout.setSpacing(10)

        title_row = QHBoxLayout()
        title_stack = QVBoxLayout()
        title_stack.setSpacing(0)
        title = QLabel("Runtime")
        title.setObjectName("inspectorTitle")
        title_stack.addWidget(title)
        subtitle = QLabel("Execution, changes, and delegated work")
        subtitle.setObjectName("mutedLabel")
        title_stack.addWidget(subtitle)
        title_row.addLayout(title_stack)
        title_row.addStretch(1)
        self.sandbox_label = QLabel("No process")
        self.sandbox_label.setObjectName("sandboxChip")
        self.sandbox_label.setProperty("state", "idle")
        title_row.addWidget(self.sandbox_label, 0, Qt.AlignmentFlag.AlignTop)
        layout.addLayout(title_row)

        self.activity_tabs = QTabWidget()
        self.activity_tabs.setObjectName("activityTabs")
        self.activity_tabs.setDocumentMode(True)
        self.activity_view = QTextBrowser()
        self.activity_view.setObjectName("activityView")
        self.activity_view.setOpenExternalLinks(False)
        self.activity_view.setFrameShape(QFrame.Shape.NoFrame)
        self.terminal_view = self._read_only_panel("terminalView")
        self.diff_view = self._read_only_panel("diffView")
        self.browser_view = self._read_only_panel("browserView")
        self.agents_view = self._read_only_panel("agentsView")
        self.activity_tabs.addTab(self.activity_view, "Activity")
        self.activity_tabs.addTab(self.terminal_view, "Terminal")
        self.activity_tabs.addTab(self.diff_view, "Diff")
        self.activity_tabs.addTab(self.browser_view, "Browser")
        self.activity_tabs.addTab(self.agents_view, "Agents")
        layout.addWidget(self.activity_tabs, 1)

        self.main_splitter.addWidget(self.activity_panel)

    @staticmethod
    def _read_only_panel(name: str) -> QPlainTextEdit:
        view = QPlainTextEdit()
        view.setObjectName(name)
        view.setReadOnly(True)
        view.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        return view

    def toggle_sidebar(self) -> None:
        self._sidebar_visible = not self._sidebar_visible
        self.sidebar_panel.setVisible(self._sidebar_visible)
        self.sidebar_toggle_button.setProperty("active", self._sidebar_visible)
        _repolish(self.sidebar_toggle_button)

    def toggle_runtime(self) -> None:
        self._runtime_visible = not self._runtime_visible
        self.activity_panel.setVisible(self._runtime_visible)
        self.runtime_toggle_button.setProperty("active", self._runtime_visible)
        _repolish(self.runtime_toggle_button)

    def _apply_style(self) -> None:
        self.setStyleSheet(
            """
            QMainWindow, QWidget {
                background: #08090d;
                color: #edf0f5;
                font-family: "Segoe UI Variable", "Segoe UI", sans-serif;
                font-size: 14px;
            }
            QFrame#sidebar, QFrame#activityPanel { background: #0a0c11; }
            QFrame#conversationPanel { background: #08090d; }

            QLabel#brandMark {
                background: #7467df;
                color: white;
                border-radius: 10px;
                font-size: 16px;
                font-weight: 800;
            }
            QLabel#brandLabel { color: #f7f8fb; font-size: 17px; font-weight: 720; }
            QLabel#brandSubtitle { color: #737a8c; font-size: 11px; }
            QLabel#sectionLabel { color: #747c8f; font-size: 10px; font-weight: 700; letter-spacing: 1.3px; }
            QLabel#mutedLabel { color: #747b8d; font-size: 11px; }
            QLabel#threadTitle { color: #f4f5f8; font-size: 19px; font-weight: 720; }
            QLabel#projectName { color: #a7adbb; font-size: 11px; font-weight: 650; }

            QFrame#workspaceHeader {
                background: transparent;
                border: none;
                border-bottom: 1px solid #181b24;
                border-radius: 0;
            }
            QLabel#statusChip, QLabel#sandboxChip {
                border-radius: 9px;
                padding: 4px 9px;
                font-size: 10px;
                font-weight: 650;
            }
            QLabel#statusChip { background: #11141b; color: #9299aa; }
            QLabel#statusChip[state="running"], QLabel#statusChip[state="starting"] { background: #121a25; color: #9ac8ff; }
            QLabel#statusChip[state="completed"] { background: #101a17; color: #8bd1b6; }
            QLabel#statusChip[state="waiting_approval"] { background: #211a10; color: #edc177; }
            QLabel#statusChip[state="failed"], QLabel#statusChip[state="cancelled"] { background: #221416; color: #efa0a7; }
            QLabel#sandboxChip { background: #10131a; color: #858c9d; }
            QLabel#sandboxChip[state="enforced"] { background: #101a17; color: #8bd1b6; }
            QLabel#sandboxChip[state="unprotected"] { background: #211517; color: #e79aa2; }

            QListWidget#threadList { background: transparent; border: none; outline: none; padding: 0; }
            QListWidget#threadList::item {
                background: transparent;
                border: 1px solid transparent;
                border-radius: 10px;
                margin: 1px 0;
                padding: 0;
            }
            QListWidget#threadList::item:hover { background: #0f1218; }
            QListWidget#threadList::item:selected { background: #151624; border-color: #2b2b47; }
            QWidget#threadItemWidget { background: transparent; }
            QLabel#threadItemTitle { background: transparent; color: #dfe2e9; font-size: 13px; font-weight: 650; }
            QLabel#threadItemMeta { background: transparent; color: #697183; font-size: 10px; }
            QLabel#threadStatus { background: #11141a; color: #7f8798; border-radius: 7px; padding: 2px 6px; font-size: 9px; font-weight: 650; }
            QLabel#threadStatus[state="completed"] { color: #86ccb2; background: #0f1916; }
            QLabel#threadStatus[state="running"], QLabel#threadStatus[state="starting"] { color: #95c4fb; background: #111925; }
            QLabel#threadStatus[state="waiting_approval"] { color: #ebbe72; background: #201910; }

            QLabel#connectionDot { color: #59c994; background: transparent; font-size: 9px; }
            QLabel#connectionDot[state="disconnected"] { color: #d86f77; }
            QLabel#protocolLabel { color: #676f80; background: transparent; font-size: 10px; }

            QFrame#emptyState { background: transparent; }
            QFrame#emptyStateContent { background: transparent; min-width: 480px; max-width: 620px; }
            QLabel#emptyKicker { color: #8175e8; font-size: 10px; font-weight: 750; letter-spacing: 1.5px; }
            QLabel#emptyTitle { color: #eff1f6; font-size: 24px; font-weight: 730; }
            QLabel#emptyBody { color: #7e8597; font-size: 13px; line-height: 1.4; }
            QPushButton#promptSuggestion {
                background: #0d1016;
                border: 1px solid #1a1f2a;
                border-radius: 10px;
                min-height: 38px;
                color: #adb3c0;
                font-weight: 550;
                text-align: left;
                padding: 0 12px;
            }
            QPushButton#promptSuggestion:hover { background: #11151d; border-color: #2a3040; color: #e7e9ef; }

            QTextBrowser#transcript {
                background: #08090d;
                border: none;
                color: #edf0f5;
                padding: 0;
                selection-background-color: #3c3765;
            }

            QFrame#composerFrame {
                background: #0d0f15;
                border: 1px solid #20242f;
                border-radius: 14px;
            }
            QTextEdit#composer {
                background: transparent;
                border: none;
                padding: 4px 2px 6px 2px;
                color: #f2f3f7;
                selection-background-color: #484078;
                font-size: 14px;
            }
            QLabel#composerHint, QLabel#composerState { color: #626a7a; font-size: 10px; }
            QLabel#composerState { color: #8990a0; }
            QLabel#composerChip {
                color: #858c9c;
                background: #11141a;
                border-radius: 7px;
                padding: 2px 7px;
                font-size: 9px;
            }

            QFrame#approvalCard { background: #15121d; border: 1px solid #40365d; border-radius: 13px; }
            QLabel#approvalIcon { background: #7467df; color: white; border-radius: 12px; font-weight: 800; }
            QLabel#approvalTitle { color: #eeeaff; font-size: 13px; font-weight: 700; }
            QLabel#approvalDetails { color: #b6b1c3; }

            QPushButton {
                min-height: 32px;
                background: #13161d;
                border: 1px solid #232833;
                border-radius: 9px;
                padding: 1px 11px;
                color: #ccd0da;
                font-weight: 560;
            }
            QPushButton:hover { background: #181c25; border-color: #333a49; color: #f0f2f6; }
            QPushButton:pressed { background: #10131a; }
            QPushButton:disabled { color: #505665; background: #0e1015; border-color: #171b23; }
            QPushButton#newThreadButton, QPushButton#sendButton, QPushButton#allowButton {
                background: #665bda;
                border-color: #6f65df;
                color: white;
                font-weight: 700;
            }
            QPushButton#newThreadButton:hover, QPushButton#sendButton:hover, QPushButton#allowButton:hover { background: #7166e5; }
            QPushButton#openProjectButton { min-width: 54px; }
            QPushButton#denyButton, QPushButton#stopButton { background: #12151b; }
            QPushButton#stopButton:enabled { color: #e49ca3; border-color: #45282d; }
            QPushButton#iconButton {
                min-width: 28px; max-width: 28px;
                min-height: 28px; max-height: 28px;
                padding: 0; border-radius: 8px;
                background: transparent;
                border-color: transparent;
            }
            QPushButton#panelToggle {
                min-height: 26px;
                padding: 0 8px;
                background: transparent;
                border-color: #1a1e27;
                color: #7e8596;
                font-size: 10px;
            }
            QPushButton#panelToggle:hover { color: #c6cad4; border-color: #2b3140; }
            QPushButton#panelToggle[active="true"] { color: #aaa2f6; border-color: #302d4b; }

            QLabel#inspectorTitle { color: #f0f1f5; font-size: 16px; font-weight: 710; }
            QTabWidget#activityTabs::pane { background: transparent; border: none; top: -1px; }
            QTabBar::tab {
                background: transparent;
                color: #6e7586;
                padding: 9px 9px;
                border: none;
                border-bottom: 2px solid transparent;
                font-size: 11px;
            }
            QTabBar::tab:hover { color: #b5bac5; }
            QTabBar::tab:selected { color: #e7e5ff; border-bottom-color: #7569e3; }
            QTextBrowser#activityView, QPlainTextEdit {
                background: #0b0d12;
                border: none;
                color: #adb3bf;
                padding: 8px 2px 8px 5px;
                selection-background-color: #38335b;
                font-family: "Cascadia Mono", "Consolas", monospace;
                font-size: 11px;
            }

            QSplitter::handle { background: #161922; width: 1px; }
            QSplitter::handle:hover { background: #323746; }
            QScrollBar:vertical { background: transparent; width: 8px; margin: 2px; }
            QScrollBar::handle:vertical { background: #292e39; border-radius: 4px; min-height: 30px; }
            QScrollBar::handle:vertical:hover { background: #383e4b; }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
            QScrollBar:horizontal { background: transparent; height: 8px; }
            QScrollBar::handle:horizontal { background: #292e39; border-radius: 4px; min-width: 30px; }
            """
        )
        self.sidebar_toggle_button.setProperty("active", True)
        self.runtime_toggle_button.setProperty("active", True)
        _repolish(self.sidebar_toggle_button)
        _repolish(self.runtime_toggle_button)

    def _apply_initialization(self) -> None:
        protocol = self.initialization.get("protocolVersion", "?")
        server = self.initialization.get("serverInfo") or {}
        capabilities = self.initialization.get("capabilities") or {}
        streaming = bool(capabilities.get("providerStreaming"))
        server_version = _text(server.get("version")) or "?"
        self.protocol_label.setText(
            f"App Server v{server_version} · protocol {protocol}\n"
            f"Streaming {'on' if streaming else 'fallback'}"
        )
        self.connection_dot.setProperty("state", "connected")
        _repolish(self.connection_dot)

    def _run_rpc(self, tag: str, operation: Callable[[], Any]) -> None:
        if self._closed or tag in self._pending_rpc:
            return
        self._pending_rpc.add(tag)

        def runner() -> None:
            try:
                result = operation()
            except Exception as exc:
                self.bridge.rpcError.emit(tag, f"{type(exc).__name__}: {exc}")
            else:
                self.bridge.rpcResult.emit(tag, result)

        threading.Thread(target=runner, name=f"loom-desktop-rpc-{tag[:24]}", daemon=True).start()

    def refresh_threads(self) -> None:
        self._run_rpc("threads", lambda: self.client.thread_list(limit=200))

    def choose_workspace(self) -> None:
        selected = QFileDialog.getExistingDirectory(
            self,
            "Open Loom workspace",
            self.current_workspace or str(self.default_workspace),
        )
        if selected:
            self._create_thread(Path(selected))

    def new_thread_in_current_workspace(self) -> None:
        self._create_thread(Path(self.current_workspace or self.default_workspace))

    def _create_thread(self, workspace: Path) -> None:
        workspace = workspace.expanduser().resolve()
        tag = f"new:{workspace}"
        self._run_rpc(
            tag,
            lambda: self.client.thread_start(
                workspace=workspace,
                permission_mode=self.default_permission_mode,
            ),
        )

    def _thread_selection_changed(
        self,
        current: QListWidgetItem | None,
        _previous: QListWidgetItem | None,
    ) -> None:
        if current is None:
            return
        record = current.data(_THREAD_ROLE) or {}
        thread_id = _text(record.get("id")).strip()
        if not thread_id or thread_id == self.current_thread_id:
            return
        self.load_thread(thread_id)

    def load_thread(self, thread_id: str) -> None:
        thread_id = _text(thread_id).strip()
        if not thread_id:
            return
        self._run_rpc(f"snapshot:{thread_id}", lambda: self.client.thread_read(thread_id))

    def send_prompt(self) -> None:
        text = self.composer.toPlainText().strip()
        if not text or not self.current_thread_id:
            return
        self.composer.clear()
        self._optimistic_user = text
        self._live_assistant.clear()
        self._render_transcript()
        self._set_status("starting")
        self.send_button.setEnabled(False)
        self.stop_button.setEnabled(True)
        thread_id = self.current_thread_id
        self._run_rpc(f"turn:{thread_id}", lambda: self.client.turn_start(thread_id, text))

    def interrupt_turn(self) -> None:
        if not self.current_thread_id:
            return
        thread_id = self.current_thread_id
        turn_id = self.current_turn_id or None
        self._run_rpc(
            f"interrupt:{thread_id}",
            lambda: self.client.turn_interrupt(thread_id, turn_id),
        )

    def respond_approval(self, approved: bool) -> None:
        approval = self.current_snapshot.get("pendingApproval") or {}
        call_id = _text(approval.get("callId")).strip()
        if not call_id or not self.current_thread_id:
            return
        self.allow_button.setEnabled(False)
        self.deny_button.setEnabled(False)
        thread_id = self.current_thread_id
        self._run_rpc(
            f"approval:{thread_id}:{call_id}",
            lambda: self.client.approval_respond(thread_id, call_id, approved=approved),
        )

    def _on_rpc_result(self, tag: str, payload: Any) -> None:
        self._pending_rpc.discard(tag)
        if tag == "threads":
            self._apply_thread_list(payload if isinstance(payload, dict) else {})
            return
        if tag.startswith("new:"):
            record = (payload or {}).get("thread") if isinstance(payload, dict) else None
            if isinstance(record, dict):
                self._threads_by_id[_text(record.get("id"))] = record
                self.current_workspace = _text(record.get("workspace")) or self.current_workspace
                self._startup_autocreate = False
                self.refresh_threads()
                self.load_thread(_text(record.get("id")))
            return
        if tag.startswith("snapshot:"):
            thread_id = tag.split(":", 1)[1]
            if thread_id and isinstance(payload, dict):
                self._apply_snapshot(payload)
            return
        if tag.startswith("turn:"):
            if isinstance(payload, dict):
                turn = payload.get("turn") or {}
                self.current_turn_id = _text(turn.get("id")) or self.current_turn_id
            return
        if tag.startswith("approval:"):
            self._set_status("running")
            self.approval_frame.hide()
            self.stop_button.setEnabled(True)
            return
        if tag.startswith("interrupt:"):
            self._append_activity("Interrupt requested", marker="!")

    def _on_rpc_error(self, tag: str, message: str) -> None:
        self._pending_rpc.discard(tag)
        self._append_activity(f"RPC error · {message}", marker="!")
        if tag.startswith(("turn:", "approval:", "interrupt:")):
            self.send_button.setEnabled(bool(self.current_thread_id))
            self.stop_button.setEnabled(False)
        if tag.startswith("approval:"):
            self.allow_button.setEnabled(True)
            self.deny_button.setEnabled(True)
        if self.isVisible():
            QMessageBox.warning(self, "Loom App Server", message)

    def _apply_thread_list(self, payload: dict[str, Any]) -> None:
        records = payload.get("threads") or []
        if not isinstance(records, list):
            records = []
        selected_id = self.current_thread_id
        self._threads_by_id = {
            _text(record.get("id")): record
            for record in records
            if isinstance(record, dict) and _text(record.get("id"))
        }
        self.thread_section_label.setText(f"THREADS  {len(records)}")
        self.thread_list.blockSignals(True)
        self.thread_list.clear()
        selected_item: QListWidgetItem | None = None
        for record in records:
            if not isinstance(record, dict):
                continue
            item = QListWidgetItem()
            item.setSizeHint(QSize(0, 58))
            item.setToolTip(_text(record.get("workspace")))
            item.setData(_THREAD_ROLE, record)
            self.thread_list.addItem(item)
            self.thread_list.setItemWidget(
                item,
                ThreadListItemWidget(
                    record,
                    self.thread_list,
                    active_workspace=self.current_workspace,
                ),
            )
            if _text(record.get("id")) == selected_id:
                selected_item = item
        self.thread_list.blockSignals(False)

        if selected_item is not None:
            self.thread_list.setCurrentItem(selected_item)
            return
        if records:
            first = self.thread_list.item(0)
            self.thread_list.setCurrentItem(first)
            record = first.data(_THREAD_ROLE) or {}
            self.load_thread(_text(record.get("id")))
            self._startup_autocreate = False
            return
        if self._startup_autocreate:
            self._startup_autocreate = False
            self._create_thread(self.default_workspace)

    def _apply_snapshot(self, snapshot: dict[str, Any]) -> None:
        thread = snapshot.get("thread") or {}
        thread_id = _text(thread.get("id")).strip()
        if not thread_id:
            return
        self.current_snapshot = snapshot
        self.current_thread_id = thread_id
        self.current_turn_id = _text(thread.get("currentTurnId"))
        self.current_workspace = _text(thread.get("workspace")) or self.current_workspace
        messages = snapshot.get("messages") or []
        self._durable_messages = [dict(item) for item in messages if isinstance(item, dict)]
        self._optimistic_user = None

        durable_assistant_texts = {
            _text(message.get("content"))
            for message in self._durable_messages
            if _text(message.get("role")) == "assistant"
        }
        self._live_assistant = {
            item_id: text
            for item_id, text in self._live_assistant.items()
            if text and text not in durable_assistant_texts
        }

        self.thread_title_label.setText(_text(thread.get("title")).strip() or "New thread")
        self.workspace_label.setText(_short_path(self.current_workspace))
        self.workspace_path_label.setText(self.current_workspace)
        self.permission_label.setText(
            _text(thread.get("permissionMode")) or self.default_permission_mode
        )
        self._set_status(_text(thread.get("status")) or "idle")
        usage = thread.get("usage") or {}
        self.usage_label.setText(f"{int(usage.get('totalTokens') or 0):,} tokens")
        self._apply_pending_approval(snapshot.get("pendingApproval"))
        self._render_transcript()
        self._render_runtime_panels(snapshot)
        self._select_thread_item(thread_id)

    def _select_thread_item(self, thread_id: str) -> None:
        for index in range(self.thread_list.count()):
            item = self.thread_list.item(index)
            record = item.data(_THREAD_ROLE) or {}
            if _text(record.get("id")) == thread_id:
                self.thread_list.blockSignals(True)
                self.thread_list.setCurrentItem(item)
                self.thread_list.blockSignals(False)
                return

    def _set_status(self, status: str) -> None:
        status = status or "idle"
        self.status_label.setText(_human_status(status))
        self.status_label.setProperty("state", status)
        _repolish(self.status_label)
        active = status in {"running", "starting", "waiting_approval"}
        self.stop_button.setEnabled(active and bool(self.current_thread_id))
        self.send_button.setEnabled(bool(self.current_thread_id) and not active)
        if status == "waiting_approval":
            composer_state = "Waiting for approval"
        elif status in {"running", "starting"}:
            composer_state = "Loom is working"
        elif status == "failed":
            composer_state = "Turn failed"
        else:
            composer_state = "Ready"
        self.composer_state_label.setText(composer_state)

    def _apply_pending_approval(self, approval: Any) -> None:
        if not isinstance(approval, dict) or not approval.get("callId"):
            self.approval_frame.hide()
            return
        self.current_snapshot["pendingApproval"] = approval
        tool = _text(approval.get("toolName")) or "tool"
        reason = _text(approval.get("reason"))
        effect = _text(approval.get("effect"))
        arguments = approval.get("arguments") or {}
        self.approval_title.setText(f"Approval required · {tool}")
        detail_parts = [part for part in (effect, reason) if part]
        detail = " · ".join(detail_parts)
        if arguments:
            detail += ("\n" if detail else "") + _pretty(arguments)
        self.approval_details.setText(detail)
        self.allow_button.setEnabled(True)
        self.deny_button.setEnabled(True)
        self.approval_frame.show()

    def _render_transcript(self) -> None:
        cards: list[str] = []
        for message in self._durable_messages:
            role = _text(message.get("role"))
            content = _text(message.get("content"))
            if role not in {"user", "assistant"} or not content:
                continue
            cards.append(self._message_card(role, content, streaming=False))
        if self._optimistic_user:
            cards.append(self._message_card("user", self._optimistic_user, streaming=True))
        for text in self._live_assistant.values():
            cards.append(self._message_card("assistant", text or "…", streaming=True))

        if not cards:
            self.transcript.hide()
            self.empty_state.show()
            return

        self.empty_state.hide()
        self.transcript.show()
        scrollbar = self.transcript.verticalScrollBar()
        follow_tail = scrollbar.maximum() - scrollbar.value() <= 48
        old_value = scrollbar.value()

        document = """
            <style>
                body { color: #e8ebf0; font-family: 'Segoe UI', sans-serif; font-size: 14px; margin: 10px 8px 24px 8px; background: #08090d; }
                .row { margin: 3px 0 20px 0; }
                .meta { color: #72798a; font-size: 9px; font-weight: 700; letter-spacing: 1.15px; margin: 0 0 6px 2px; }
                .assistant .meta { color: #8f86ec; }
                .bubble { color: #e9ecf1; line-height: 1.45; white-space: pre-wrap; }
                .assistant .bubble { background: transparent; border-left: 2px solid #29263d; padding: 2px 10px 2px 12px; margin-right: 24px; }
                .user .bubble { background: #141522; border: 1px solid #25263a; border-radius: 12px; padding: 10px 13px; margin-left: 56px; }
                .stream { color: #9e95f0; font-size: 9px; font-weight: 650; }
            </style>
        """ + "".join(cards)
        self.transcript.setHtml(document)

        if follow_tail:
            cursor = self.transcript.textCursor()
            cursor.movePosition(QTextCursor.MoveOperation.End)
            self.transcript.setTextCursor(cursor)
        else:
            scrollbar.setValue(min(old_value, scrollbar.maximum()))

    @staticmethod
    def _message_card(role: str, content: str, *, streaming: bool) -> str:
        label = "YOU" if role == "user" else "LOOM"
        safe = html.escape(content).replace("\n", "<br>")
        live = " <span class='stream'>· LIVE</span>" if streaming else ""
        return (
            f"<div class='row {role}'>"
            f"<div class='meta'>{label}{live}</div>"
            f"<div class='bubble'>{safe}</div>"
            "</div>"
        )

    def _render_runtime_panels(self, snapshot: dict[str, Any]) -> None:
        events = snapshot.get("events") or []
        self._activity_tail = [
            (
                _short_time(event.get("createdAt")),
                _event_marker(event.get("kind")),
                _event_summary(event),
            )
            for event in events[-300:]
            if isinstance(event, dict)
        ]
        self._render_activity()

        process_items: list[dict[str, Any]] = []
        diff_items: list[dict[str, Any]] = []
        browser_items: list[dict[str, Any]] = []
        agent_items: list[dict[str, Any]] = []
        for turn in snapshot.get("turns") or []:
            if not isinstance(turn, dict):
                continue
            for item in turn.get("items") or []:
                if not isinstance(item, dict):
                    continue
                item_type = _text(item.get("type"))
                if item_type == "process":
                    process_items.append(item)
                elif item_type == "file_edit":
                    diff_items.append(item)
                elif item_type == "tool_call":
                    tool = _text(item.get("toolName"))
                    lowered = tool.casefold()
                    if "browser" in lowered:
                        browser_items.append(item)
                    if tool in {
                        "spawn_agent",
                        "send_agent_message",
                        "wait_agent",
                        "list_agents",
                        "close_agent",
                    }:
                        agent_items.append(item)

        self.terminal_view.setPlainText(self._process_text(process_items))
        self.diff_view.setPlainText(self._diff_text(diff_items))
        self.browser_view.setPlainText(self._tool_activity_text(browser_items, "browser"))
        self.agents_view.setPlainText(self._tool_activity_text(agent_items, "agent"))
        self._update_sandbox_status(process_items)

    def _render_activity(self) -> None:
        rows = []
        for when, marker, summary in self._activity_tail[-300:]:
            rows.append(
                "<div class='entry'>"
                f"<span class='marker'>{html.escape(marker)}</span>"
                "<div class='body'>"
                f"<div class='summary'>{html.escape(summary)}</div>"
                f"<div class='time'>{html.escape(when or 'live')}</div>"
                "</div>"
                "</div>"
            )
        if not rows:
            rows.append(
                """
                <div class="empty">
                    <div class="emptyTitle">Runtime is quiet</div>
                    <div class="emptyBody">Model steps, tools, commands, diffs and delegated work will appear here.</div>
                </div>
                """
            )
        self.activity_view.setHtml(
            """
            <style>
                body { font-family: 'Segoe UI', sans-serif; background: #0b0d12; color: #acb2bf; margin: 6px 3px; font-size: 11px; }
                .entry { display: block; border-bottom: 1px solid #171a22; padding: 8px 4px; margin: 0; }
                .marker { color: #8b82e6; font-weight: 700; margin-right: 8px; }
                .body { display: inline; }
                .summary { display: inline; color: #b9bec8; }
                .time { color: #5f6676; font-family: 'Consolas', monospace; font-size: 9px; margin-top: 3px; margin-left: 17px; }
                .empty { margin: 52px 10px 0 10px; }
                .emptyTitle { color: #c2c6cf; font-size: 13px; font-weight: 700; margin-bottom: 5px; }
                .emptyBody { color: #6d7484; }
            </style>
            """
            + "".join(rows)
        )
        cursor = self.activity_view.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        self.activity_view.setTextCursor(cursor)

    @staticmethod
    def _process_text(items: list[dict[str, Any]]) -> str:
        if not items:
            return "Terminal is quiet.\n\nManaged command output will appear here when Loom runs a process."
        chunks: list[str] = []
        for item in items[-20:]:
            argv = item.get("argv") or []
            command = " ".join(str(part) for part in argv)
            status = _human_status(item.get("status"))
            header = (
                f"{status}  ·  {_text(item.get('processId'))}\n"
                f"$ {command}\n"
                f"{_text(item.get('cwd'))}"
            )
            output = _text(item.get("stdout"))
            error = _text(item.get("stderr"))
            if output:
                header += f"\n\nstdout\n{output[-8000:]}"
            if error:
                header += f"\n\nstderr\n{error[-8000:]}"
            chunks.append(header)
        return "\n\n────────────────────────────────\n\n".join(chunks)

    @staticmethod
    def _diff_text(items: list[dict[str, Any]]) -> str:
        if not items:
            return "No workspace changes yet.\n\nThe latest turn diff will appear here after Loom edits files."
        latest = items[-1]
        paths = latest.get("paths") or []
        prefix = "Changed paths\n" + "\n".join(f"  {path}" for path in paths)
        diff = _text(latest.get("diff"))
        if latest.get("truncated"):
            prefix += "\n\n(diff truncated by Runtime)"
        return prefix + (f"\n\n{diff}" if diff else "")

    @staticmethod
    def _tool_activity_text(items: list[dict[str, Any]], category: str) -> str:
        if not items:
            if category == "browser":
                return "Browser is idle.\n\nNavigation and Browser tool activity will appear here when Loom uses the web."
            return "No agent coordination yet.\n\nSub-agent control activity will appear here when Loom delegates work."
        chunks = []
        for item in items[-30:]:
            chunks.append(
                f"{_human_status(item.get('status'))}  ·  {_text(item.get('toolName'))}\n"
                f"{_pretty(item.get('arguments') or {})}"
            )
        return "\n\n────────────────────────────────\n\n".join(chunks)

    def _update_sandbox_status(self, processes: list[dict[str, Any]]) -> None:
        sandbox: dict[str, Any] | None = None
        for item in reversed(processes):
            value = item.get("sandbox")
            if isinstance(value, dict) and value:
                sandbox = value
                break
        if sandbox is None:
            self.sandbox_label.setText("No process")
            self.sandbox_label.setProperty("state", "idle")
            _repolish(self.sandbox_label)
            return
        enforced = bool(sandbox.get("enforced"))
        backend = _text(sandbox.get("backend")) or "none"
        self.sandbox_label.setText(f"{'Sandboxed' if enforced else 'Not sandboxed'} · {backend}")
        self.sandbox_label.setProperty("state", "enforced" if enforced else "unprotected")
        _repolish(self.sandbox_label)

    def _on_notification(self, method: str, params: Any) -> None:
        if not isinstance(params, dict):
            return
        thread_id = _text(params.get("threadId"))
        if not thread_id:
            item = params.get("item") or {}
            thread_id = _text(item.get("threadId")) if isinstance(item, dict) else ""
        if method == "thread/started":
            self.refresh_threads()
            return
        if self.current_thread_id and thread_id and thread_id != self.current_thread_id:
            return

        self._append_activity(_notification_summary(method, params))
        if method == "turn/started":
            turn = params.get("turn") or {}
            self.current_turn_id = _text(turn.get("id")) or self.current_turn_id
            self._set_status("running")
            return
        if method == "item/started":
            item = params.get("item") or {}
            if item.get("type") == "assistant_message":
                self._live_assistant.setdefault(_text(item.get("id")), "")
                self._render_transcript()
            return
        if method == "item/delta":
            self._apply_item_delta(params)
            return
        if method == "item/completed":
            item = params.get("item") or {}
            item_id = _text(item.get("id"))
            if item.get("type") == "assistant_message" and item_id:
                self._live_assistant.pop(item_id, None)
            if item.get("type") == "user_message":
                self._optimistic_user = None
            self._reconcile_current_thread()
            return
        if method == "approval/requested":
            approval = params.get("approval") or {}
            if isinstance(approval, dict):
                normalized = {
                    "callId": approval.get("callId"),
                    "toolName": approval.get("toolName"),
                    "arguments": approval.get("arguments") or {},
                    "effect": approval.get("effect"),
                    "reason": approval.get("reason"),
                }
                self.current_snapshot["pendingApproval"] = normalized
                self._apply_pending_approval(normalized)
                self._set_status("waiting_approval")
            return
        if method == "turn/completed":
            turn = params.get("turn") or {}
            self._set_status(_text(turn.get("status")) or "completed")
            self._live_assistant.clear()
            self._optimistic_user = None
            self._reconcile_current_thread()
            self.refresh_threads()

    def _apply_item_delta(self, params: dict[str, Any]) -> None:
        item_id = _text(params.get("itemId"))
        delta = params.get("delta") or {}
        if not isinstance(delta, dict):
            return
        if "text" in delta and item_id:
            self._live_assistant[item_id] = self._live_assistant.get(item_id, "") + _text(delta.get("text"))
            self._render_transcript()
        stdout = _text(delta.get("stdout"))
        stderr = _text(delta.get("stderr"))
        if stdout:
            self.terminal_view.appendPlainText(stdout.rstrip("\n"))
        if stderr:
            self.terminal_view.appendPlainText(stderr.rstrip("\n"))

    def _reconcile_current_thread(self) -> None:
        if self.current_thread_id:
            self.load_thread(self.current_thread_id)

    def _append_activity(self, text: str, *, marker: str = "•") -> None:
        self._activity_tail = self._activity_tail[-299:]
        self._activity_tail.append(("live", marker, text))
        self._render_activity()

    def _on_server_stderr(self, line: str) -> None:
        self._append_activity(f"Server · {line}", marker="!")

    def _on_server_exit(self, message: str) -> None:
        self.protocol_label.setText("App Server · stopped")
        self.connection_dot.setProperty("state", "disconnected")
        _repolish(self.connection_dot)
        self.send_button.setEnabled(False)
        self.stop_button.setEnabled(False)
        self._append_activity(message, marker="!")
        if self.isVisible() and not self._closed:
            QMessageBox.critical(self, "Loom App Server stopped", message)

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt override
        self._closed = True
        close = getattr(self.client, "close", None)
        if callable(close):
            close()
        event.accept()


__all__ = [
    "ComposerTextEdit",
    "DesktopEventBridge",
    "LoomDesktopWindow",
    "ThreadListItemWidget",
]
