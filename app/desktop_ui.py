from __future__ import annotations

import html
import json
import threading
from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import QObject, Qt, Signal
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


def _event_summary(event: dict[str, Any]) -> str:
    kind = _text(event.get("kind")) or "event"
    data = event.get("data") or {}
    if not isinstance(data, dict):
        data = {}
    if kind.startswith("tool_"):
        tool = _text(data.get("tool"))
        call_id = _text(data.get("call_id"))
        suffix = " · ".join(part for part in (tool, call_id[:12]) if part)
        return f"{kind}{' · ' + suffix if suffix else ''}"
    if kind.startswith("process_"):
        process_id = _text(data.get("process_id"))
        return f"{kind}{' · ' + process_id[:12] if process_id else ''}"
    if kind == "turn_diff_updated":
        paths = data.get("paths") or []
        return f"workspace diff · {len(paths)} path(s)"
    if kind == "model_requested":
        return f"model step {data.get('step', '?')}"
    if kind == "model_response":
        usage = data.get("usage") or {}
        total = usage.get("total_tokens") if isinstance(usage, dict) else None
        return f"model response{f' · {total} tokens' if total else ''}"
    return kind


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
        self.resize(1500, 920)
        self.setMinimumSize(1040, 680)

        root = QWidget(self)
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)
        self.setCentralWidget(root)

        self.main_splitter = QSplitter(Qt.Orientation.Horizontal, root)
        self.main_splitter.setObjectName("mainSplitter")
        root_layout.addWidget(self.main_splitter)

        self._build_sidebar()
        self._build_conversation_panel()
        self._build_activity_panel()
        self.main_splitter.setSizes([270, 850, 380])
        self.main_splitter.setStretchFactor(0, 0)
        self.main_splitter.setStretchFactor(1, 1)
        self.main_splitter.setStretchFactor(2, 0)

    def _build_sidebar(self) -> None:
        panel = QFrame()
        panel.setObjectName("sidebar")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(16, 18, 14, 16)
        layout.setSpacing(10)

        brand = QLabel("LOOM")
        brand.setObjectName("brandLabel")
        layout.addWidget(brand)
        subtitle = QLabel("Local Agent Workspace")
        subtitle.setObjectName("mutedLabel")
        layout.addWidget(subtitle)

        actions = QHBoxLayout()
        self.open_project_button = QPushButton("Open Project")
        self.open_project_button.setObjectName("openProjectButton")
        self.open_project_button.clicked.connect(self.choose_workspace)
        actions.addWidget(self.open_project_button)
        self.new_thread_button = QPushButton("New")
        self.new_thread_button.setObjectName("newThreadButton")
        self.new_thread_button.clicked.connect(self.new_thread_in_current_workspace)
        actions.addWidget(self.new_thread_button)
        layout.addLayout(actions)

        thread_header = QHBoxLayout()
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
        self.thread_list.currentItemChanged.connect(self._thread_selection_changed)
        layout.addWidget(self.thread_list, 1)

        self.protocol_label = QLabel("App Server · disconnected")
        self.protocol_label.setObjectName("mutedLabel")
        self.protocol_label.setWordWrap(True)
        layout.addWidget(self.protocol_label)
        self.main_splitter.addWidget(panel)

    def _build_conversation_panel(self) -> None:
        panel = QFrame()
        panel.setObjectName("conversationPanel")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(22, 18, 22, 18)
        layout.setSpacing(12)

        header = QFrame()
        header.setObjectName("workspaceHeader")
        header_layout = QVBoxLayout(header)
        header_layout.setContentsMargins(14, 10, 14, 10)
        header_layout.setSpacing(5)

        first_line = QHBoxLayout()
        self.workspace_label = QLabel(_short_path(self.default_workspace))
        self.workspace_label.setObjectName("workspaceTitle")
        first_line.addWidget(self.workspace_label)
        first_line.addStretch(1)
        self.status_label = QLabel("idle")
        self.status_label.setObjectName("statusChip")
        first_line.addWidget(self.status_label)
        self.permission_label = QLabel(self.default_permission_mode)
        self.permission_label.setObjectName("statusChip")
        first_line.addWidget(self.permission_label)
        header_layout.addLayout(first_line)

        second_line = QHBoxLayout()
        self.workspace_path_label = QLabel(str(self.default_workspace))
        self.workspace_path_label.setObjectName("mutedLabel")
        self.workspace_path_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        second_line.addWidget(self.workspace_path_label, 1)
        self.usage_label = QLabel("0 tokens")
        self.usage_label.setObjectName("mutedLabel")
        second_line.addWidget(self.usage_label)
        header_layout.addLayout(second_line)
        layout.addWidget(header)

        self.transcript = QTextBrowser()
        self.transcript.setObjectName("transcript")
        self.transcript.setOpenExternalLinks(False)
        self.transcript.setPlaceholderText("Start a thread and give Loom a task.")
        layout.addWidget(self.transcript, 1)

        self.approval_frame = QFrame()
        self.approval_frame.setObjectName("approvalCard")
        approval_layout = QVBoxLayout(self.approval_frame)
        approval_layout.setContentsMargins(14, 12, 14, 12)
        approval_layout.setSpacing(8)
        self.approval_title = QLabel("Approval required")
        self.approval_title.setObjectName("approvalTitle")
        approval_layout.addWidget(self.approval_title)
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
        self.allow_button = QPushButton("Allow")
        self.allow_button.setObjectName("allowButton")
        self.allow_button.clicked.connect(lambda: self.respond_approval(True))
        approval_actions.addWidget(self.allow_button)
        approval_layout.addLayout(approval_actions)
        self.approval_frame.hide()
        layout.addWidget(self.approval_frame)

        composer_frame = QFrame()
        composer_frame.setObjectName("composerFrame")
        composer_layout = QVBoxLayout(composer_frame)
        composer_layout.setContentsMargins(12, 10, 12, 10)
        composer_layout.setSpacing(8)
        self.composer = ComposerTextEdit()
        self.composer.setObjectName("composer")
        self.composer.setPlaceholderText("Ask Loom to inspect, edit, run, debug, browse, or coordinate…")
        self.composer.setMaximumHeight(150)
        self.composer.setMinimumHeight(72)
        self.composer.sendRequested.connect(self.send_prompt)
        composer_layout.addWidget(self.composer)
        compose_actions = QHBoxLayout()
        hint = QLabel("Enter to send · Shift+Enter for newline")
        hint.setObjectName("mutedLabel")
        compose_actions.addWidget(hint)
        compose_actions.addStretch(1)
        self.stop_button = QPushButton("Stop")
        self.stop_button.setObjectName("stopButton")
        self.stop_button.clicked.connect(self.interrupt_turn)
        self.stop_button.setEnabled(False)
        compose_actions.addWidget(self.stop_button)
        self.send_button = QPushButton("Send")
        self.send_button.setObjectName("sendButton")
        self.send_button.clicked.connect(self.send_prompt)
        compose_actions.addWidget(self.send_button)
        composer_layout.addLayout(compose_actions)
        layout.addWidget(composer_frame)
        self.main_splitter.addWidget(panel)

    def _build_activity_panel(self) -> None:
        panel = QFrame()
        panel.setObjectName("activityPanel")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(14, 18, 16, 16)
        layout.setSpacing(10)

        title_row = QHBoxLayout()
        title = QLabel("RUNTIME")
        title.setObjectName("sectionLabel")
        title_row.addWidget(title)
        title_row.addStretch(1)
        self.sandbox_label = QLabel("Sandbox: no process yet")
        self.sandbox_label.setObjectName("mutedLabel")
        title_row.addWidget(self.sandbox_label)
        layout.addLayout(title_row)

        self.activity_tabs = QTabWidget()
        self.activity_tabs.setObjectName("activityTabs")
        self.activity_view = self._read_only_panel("activityView")
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
            QMainWindow, QWidget { background: #0a0a0d; color: #ececf2; }
            QFrame#sidebar, QFrame#activityPanel { background: #0e0e13; }
            QFrame#conversationPanel { background: #0b0b0f; }
            QLabel#brandLabel { font-size: 20px; font-weight: 800; letter-spacing: 4px; color: #f2f0ff; }
            QLabel#sectionLabel { font-size: 11px; font-weight: 700; letter-spacing: 1.5px; color: #9c97b7; }
            QLabel#mutedLabel { color: #777386; font-size: 11px; }
            QLabel#workspaceTitle { font-size: 18px; font-weight: 700; }
            QLabel#statusChip { background: #191821; border: 1px solid #292634; border-radius: 9px; padding: 3px 8px; color: #c9c4de; }
            QFrame#workspaceHeader, QFrame#composerFrame { background: #111117; border: 1px solid #22212a; border-radius: 12px; }
            QFrame#approvalCard { background: #17131e; border: 1px solid #5b3d7d; border-radius: 12px; }
            QLabel#approvalTitle { font-size: 13px; font-weight: 700; color: #e2d2ff; }
            QLabel#approvalDetails { color: #bbb4c9; }
            QListWidget#threadList, QTextBrowser#transcript, QPlainTextEdit { background: #0d0d12; border: 1px solid #1e1d25; border-radius: 10px; selection-background-color: #42305f; }
            QListWidget#threadList::item { border-radius: 8px; padding: 9px 8px; margin: 2px 0; }
            QListWidget#threadList::item:selected { background: #211a2e; color: #f0eaff; }
            QTextEdit#composer { background: transparent; border: none; color: #f0eff5; selection-background-color: #4c356b; }
            QPushButton { background: #18171f; border: 1px solid #2a2833; border-radius: 8px; padding: 7px 11px; color: #d4d1dc; }
            QPushButton:hover { background: #211f2a; border-color: #41394e; }
            QPushButton:disabled { color: #56535e; background: #111116; border-color: #1b1a20; }
            QPushButton#sendButton, QPushButton#allowButton { background: #6d4ca1; border-color: #8060b4; color: white; font-weight: 700; }
            QPushButton#sendButton:hover, QPushButton#allowButton:hover { background: #7d5ab3; }
            QPushButton#denyButton, QPushButton#stopButton { background: #17151a; }
            QPushButton#iconButton { padding: 3px 7px; min-width: 20px; }
            QTabWidget::pane { border: 1px solid #1f1e27; border-radius: 9px; top: -1px; }
            QTabBar::tab { background: #111117; color: #777386; padding: 7px 9px; border: none; }
            QTabBar::tab:selected { color: #e5def1; border-bottom: 2px solid #7f5bae; }
            QSplitter::handle { background: #18171e; width: 1px; }
            QScrollBar:vertical { background: transparent; width: 10px; }
            QScrollBar::handle:vertical { background: #2a2832; border-radius: 5px; min-height: 28px; }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
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
            f"Provider streaming: {'on' if streaming else 'fallback'}"
        )

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

    def _thread_selection_changed(self, current: QListWidgetItem | None, _previous: QListWidgetItem | None) -> None:
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
        self._append_activity(f"RPC error · {message}")
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
            title = _text(record.get("title")).strip() or "New thread"
            workspace = _short_path(record.get("workspace"))
            status = _text(record.get("status")) or "idle"
            item = QListWidgetItem(f"{title}\n{workspace} · {status}")
            item.setToolTip(_text(record.get("workspace")))
            item.setData(_THREAD_ROLE, record)
            self.thread_list.addItem(item)
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

        self.workspace_label.setText(_short_path(self.current_workspace))
        self.workspace_path_label.setText(self.current_workspace)
        self.permission_label.setText(_text(thread.get("permissionMode")) or self.default_permission_mode)
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
        self.status_label.setText(status)
        active = status in {"running", "starting", "waiting_approval"}
        self.stop_button.setEnabled(active and bool(self.current_thread_id))
        self.send_button.setEnabled(bool(self.current_thread_id) and not active)

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
        document = """
            <style>
                body { color: #e9e7ef; font-family: 'Segoe UI', sans-serif; margin: 8px; }
                .row { margin: 8px 0 14px 0; }
                .meta { color: #777386; font-size: 10px; margin-bottom: 4px; letter-spacing: 1px; }
                .bubble { background: #121218; border: 1px solid #23212b; border-radius: 10px; padding: 11px 13px; white-space: pre-wrap; }
                .user .bubble { background: #17131f; border-color: #2d2637; }
                .stream { color: #bca9d6; font-size: 10px; }
            </style>
        """ + "".join(cards)
        self.transcript.setHtml(document)
        cursor = self.transcript.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        self.transcript.setTextCursor(cursor)

    @staticmethod
    def _message_card(role: str, content: str, *, streaming: bool) -> str:
        label = "YOU" if role == "user" else "LOOM"
        safe = html.escape(content).replace("\n", "<br>")
        live = " <span class='stream'>· live</span>" if streaming else ""
        return f"<div class='row {role}'><div class='meta'>{label}{live}</div><div class='bubble'>{safe}</div></div>"

    def _render_runtime_panels(self, snapshot: dict[str, Any]) -> None:
        events = snapshot.get("events") or []
        activity_lines = []
        for event in events[-300:]:
            if not isinstance(event, dict):
                continue
            when = _text(event.get("createdAt"))
            activity_lines.append(f"{when[-12:]}  {_event_summary(event)}")
        self.activity_view.setPlainText("\n".join(activity_lines) or "No runtime activity yet.")
        self.activity_view.moveCursor(QTextCursor.MoveOperation.End)

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
                    if tool in {"spawn_agent", "send_agent_message", "wait_agent", "list_agents", "close_agent"}:
                        agent_items.append(item)

        self.terminal_view.setPlainText(self._process_text(process_items))
        self.diff_view.setPlainText(self._diff_text(diff_items))
        self.browser_view.setPlainText(self._tool_activity_text(browser_items, "browser"))
        self.agents_view.setPlainText(self._tool_activity_text(agent_items, "agent"))
        self._update_sandbox_status(process_items)

    @staticmethod
    def _process_text(items: list[dict[str, Any]]) -> str:
        if not items:
            return "No managed process activity in this thread yet."
        chunks: list[str] = []
        for item in items[-20:]:
            argv = item.get("argv") or []
            command = " ".join(str(part) for part in argv)
            header = f"[{_text(item.get('status'))}] {_text(item.get('processId'))}\n{command}\n{_text(item.get('cwd'))}"
            output = _text(item.get("stdout"))
            error = _text(item.get("stderr"))
            if output:
                header += f"\n\nstdout:\n{output[-8000:]}"
            if error:
                header += f"\n\nstderr:\n{error[-8000:]}"
            chunks.append(header)
        return "\n\n────────────────────────\n\n".join(chunks)

    @staticmethod
    def _diff_text(items: list[dict[str, Any]]) -> str:
        if not items:
            return "No workspace diff has been emitted in this thread yet."
        latest = items[-1]
        paths = latest.get("paths") or []
        prefix = "Paths:\n" + "\n".join(f"  {path}" for path in paths)
        diff = _text(latest.get("diff"))
        if latest.get("truncated"):
            prefix += "\n(diff truncated by Runtime)"
        return prefix + (f"\n\n{diff}" if diff else "")

    @staticmethod
    def _tool_activity_text(items: list[dict[str, Any]], category: str) -> str:
        if not items:
            if category == "browser":
                return (
                    "No Browser tool activity in this thread yet.\n\n"
                    "App Server v1 exposes Browser activity through normal tool events; "
                    "a dedicated live Browser snapshot is not claimed here."
                )
            return (
                "No AgentGraph control activity in this thread yet.\n\n"
                "This panel reflects protocol-visible Agent control tools; a dedicated "
                "tree snapshot method is not part of App Server v1 yet."
            )
        chunks = []
        for item in items[-30:]:
            chunks.append(
                f"[{_text(item.get('status'))}] {_text(item.get('toolName'))}\n"
                f"{_pretty(item.get('arguments') or {})}"
            )
        return "\n\n".join(chunks)

    def _update_sandbox_status(self, processes: list[dict[str, Any]]) -> None:
        sandbox: dict[str, Any] | None = None
        for item in reversed(processes):
            value = item.get("sandbox")
            if isinstance(value, dict) and value:
                sandbox = value
                break
        if sandbox is None:
            self.sandbox_label.setText("Sandbox: no process yet")
            return
        enforced = bool(sandbox.get("enforced"))
        backend = _text(sandbox.get("backend")) or "none"
        self.sandbox_label.setText(f"Sandbox: {'on' if enforced else 'not enforced'} · {backend}")

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

    def _append_activity(self, text: str) -> None:
        current = self.activity_view.toPlainText()
        if current == "No runtime activity yet.":
            current = ""
        lines = current.splitlines()[-299:] if current else []
        lines.append(text)
        self.activity_view.setPlainText("\n".join(lines))
        self.activity_view.moveCursor(QTextCursor.MoveOperation.End)

    def _on_server_stderr(self, line: str) -> None:
        self._append_activity(f"server · {line}")

    def _on_server_exit(self, message: str) -> None:
        self.protocol_label.setText("App Server · stopped")
        self.send_button.setEnabled(False)
        self.stop_button.setEnabled(False)
        self._append_activity(message)
        if self.isVisible() and not self._closed:
            QMessageBox.critical(self, "Loom App Server stopped", message)

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt override
        self._closed = True
        close = getattr(self.client, "close", None)
        if callable(close):
            close()
        event.accept()


__all__ = ["ComposerTextEdit", "DesktopEventBridge", "LoomDesktopWindow"]
