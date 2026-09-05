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


def _event_summary(event: dict[str, Any]) -> str:
    kind = _text(event.get("kind")) or "event"
    data = event.get("data") or {}
    if not isinstance(data, dict):
        data = {}
    if kind.startswith("tool_"):
        tool = _text(data.get("tool"))
        call_id = _text(data.get("call_id"))
        suffix = " · ".join(part for part in (tool, call_id[:12]) if part)
        return f"{kind.replace('_', ' ')}{' · ' + suffix if suffix else ''}"
    if kind.startswith("process_"):
        process_id = _text(data.get("process_id"))
        return f"{kind.replace('_', ' ')}{' · ' + process_id[:12] if process_id else ''}"
    if kind == "turn_diff_updated":
        paths = data.get("paths") or []
        return f"workspace diff · {len(paths)} path(s)"
    if kind == "model_requested":
        return f"model step {data.get('step', '?')}"
    if kind == "model_response":
        usage = data.get("usage") or {}
        total = usage.get("total_tokens") if isinstance(usage, dict) else None
        return f"model response{f' · {total} tokens' if total else ''}"
    return kind.replace("_", " ")


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
    if value == "tool_approval_required":
        return "!"
    if value == "turn_started":
        return "→"
    return "•"


def _notification_summary(method: str, params: dict[str, Any]) -> str:
    if method == "item/delta":
        delta = params.get("delta") or {}
        if "text" in delta:
            return "assistant streaming"
        if "stdout" in delta or "stderr" in delta:
            return "process output"
        if delta.get("kind") == "tool_call_argument":
            return f"tool arguments · {_text(delta.get('toolName')) or 'tool'}"
        if delta.get("status"):
            return f"item · {delta.get('status')}"
    if method == "approval/requested":
        approval = params.get("approval") or {}
        return f"approval requested · {_text(approval.get('toolName')) or 'tool'}"
    if method == "turn/completed":
        turn = params.get("turn") or {}
        return f"turn · {_text(turn.get('status')) or 'completed'}"
    return method


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
    """Compact product-style row for one durable Loom thread."""

    def __init__(self, record: dict[str, Any], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("threadItemWidget")
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(11, 8, 11, 8)
        layout.setSpacing(4)

        title = QLabel(_text(record.get("title")).strip() or "New thread")
        title.setObjectName("threadItemTitle")
        title.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(title)

        meta = QHBoxLayout()
        meta.setContentsMargins(0, 0, 0, 0)
        meta.setSpacing(7)
        workspace = QLabel(_short_path(record.get("workspace")))
        workspace.setObjectName("threadItemMeta")
        meta.addWidget(workspace, 1)

        status_value = _text(record.get("status")) or "idle"
        status = QLabel(_human_status(status_value))
        status.setObjectName("threadStatus")
        status.setProperty("state", status_value)
        meta.addWidget(status)
        layout.addLayout(meta)


class LoomDesktopWindow(QMainWindow):
    """Native Loom client over the stable App Server protocol.

    This class deliberately knows nothing about AgentRuntime or provider secrets.
    Its only execution surface is the JSON-RPC client supplied by the launcher.
    """

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
        self.resize(1580, 960)
        self.setMinimumSize(1100, 700)

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
        self.main_splitter.setSizes([290, 900, 390])
        self.main_splitter.setStretchFactor(0, 0)
        self.main_splitter.setStretchFactor(1, 1)
        self.main_splitter.setStretchFactor(2, 0)

    def _build_sidebar(self) -> None:
        panel = QFrame()
        panel.setObjectName("sidebar")
        panel.setMinimumWidth(260)
        panel.setMaximumWidth(360)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(16, 18, 14, 16)
        layout.setSpacing(14)

        brand_row = QHBoxLayout()
        brand_row.setSpacing(10)
        mark = QLabel("L")
        mark.setObjectName("brandMark")
        mark.setAlignment(Qt.AlignmentFlag.AlignCenter)
        mark.setFixedSize(34, 34)
        brand_row.addWidget(mark)

        brand_text = QVBoxLayout()
        brand_text.setSpacing(1)
        self.brand_label = QLabel("Loom")
        self.brand_label.setObjectName("brandLabel")
        brand_text.addWidget(self.brand_label)
        subtitle = QLabel("Local coding agent")
        subtitle.setObjectName("brandSubtitle")
        brand_text.addWidget(subtitle)
        brand_row.addLayout(brand_text, 1)
        layout.addLayout(brand_row)

        actions = QHBoxLayout()
        actions.setSpacing(8)
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
        thread_header.setContentsMargins(2, 2, 0, 0)
        label = QLabel("THREADS")
        label.setObjectName("sectionLabel")
        thread_header.addWidget(label)
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
        self.thread_list.setSpacing(3)
        self.thread_list.currentItemChanged.connect(self._thread_selection_changed)
        layout.addWidget(self.thread_list, 1)

        self.connection_card = QFrame()
        self.connection_card.setObjectName("connectionCard")
        connection_layout = QHBoxLayout(self.connection_card)
        connection_layout.setContentsMargins(10, 9, 10, 9)
        connection_layout.setSpacing(8)
        self.connection_dot = QLabel("●")
        self.connection_dot.setObjectName("connectionDot")
        self.connection_dot.setProperty("state", "connected")
        connection_layout.addWidget(self.connection_dot, 0, Qt.AlignmentFlag.AlignTop)
        self.protocol_label = QLabel("App Server · disconnected")
        self.protocol_label.setObjectName("protocolLabel")
        self.protocol_label.setWordWrap(True)
        connection_layout.addWidget(self.protocol_label, 1)
        layout.addWidget(self.connection_card)

        self.main_splitter.addWidget(panel)

    def _build_conversation_panel(self) -> None:
        panel = QFrame()
        panel.setObjectName("conversationPanel")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(14)

        header = QFrame()
        header.setObjectName("workspaceHeader")
        header_layout = QVBoxLayout(header)
        header_layout.setContentsMargins(16, 13, 16, 12)
        header_layout.setSpacing(7)

        eyebrow_row = QHBoxLayout()
        eyebrow = QLabel("CURRENT THREAD")
        eyebrow.setObjectName("eyebrowLabel")
        eyebrow_row.addWidget(eyebrow)
        eyebrow_row.addStretch(1)
        self.usage_label = QLabel("0 tokens")
        self.usage_label.setObjectName("tokenBadge")
        eyebrow_row.addWidget(self.usage_label)
        header_layout.addLayout(eyebrow_row)

        first_line = QHBoxLayout()
        self.thread_title_label = QLabel("New thread")
        self.thread_title_label.setObjectName("threadTitle")
        self.thread_title_label.setTextFormat(Qt.TextFormat.PlainText)
        first_line.addWidget(self.thread_title_label, 1)
        self.status_label = QLabel("idle")
        self.status_label.setObjectName("statusChip")
        self.status_label.setProperty("state", "idle")
        first_line.addWidget(self.status_label)
        self.permission_label = QLabel(self.default_permission_mode)
        self.permission_label.setObjectName("permissionChip")
        first_line.addWidget(self.permission_label)
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

        self.transcript = QTextBrowser()
        self.transcript.setObjectName("transcript")
        self.transcript.setOpenExternalLinks(False)
        self.transcript.setFrameShape(QFrame.Shape.NoFrame)
        self.transcript.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.transcript.setPlaceholderText("Start a thread and give Loom a task.")
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
        composer_layout.setContentsMargins(14, 11, 12, 11)
        composer_layout.setSpacing(8)

        composer_header = QHBoxLayout()
        composer_label = QLabel("MESSAGE")
        composer_label.setObjectName("eyebrowLabel")
        composer_header.addWidget(composer_label)
        composer_header.addStretch(1)
        self.composer_state_label = QLabel("Ready")
        self.composer_state_label.setObjectName("composerState")
        composer_header.addWidget(self.composer_state_label)
        composer_layout.addLayout(composer_header)

        self.composer = ComposerTextEdit()
        self.composer.setObjectName("composer")
        self.composer.setPlaceholderText("Ask Loom to inspect, edit, run, debug, browse, or coordinate…")
        self.composer.setMaximumHeight(128)
        self.composer.setMinimumHeight(66)
        self.composer.sendRequested.connect(self.send_prompt)
        composer_layout.addWidget(self.composer)

        compose_actions = QHBoxLayout()
        hint = QLabel("Enter to send  ·  Shift+Enter for newline")
        hint.setObjectName("composerHint")
        compose_actions.addWidget(hint)
        compose_actions.addStretch(1)
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

    def _build_activity_panel(self) -> None:
        panel = QFrame()
        panel.setObjectName("activityPanel")
        panel.setMinimumWidth(330)
        panel.setMaximumWidth(560)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(16, 20, 16, 16)
        layout.setSpacing(12)

        title_row = QHBoxLayout()
        title_stack = QVBoxLayout()
        title_stack.setSpacing(1)
        title = QLabel("Runtime")
        title.setObjectName("inspectorTitle")
        title_stack.addWidget(title)
        subtitle = QLabel("Live execution inspector")
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

        self.main_splitter.addWidget(panel)

    @staticmethod
    def _read_only_panel(name: str) -> QPlainTextEdit:
        view = QPlainTextEdit()
        view.setObjectName(name)
        view.setReadOnly(True)
        view.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        return view

    def _apply_style(self) -> None:
        self.setStyleSheet(
            """
            QMainWindow, QWidget {
                background: #08090d;
                color: #eef0f5;
                font-family: "Segoe UI Variable", "Segoe UI", sans-serif;
                font-size: 13px;
            }
            QFrame#sidebar, QFrame#activityPanel { background: #0b0d12; }
            QFrame#conversationPanel { background: #090a0f; }
            QLabel#brandMark { background: #6f5ae8; color: white; border-radius: 10px; font-size: 16px; font-weight: 800; }
            QLabel#brandLabel { color: #f7f7fb; font-size: 17px; font-weight: 700; }
            QLabel#brandSubtitle { color: #747b8d; font-size: 11px; }
            QLabel#sectionLabel, QLabel#eyebrowLabel { color: #777f93; font-size: 10px; font-weight: 700; letter-spacing: 1.4px; }
            QLabel#mutedLabel { color: #767d8e; font-size: 11px; }
            QLabel#threadTitle { color: #f4f5f8; font-size: 19px; font-weight: 700; }
            QLabel#projectName { color: #a6acbb; font-size: 11px; font-weight: 600; background: #151720; border: 1px solid #232735; border-radius: 7px; padding: 2px 7px; }
            QLabel#tokenBadge { color: #83899a; background: #10121a; border: 1px solid #1f2330; border-radius: 8px; padding: 3px 8px; font-size: 10px; }
            QLabel#statusChip, QLabel#permissionChip, QLabel#sandboxChip { border-radius: 9px; padding: 4px 9px; font-size: 10px; font-weight: 650; }
            QLabel#statusChip { background: #141720; border: 1px solid #252a38; color: #a7adbd; }
            QLabel#statusChip[state="running"], QLabel#statusChip[state="starting"] { background: #151c28; border-color: #28405f; color: #9bc8ff; }
            QLabel#statusChip[state="completed"] { background: #111c19; border-color: #21463a; color: #91d7bd; }
            QLabel#statusChip[state="waiting_approval"] { background: #241b10; border-color: #5a4320; color: #efc37f; }
            QLabel#statusChip[state="failed"], QLabel#statusChip[state="cancelled"] { background: #251516; border-color: #583032; color: #f1a4a9; }
            QLabel#permissionChip { background: #171529; border: 1px solid #312c58; color: #b9afff; }
            QLabel#sandboxChip { background: #12151d; border: 1px solid #232938; color: #8d94a5; }
            QLabel#sandboxChip[state="enforced"] { background: #111c19; border-color: #21463a; color: #91d7bd; }
            QLabel#sandboxChip[state="unprotected"] { background: #231819; border-color: #503034; color: #eaa2a8; }
            QFrame#workspaceHeader { background: #101219; border: 1px solid #202431; border-radius: 14px; }
            QFrame#composerFrame { background: #101219; border: 1px solid #252938; border-radius: 15px; }
            QFrame#composerFrame:hover { border-color: #30364a; }
            QFrame#approvalCard { background: #171323; border: 1px solid #4d3f72; border-radius: 14px; }
            QLabel#approvalIcon { background: #6f5ae8; color: white; border-radius: 12px; font-weight: 800; }
            QLabel#approvalTitle { color: #efeaff; font-size: 13px; font-weight: 700; }
            QLabel#approvalDetails { color: #b8b3c5; }
            QListWidget#threadList { background: transparent; border: none; outline: none; padding: 0; }
            QListWidget#threadList::item { background: #0e1016; border: 1px solid #171b25; border-radius: 11px; margin: 1px 0; padding: 0; }
            QListWidget#threadList::item:hover { background: #12151d; border-color: #252a39; }
            QListWidget#threadList::item:selected { background: #17172a; border-color: #3b3767; }
            QWidget#threadItemWidget { background: transparent; }
            QLabel#threadItemTitle { background: transparent; color: #dfe2ea; font-size: 12px; font-weight: 650; }
            QLabel#threadItemMeta { background: transparent; color: #6f7688; font-size: 10px; }
            QLabel#threadStatus { background: #151821; color: #82899a; border-radius: 7px; padding: 2px 6px; font-size: 9px; font-weight: 650; }
            QLabel#threadStatus[state="completed"] { color: #8fd1b7; background: #111b18; }
            QLabel#threadStatus[state="running"], QLabel#threadStatus[state="starting"] { color: #9bc8ff; background: #131a25; }
            QLabel#threadStatus[state="waiting_approval"] { color: #efc37f; background: #211a11; }
            QFrame#connectionCard { background: #0f1117; border: 1px solid #1b1f2a; border-radius: 11px; }
            QLabel#connectionDot { color: #5ecf9b; background: transparent; font-size: 10px; }
            QLabel#connectionDot[state="disconnected"] { color: #d66b73; }
            QLabel#protocolLabel { color: #72798a; background: transparent; font-size: 10px; }
            QTextBrowser#transcript { background: #090a0f; border: none; color: #eef0f5; padding: 0; selection-background-color: #40376b; }
            QTextEdit#composer { background: #0c0e14; border: 1px solid #1e2230; border-radius: 10px; padding: 9px 10px; color: #f3f4f8; selection-background-color: #4c4380; }
            QTextEdit#composer:focus { border-color: #4a427b; background: #0d0f16; }
            QLabel#composerHint, QLabel#composerState { color: #666d7e; font-size: 10px; }
            QLabel#composerState { color: #8c93a4; }
            QPushButton { min-height: 32px; background: #151820; border: 1px solid #252a38; border-radius: 9px; padding: 1px 11px; color: #cfd3dd; font-weight: 550; }
            QPushButton:hover { background: #1a1e28; border-color: #343b4d; color: #f2f3f7; }
            QPushButton:pressed { background: #11141b; }
            QPushButton:disabled { color: #525866; background: #0f1117; border-color: #191d27; }
            QPushButton#newThreadButton, QPushButton#sendButton, QPushButton#allowButton { background: #6756db; border-color: #7768e7; color: white; font-weight: 700; }
            QPushButton#newThreadButton:hover, QPushButton#sendButton:hover, QPushButton#allowButton:hover { background: #7564ea; border-color: #8a7cf2; }
            QPushButton#openProjectButton { min-width: 54px; }
            QPushButton#denyButton, QPushButton#stopButton { background: #14161d; }
            QPushButton#stopButton:enabled { color: #e6a2a7; border-color: #4b2a2f; }
            QPushButton#iconButton { min-width: 28px; max-width: 28px; min-height: 28px; max-height: 28px; padding: 0; border-radius: 8px; }
            QLabel#inspectorTitle { color: #f0f1f5; font-size: 16px; font-weight: 700; }
            QTabWidget#activityTabs::pane { background: #0d0f15; border: 1px solid #1d212c; border-radius: 12px; top: -1px; }
            QTabBar::tab { background: transparent; color: #6f7687; padding: 9px 10px; border: none; border-bottom: 2px solid transparent; font-size: 11px; }
            QTabBar::tab:hover { color: #b6bbc7; }
            QTabBar::tab:selected { color: #e9e7ff; border-bottom-color: #7868eb; }
            QTextBrowser#activityView, QPlainTextEdit { background: #0d0f15; border: none; color: #aeb4c1; padding: 10px; selection-background-color: #3d365f; font-family: "Cascadia Mono", "Consolas", monospace; font-size: 11px; }
            QSplitter::handle { background: #181b24; width: 1px; }
            QSplitter::handle:hover { background: #34394a; }
            QScrollBar:vertical { background: transparent; width: 9px; margin: 2px; }
            QScrollBar::handle:vertical { background: #2b303d; border-radius: 4px; min-height: 30px; }
            QScrollBar::handle:vertical:hover { background: #3a4050; }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
            QScrollBar:horizontal { background: transparent; height: 9px; }
            QScrollBar::handle:horizontal { background: #2b303d; border-radius: 4px; min-width: 30px; }
            """
        )

    def _apply_initialization(self) -> None:
        protocol = self.initialization.get("protocolVersion", "?")
        server = self.initialization.get("serverInfo") or {}
        capabilities = self.initialization.get("capabilities") or {}
        streaming = bool(capabilities.get("providerStreaming"))
        server_version = _text(server.get("version")) or "?"
        self.protocol_label.setText(
            f"App Server v{server_version} · protocol {protocol}\n"
            f"Provider streaming · {'on' if streaming else 'fallback'}"
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
            self._append_activity("interrupt requested")
            return

    def _on_rpc_error(self, tag: str, message: str) -> None:
        self._pending_rpc.discard(tag)
        self._append_activity(f"RPC error · {message}", marker="!")
        if tag.startswith(("turn:", "approval:", "interrupt:")):
            self.send_button.setEnabled(bool(self.current_thread_id))
            self.stop_button.setEnabled(False)
        if tag.startswith("approval:"):
            self.allow_button.setEnabled(True)
            self.deny_button.setEnabled(True)
        if not self.isVisible():
            return
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
        self.thread_list.blockSignals(True)
        self.thread_list.clear()
        selected_item: QListWidgetItem | None = None
        for record in records:
            if not isinstance(record, dict):
                continue
            item = QListWidgetItem()
            item.setSizeHint(QSize(0, 61))
            item.setToolTip(_text(record.get("workspace")))
            item.setData(_THREAD_ROLE, record)
            self.thread_list.addItem(item)
            self.thread_list.setItemWidget(item, ThreadListItemWidget(record, self.thread_list))
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
        scrollbar = self.transcript.verticalScrollBar()
        follow_tail = scrollbar.maximum() - scrollbar.value() <= 48
        old_value = scrollbar.value()

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
            cards.append(
                """
                <div class="empty">
                    <div class="emptyTitle">Ready when you are</div>
                    <div class="emptyBody">Ask Loom to inspect this project, change code, run commands, browse, or coordinate a longer task.</div>
                </div>
                """
            )

        document = """
            <style>
                body { color: #e8eaf0; font-family: 'Segoe UI', sans-serif; font-size: 13px; margin: 12px 10px 20px 10px; background: #090a0f; }
                .row { margin: 4px 0 18px 0; }
                .meta { color: #7d8496; font-size: 9px; font-weight: 700; letter-spacing: 1.2px; margin: 0 0 6px 2px; }
                .assistant .meta { color: #9087f2; }
                .user .meta { color: #8990a2; }
                .bubble { color: #e9ebf1; background: #101219; border: 1px solid #1e2230; border-radius: 12px; padding: 12px 14px; white-space: pre-wrap; }
                .assistant .bubble { background: #0f1117; border-color: #1b1f2a; margin-right: 20px; }
                .user .bubble { background: #17172a; border-color: #2f2d50; margin-left: 44px; }
                .stream { color: #a99fff; font-size: 9px; font-weight: 600; }
                .empty { margin: 90px 24px 0 24px; padding: 24px; background: #0e1016; border: 1px solid #181c26; border-radius: 14px; color: #7a8293; }
                .emptyTitle { color: #d9dce5; font-size: 16px; font-weight: 700; margin-bottom: 7px; }
                .emptyBody { color: #747b8d; font-size: 12px; }
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
        activity_tail: list[tuple[str, str, str]] = []
        for event in events[-300:]:
            if not isinstance(event, dict):
                continue
            activity_tail.append(
                (
                    _short_time(event.get("createdAt")),
                    _event_marker(event.get("kind")),
                    _event_summary(event),
                )
            )
        self._activity_tail = activity_tail
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
            safe_time = html.escape(when or "live")
            safe_marker = html.escape(marker)
            safe_summary = html.escape(summary)
            rows.append(
                "<div class='entry'>"
                f"<span class='time'>{safe_time}</span>"
                f"<span class='marker'>{safe_marker}</span>"
                f"<span class='summary'>{safe_summary}</span>"
                "</div>"
            )
        if not rows:
            rows.append(
                """
                <div class="empty">
                    <div class="emptyTitle">No activity yet</div>
                    <div class="emptyBody">Model steps, tools, processes and workspace changes will appear here.</div>
                </div>
                """
            )
        self.activity_view.setHtml(
            """
            <style>
                body { font-family: 'Segoe UI', sans-serif; background: #0d0f15; color: #aeb4c1; margin: 8px; font-size: 11px; }
                .entry { background: #10131a; border: 1px solid #191e29; border-radius: 8px; padding: 7px 8px; margin: 0 0 6px 0; }
                .time { color: #596174; font-family: 'Consolas', monospace; font-size: 9px; margin-right: 8px; }
                .marker { color: #8f84f2; font-weight: 700; margin-right: 8px; }
                .summary { color: #b9beca; }
                .empty { margin: 48px 10px 0 10px; color: #6d7485; }
                .emptyTitle { color: #bfc3cc; font-size: 13px; font-weight: 700; margin-bottom: 5px; }
                .emptyBody { color: #6d7485; }
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
            return (
                "Terminal is quiet.\n\n"
                "Managed command output will appear here when Loom runs a process."
            )
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
            return (
                "No workspace changes yet.\n\n"
                "The latest turn diff will appear here after Loom edits files."
            )
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
                return (
                    "Browser is idle.\n\n"
                    "Navigation and Browser tool activity will appear here when Loom uses the web."
                )
            return (
                "No agent coordination yet.\n\n"
                "Sub-agent control activity will appear here when Loom delegates work."
            )
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
        self.sandbox_label.setText(
            f"{'Sandboxed' if enforced else 'Not sandboxed'} · {backend}"
        )
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
            self._live_assistant[item_id] = (
                self._live_assistant.get(item_id, "") + _text(delta.get("text"))
            )
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
        self._append_activity(f"server · {line}", marker="!")

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
