"""The Loom desktop window.

A single window class over the App Server protocol. It owns no runtime, no
model loop, no permission engine and no provider credentials; it reads durable
thread state and renders observable Runtime activity.
"""

from __future__ import annotations

import html
from pathlib import Path
from typing import Any

from PySide6.QtCore import QEvent, QObject, Qt, QTimer
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QTabWidget,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from app.desktop import format as fmt
from app.desktop import theme
from app.desktop.rpc import DesktopEventBridge, RpcRunner, connect_client
from app.desktop.state import ThreadState
from app.desktop.widgets import (
    ApprovalCard,
    Banner,
    CardListView,
    ComposerTextEdit,
    DiffView,
    EmptyState,
    ThreadGroupHeader,
    ThreadListItemWidget,
    TranscriptView,
    repolish,
    thread_row_size,
)


THREAD_ROLE = Qt.ItemDataRole.UserRole
GROUP_ROLE = Qt.ItemDataRole.UserRole + 1

PERMISSION_MODES = ("read-only", "approval", "workspace", "full-access")

# One coalescing window for the "something changed, re-read the thread" work
# that notifications trigger in bursts.
RECONCILE_DELAY_MS = 90
THREAD_LIST_DELAY_MS = 250


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
            default_permission_mode or runtime_info.get("defaultPermissionMode") or "approval"
        )

        self.state = ThreadState()
        self.current_workspace = str(self.default_workspace)
        self.current_turn_id = ""
        self._thread_view = "active"
        self._thread_counts = {"active": 0, "archived": 0, "all": 0}
        self._thread_management_supported = False
        self._draft_workspace: Path | None = None
        self._pending_prompt = ""
        self._closed = False
        self._activity_tail: list[tuple[str, str, str]] = []
        self._sidebar_visible = True
        self._runtime_visible = True

        self.bridge = DesktopEventBridge(self)
        self.rpc = RpcRunner(self.bridge)
        self.bridge.notification.connect(self._on_notification)
        self.bridge.stderr.connect(self._on_server_stderr)
        self.bridge.serverExited.connect(self._on_server_exit)
        self.bridge.rpcResult.connect(self._on_rpc_result)
        self.bridge.rpcError.connect(self._on_rpc_error)
        connect_client(self.client, self.bridge)

        self._reconcile_timer = QTimer(self)
        self._reconcile_timer.setSingleShot(True)
        self._reconcile_timer.setInterval(RECONCILE_DELAY_MS)
        self._reconcile_timer.timeout.connect(self._reconcile_now)

        self._thread_list_timer = QTimer(self)
        self._thread_list_timer.setSingleShot(True)
        self._thread_list_timer.setInterval(THREAD_LIST_DELAY_MS)
        self._thread_list_timer.timeout.connect(self.refresh_threads)

        self._build_ui()
        self.setStyleSheet(theme.stylesheet())
        self._apply_initialization()
        self.refresh_threads()

    @property
    def current_thread_id(self) -> str:
        return self.state.thread_id

    @property
    def current_snapshot(self) -> dict[str, Any]:
        return self.state.snapshot

    # ------------------------------------------------------------------
    # construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        self.setObjectName("loomDesktopWindow")
        self.setWindowTitle("Loom")
        self.resize(1680, 1020)
        self.setMinimumSize(1180, 740)

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
        self._build_conversation()
        self._build_runtime_panel()
        self.main_splitter.setSizes([288, 1020, 372])
        self.main_splitter.setStretchFactor(0, 0)
        self.main_splitter.setStretchFactor(1, 1)
        self.main_splitter.setStretchFactor(2, 0)

        for button in self.findChildren(QPushButton):
            button.setCursor(Qt.CursorShape.PointingHandCursor)

        self.find_shortcut = QShortcut(QKeySequence("Ctrl+F"), self)
        self.find_shortcut.activated.connect(self._focus_thread_search)
        self.new_thread_shortcut = QShortcut(QKeySequence("Ctrl+N"), self)
        self.new_thread_shortcut.activated.connect(self.new_thread_in_current_workspace)
        self.rename_shortcut = QShortcut(QKeySequence("F2"), self.thread_list)
        self.rename_shortcut.activated.connect(self._rename_selected_thread)
        self.delete_shortcut = QShortcut(QKeySequence("Delete"), self.thread_list)
        self.delete_shortcut.activated.connect(self._delete_selected_thread)

    def _build_sidebar(self) -> None:
        self.sidebar_panel = QFrame()
        self.sidebar_panel.setObjectName("sidebar")
        self.sidebar_panel.setMinimumWidth(262)
        self.sidebar_panel.setMaximumWidth(350)
        layout = QVBoxLayout(self.sidebar_panel)
        layout.setContentsMargins(18, 20, 14, 14)
        layout.setSpacing(14)

        brand = QHBoxLayout()
        brand.setSpacing(10)
        mark = QLabel("L")
        mark.setObjectName("brandMark")
        mark.setAlignment(Qt.AlignmentFlag.AlignCenter)
        mark.setFixedSize(34, 34)
        brand.addWidget(mark)
        titles = QVBoxLayout()
        titles.setSpacing(0)
        self.brand_label = QLabel("Loom")
        self.brand_label.setObjectName("brandLabel")
        titles.addWidget(self.brand_label)
        subtitle = QLabel("Local agent workspace")
        subtitle.setObjectName("brandSubtitle")
        titles.addWidget(subtitle)
        brand.addLayout(titles, 1)
        layout.addLayout(brand)

        actions = QHBoxLayout()
        actions.setSpacing(7)
        self.new_thread_button = QPushButton("+  New thread")
        self.new_thread_button.setObjectName("newThreadButton")
        self.new_thread_button.setToolTip("Create a new thread in this workspace (Ctrl+N)")
        self.new_thread_button.clicked.connect(self.new_thread_in_current_workspace)
        actions.addWidget(self.new_thread_button, 1)
        self.open_project_button = QPushButton("Open…")
        self.open_project_button.setObjectName("openProjectButton")
        self.open_project_button.setToolTip("Open another project or workspace")
        self.open_project_button.clicked.connect(self.choose_workspace)
        actions.addWidget(self.open_project_button)
        layout.addLayout(actions)

        self.thread_library_toolbar = QFrame()
        self.thread_library_toolbar.setObjectName("threadLibraryToolbar")
        toolbar = QHBoxLayout(self.thread_library_toolbar)
        toolbar.setContentsMargins(0, 0, 0, 0)
        toolbar.setSpacing(6)
        self.thread_search = QLineEdit()
        self.thread_search.setObjectName("threadSearch")
        self.thread_search.setPlaceholderText("Search conversations")
        self.thread_search.setClearButtonEnabled(True)
        self.thread_search.textChanged.connect(self._apply_thread_filter)
        toolbar.addWidget(self.thread_search, 1)
        self.archive_view_button = QPushButton("Archived")
        self.archive_view_button.setObjectName("archiveViewButton")
        self.archive_view_button.setCheckable(True)
        self.archive_view_button.setToolTip("Show archived conversations")
        self.archive_view_button.clicked.connect(self._toggle_archive_view)
        toolbar.addWidget(self.archive_view_button)
        self.thread_actions_button = QPushButton("•••")
        self.thread_actions_button.setObjectName("threadActionsButton")
        self.thread_actions_button.setToolTip("Conversation actions")
        self.thread_actions_button.setFixedWidth(34)
        self.thread_actions_button.clicked.connect(self._show_selected_thread_menu)
        toolbar.addWidget(self.thread_actions_button)
        layout.addWidget(self.thread_library_toolbar)

        header = QHBoxLayout()
        header.setContentsMargins(2, 2, 0, 0)
        self.thread_section_label = QLabel("CHATS")
        self.thread_section_label.setObjectName("sectionLabel")
        header.addWidget(self.thread_section_label)
        header.addStretch(1)
        self.refresh_button = QPushButton("↻")
        self.refresh_button.setObjectName("iconButton")
        self.refresh_button.setToolTip("Refresh durable threads")
        self.refresh_button.clicked.connect(self.refresh_threads)
        header.addWidget(self.refresh_button)
        layout.addLayout(header)

        self.thread_list = QListWidget()
        self.thread_list.setObjectName("threadList")
        self.thread_list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.thread_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.thread_list.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.thread_list.setUniformItemSizes(False)
        self.thread_list.setSpacing(2)
        self.thread_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.thread_list.customContextMenuRequested.connect(self._show_thread_context_menu)
        self.thread_list.currentItemChanged.connect(self._thread_selection_changed)
        layout.addWidget(self.thread_list, 1)

        connection = QHBoxLayout()
        connection.setContentsMargins(4, 2, 4, 0)
        connection.setSpacing(7)
        self.connection_dot = QLabel("●")
        self.connection_dot.setObjectName("connectionDot")
        self.connection_dot.setProperty("state", "connected")
        connection.addWidget(self.connection_dot)
        self.protocol_label = QLabel("Connecting…")
        self.protocol_label.setObjectName("protocolLabel")
        connection.addWidget(self.protocol_label, 1)
        layout.addLayout(connection)

        self.main_splitter.addWidget(self.sidebar_panel)

    def _build_conversation(self) -> None:
        panel = QFrame()
        panel.setObjectName("conversationPanel")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(30, 20, 30, 20)
        layout.setSpacing(14)

        # One line: panel toggles, the conversation's name, and its state. The
        # project is named by the composer's workspace chip and the sidebar
        # grouping, so the header no longer repeats it twice more.
        header = QFrame()
        header.setObjectName("workspaceHeader")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(1, 0, 1, 12)
        header_layout.setSpacing(8)

        self.sidebar_toggle_button = QPushButton("☰")
        self.sidebar_toggle_button.setObjectName("panelToggle")
        self.sidebar_toggle_button.setToolTip("Show or hide the conversation sidebar")
        self.sidebar_toggle_button.clicked.connect(self.toggle_sidebar)
        header_layout.addWidget(self.sidebar_toggle_button)

        self.thread_title_label = QLabel("New conversation")
        self.thread_title_label.setObjectName("threadTitle")
        self.thread_title_label.setTextFormat(Qt.TextFormat.PlainText)
        header_layout.addWidget(self.thread_title_label, 1)

        self.status_label = QLabel("Idle")
        self.status_label.setObjectName("statusChip")
        self.status_label.setProperty("state", "idle")
        header_layout.addWidget(self.status_label)

        self.runtime_toggle_button = QPushButton("Runtime")
        self.runtime_toggle_button.setObjectName("panelToggle")
        self.runtime_toggle_button.setToolTip("Show or hide the Runtime inspector")
        self.runtime_toggle_button.clicked.connect(self.toggle_runtime)
        header_layout.addWidget(self.runtime_toggle_button)
        layout.addWidget(header)

        # Kept as the canonical place to read the full workspace path.
        self.workspace_label = QLabel(fmt.short_path(self.default_workspace))
        self.workspace_label.setObjectName("projectName")
        self.workspace_path_label = QLabel(str(self.default_workspace))
        self.workspace_path_label.setObjectName("mutedLabel")
        self.workspace_label.hide()
        self.workspace_path_label.hide()

        # Banner, approval and composer share the transcript's measure so the
        # column reads as one thing instead of three widths.
        column = TranscriptView.MAX_CONTENT_WIDTH + 18
        self.banner = Banner()
        self.banner.setMaximumWidth(column)
        layout.addWidget(self.banner, 0, Qt.AlignmentFlag.AlignHCenter)

        self.empty_state = EmptyState()
        self.empty_state.promptChosen.connect(self._fill_composer)
        layout.addWidget(self.empty_state, 1)

        self.transcript = TranscriptView()
        self.transcript.hide()
        layout.addWidget(self.transcript, 1)

        self.approval_frame = ApprovalCard()
        self.approval_frame.responded.connect(self.respond_approval)
        self.approval_frame.setMaximumWidth(column)
        layout.addWidget(self.approval_frame, 0, Qt.AlignmentFlag.AlignHCenter)
        # Familiar aliases so callers can reach the card's parts directly.
        self.approval_title = self.approval_frame.title_label
        self.approval_details = self.approval_frame.details_label
        self.allow_button = self.approval_frame.allow_button
        self.deny_button = self.approval_frame.deny_button

        self.composer_frame = QFrame()
        self.composer_frame.setObjectName("composerFrame")
        self.composer_frame.setProperty("focused", False)
        composer_layout = QVBoxLayout(self.composer_frame)
        composer_layout.setContentsMargins(16, 13, 12, 11)
        composer_layout.setSpacing(7)

        self.composer = ComposerTextEdit()
        self.composer.setObjectName("composer")
        self.composer.setPlaceholderText("Message Loom…")
        self.composer.setMinimumHeight(68)
        self.composer.setMaximumHeight(220)
        self.composer.sendRequested.connect(self.send_prompt)
        self.composer.textChanged.connect(self._sync_composer_height)
        self.composer.installEventFilter(self)
        composer_layout.addWidget(self.composer)

        bar = QHBoxLayout()
        bar.setSpacing(7)

        self.workspace_button = QPushButton(fmt.short_path(self.default_workspace))
        self.workspace_button.setObjectName("composerControl")
        self.workspace_button.setToolTip("Change the project this conversation works in")
        self.workspace_button.clicked.connect(self.choose_workspace)
        bar.addWidget(self.workspace_button)

        # The permission mode was a read-only chip; it decides what Loom is
        # allowed to do, so it belongs where it can be changed.
        self.permission_label = QPushButton(self.default_permission_mode)
        self.permission_label.setObjectName("composerControl")
        self.permission_label.setProperty("mode", self.default_permission_mode)
        self.permission_label.setToolTip("Permission mode for new conversations")
        self.permission_label.clicked.connect(self._show_permission_menu)
        bar.addWidget(self.permission_label)

        bar.addStretch(1)

        self.usage_label = QLabel("0 tokens")
        self.usage_label.setObjectName("composerHint")
        bar.addWidget(self.usage_label)
        self.composer_state_label = QLabel("Ready")
        self.composer_state_label.setObjectName("composerState")
        bar.addWidget(self.composer_state_label)
        self.stop_button = QPushButton("Stop")
        self.stop_button.setObjectName("stopButton")
        self.stop_button.setEnabled(False)
        self.stop_button.clicked.connect(self.interrupt_turn)
        bar.addWidget(self.stop_button)
        self.send_button = QPushButton("↑")
        self.send_button.setObjectName("sendButton")
        self.send_button.setToolTip("Send  ·  Enter")
        self.send_button.setFixedSize(34, 34)
        self.send_button.clicked.connect(self.send_prompt)
        bar.addWidget(self.send_button)
        composer_layout.addLayout(bar)
        self.composer_frame.setSizePolicy(
            self.composer_frame.sizePolicy().horizontalPolicy(),
            QSizePolicy.Policy.Fixed,
        )
        self._sync_composer_height()
        self.composer_frame.setMaximumWidth(column)
        layout.addWidget(self.composer_frame, 0, Qt.AlignmentFlag.AlignHCenter)

        self.main_splitter.addWidget(panel)

    def _build_runtime_panel(self) -> None:
        self.activity_panel = QFrame()
        self.activity_panel.setObjectName("activityPanel")
        self.activity_panel.setMinimumWidth(330)
        self.activity_panel.setMaximumWidth(460)
        layout = QVBoxLayout(self.activity_panel)
        layout.setContentsMargins(18, 20, 16, 14)
        layout.setSpacing(12)

        title_row = QHBoxLayout()
        titles = QVBoxLayout()
        titles.setSpacing(0)
        title = QLabel("Runtime")
        title.setObjectName("inspectorTitle")
        titles.addWidget(title)
        subtitle = QLabel("Execution, changes, and delegated work")
        subtitle.setObjectName("mutedLabel")
        titles.addWidget(subtitle)
        title_row.addLayout(titles)
        title_row.addStretch(1)
        self.sandbox_label = QLabel("No process")
        self.sandbox_label.setObjectName("sandboxChip")
        self.sandbox_label.setProperty("state", "idle")
        title_row.addWidget(self.sandbox_label, 0, Qt.AlignmentFlag.AlignTop)
        layout.addLayout(title_row)

        self.activity_tabs = QTabWidget()
        self.activity_tabs.setObjectName("activityTabs")
        self.activity_tabs.setDocumentMode(True)
        self.activity_tabs.setUsesScrollButtons(False)
        tab_bar = self.activity_tabs.tabBar()
        tab_bar.setExpanding(False)
        # Eliding turns these into "Acti… Termi… …"; the panel is wide enough
        # for the full labels once the tab padding is tight.
        tab_bar.setElideMode(Qt.TextElideMode.ElideNone)
        self.activity_view = QTextBrowser()
        self.activity_view.setObjectName("activityView")
        self.activity_view.setOpenExternalLinks(False)
        self.activity_view.setFrameShape(QFrame.Shape.NoFrame)
        self.activity_view.document().setDocumentMargin(0)
        self.terminal_view = CardListView(
            "process",
            empty_title="Terminal is quiet",
            empty_body="Managed command output appears here when Loom runs a process.",
        )
        self.diff_view = DiffView()
        self.browser_view = CardListView(
            "tool",
            empty_title="Browser is idle",
            empty_body="Navigation and Browser tool activity appears here when Loom uses the web.",
        )
        self.agents_view = CardListView(
            "tool",
            empty_title="No delegated work",
            empty_body="Sub-agent coordination appears here when Loom spawns an agent.",
        )
        self.activity_tabs.addTab(self.activity_view, "Activity")
        self.activity_tabs.addTab(self.terminal_view, "Terminal")
        self.activity_tabs.addTab(self.diff_view, "Diff")
        self.activity_tabs.addTab(self.browser_view, "Browser")
        self.activity_tabs.addTab(self.agents_view, "Agents")
        layout.addWidget(self.activity_tabs, 1)

        # Tab widths depend on the installed UI font, so let the tab bar decide
        # how narrow this panel may get instead of guessing a constant.
        margins = layout.contentsMargins()
        self.activity_panel.setMinimumWidth(
            max(
                self.activity_panel.minimumWidth(),
                tab_bar.sizeHint().width() + margins.left() + margins.right() + 8,
            )
        )

        self.main_splitter.addWidget(self.activity_panel)

    def _apply_initialization(self) -> None:
        protocol = self.initialization.get("protocolVersion", "?")
        server = self.initialization.get("serverInfo") or {}
        capabilities = self.initialization.get("capabilities") or {}
        streaming = bool(capabilities.get("providerStreaming"))
        management = bool(capabilities.get("threadManagement"))
        self._thread_management_supported = management

        self.protocol_label.setText("Connected")
        self.protocol_label.setToolTip(
            f"App Server v{fmt.text(server.get('version')) or '?'} · protocol {protocol}\n"
            f"Provider streaming · {'on' if streaming else 'fallback'}\n"
            f"Conversation management · {'on' if management else 'unavailable'}"
        )
        self.connection_dot.setProperty("state", "connected")
        repolish(self.connection_dot)

        self.archive_view_button.setEnabled(management)
        self.archive_view_button.setToolTip(
            "Show archived conversations"
            if management
            else "Update Loom App Server to manage conversations"
        )
        self.thread_actions_button.setEnabled(False)

    # ------------------------------------------------------------------
    # panel chrome
    # ------------------------------------------------------------------

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802 - Qt override
        if watched is self.composer:
            if event.type() == QEvent.Type.FocusIn:
                self.composer_frame.setProperty("focused", True)
                repolish(self.composer_frame)
            elif event.type() == QEvent.Type.FocusOut:
                self.composer_frame.setProperty("focused", False)
                repolish(self.composer_frame)
        return super().eventFilter(watched, event)

    def _sync_composer_height(self) -> None:
        document = self.composer.document()
        height = document.size().height() + document.documentMargin() * 2 + 12
        self.composer.setFixedHeight(int(max(68.0, min(height, 220.0))))

    def toggle_sidebar(self) -> None:
        self._sidebar_visible = not self._sidebar_visible
        self.sidebar_panel.setVisible(self._sidebar_visible)
        self.sidebar_toggle_button.setProperty("active", self._sidebar_visible)
        repolish(self.sidebar_toggle_button)

    def toggle_runtime(self) -> None:
        self._runtime_visible = not self._runtime_visible
        self.activity_panel.setVisible(self._runtime_visible)
        self.runtime_toggle_button.setProperty("active", self._runtime_visible)
        repolish(self.runtime_toggle_button)

    def _fill_composer(self, value: str) -> None:
        self.composer.setPlainText(value)
        self.composer.setFocus()
        cursor = self.composer.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        self.composer.setTextCursor(cursor)

    def _show_permission_menu(self) -> None:
        """Pick the mode new conversations start in.

        The App Server sets a thread's mode when it is created, so changing it
        applies to the next conversation rather than rewriting this one.
        """
        menu = QMenu(self)
        current = self.permission_label.text()
        chosen_action = None
        for mode in PERMISSION_MODES:
            action = menu.addAction(mode)
            action.setCheckable(True)
            action.setChecked(mode == current)
            if mode == current:
                chosen_action = action
        picked = menu.exec(
            self.permission_label.mapToGlobal(self.permission_label.rect().topLeft())
        )
        if picked is None or picked is chosen_action:
            return
        self.default_permission_mode = picked.text()
        self._set_permission_display(self.default_permission_mode)
        self._append_activity(
            f"New conversations will use {self.default_permission_mode}", marker="•"
        )

    def _set_workspace_display(self, workspace: str) -> None:
        workspace = fmt.text(workspace)
        self.workspace_button.setText(fmt.short_path(workspace))
        self.workspace_button.setToolTip(workspace or "No workspace")
        self.workspace_label.setText(fmt.short_path(workspace))
        self.workspace_path_label.setText(workspace)

    def _set_permission_display(self, mode: str) -> None:
        mode = fmt.text(mode) or self.default_permission_mode
        self.permission_label.setText(mode)
        if self.permission_label.property("mode") != mode:
            self.permission_label.setProperty("mode", mode)
            repolish(self.permission_label)

    def notify(self, message: str) -> None:
        """Surface a problem inline instead of interrupting with a dialog."""
        self.banner.show_message(message)

    # ------------------------------------------------------------------
    # thread library
    # ------------------------------------------------------------------

    def refresh_threads(self) -> None:
        self._thread_list_timer.stop()
        if self._thread_view == "active":
            self.rpc.submit("threads", lambda: self.client.thread_list(limit=200))
            return
        request = getattr(self.client, "request", None)
        if not callable(request):
            self._apply_thread_list({"threads": [], "view": "archived"})
            return
        self.rpc.submit(
            "threads",
            lambda: dict(request("thread/list", {"limit": 200, "view": "archived"})),
        )

    def schedule_thread_refresh(self) -> None:
        if not self._thread_list_timer.isActive():
            self._thread_list_timer.start()

    def choose_workspace(self) -> None:
        selected = QFileDialog.getExistingDirectory(
            self, "Open Loom workspace", self.current_workspace or str(self.default_workspace)
        )
        if selected:
            self.begin_draft(Path(selected))
            self.refresh_threads()

    def new_thread_in_current_workspace(self) -> None:
        self.begin_draft(Path(self.current_workspace or self.default_workspace))

    def begin_draft(self, workspace: Path) -> None:
        """Open an unsent conversation.

        The thread is only created on the server once there is something to
        say, so opening the app or clicking "New thread" and walking away no
        longer leaves an empty conversation in the library forever.
        """
        self._draft_workspace = workspace.expanduser().resolve()
        self.current_workspace = str(self._draft_workspace)
        self.state.reset()
        self.current_turn_id = ""
        self.approval_frame.dismiss()
        self.thread_list.blockSignals(True)
        self.thread_list.setCurrentItem(None)
        self.thread_list.blockSignals(False)

        self.thread_title_label.setText("New conversation")
        self._set_workspace_display(self.current_workspace)
        self._set_permission_display(self.default_permission_mode)
        self.usage_label.setText("0 tokens")
        self._render_transcript()
        self._render_runtime_panels()
        self._set_status("idle")
        self.composer.setFocus()

    def _create_thread(self, workspace: Path) -> None:
        workspace = workspace.expanduser().resolve()
        self.rpc.submit(
            f"new:{workspace}",
            lambda: self.client.thread_start(
                workspace=workspace, permission_mode=self.default_permission_mode
            ),
        )

    def _thread_selection_changed(
        self, current: QListWidgetItem | None, _previous: QListWidgetItem | None
    ) -> None:
        self._sync_thread_actions()
        if current is None:
            return
        record = current.data(THREAD_ROLE) or {}
        thread_id = fmt.text(record.get("id")).strip()
        if thread_id and thread_id != self.state.thread_id:
            self.load_thread(thread_id)

    def load_thread(self, thread_id: str) -> None:
        thread_id = fmt.text(thread_id).strip()
        if not thread_id:
            return
        # One tag for all snapshot reads: a newer selection supersedes an older
        # in-flight one instead of racing it onto the screen.
        self.rpc.submit("snapshot", lambda: self.client.thread_read(thread_id))

    def _apply_thread_list(self, payload: dict[str, Any]) -> None:
        records = payload.get("threads")
        if not isinstance(records, list):
            records = []
        counts = payload.get("counts")
        if isinstance(counts, dict):
            for key in ("active", "archived", "all"):
                if key in counts:
                    try:
                        self._thread_counts[key] = max(0, int(counts.get(key) or 0))
                    except (TypeError, ValueError):
                        pass
        elif self._thread_view == "active":
            self._thread_counts["active"] = len(records)

        selected_id = self.state.thread_id

        self.thread_list.blockSignals(True)
        self.thread_list.clear()
        selected_item: QListWidgetItem | None = None
        for group, group_records in self._group_threads(records):
            self._add_group_header(group)
            for record in group_records:
                widget = ThreadListItemWidget(
                    record, self.thread_list, active_workspace=self.current_workspace
                )
                item = QListWidgetItem()
                item.setSizeHint(thread_row_size(widget))
                item.setData(THREAD_ROLE, record)
                item.setData(GROUP_ROLE, group)
                self.thread_list.addItem(item)
                self.thread_list.setItemWidget(item, widget)
                if fmt.text(record.get("id")) == selected_id:
                    selected_item = item
        self.thread_list.blockSignals(False)

        self._apply_thread_filter()
        self._sync_thread_actions()

        if selected_item is not None and not selected_item.isHidden():
            self.thread_list.blockSignals(True)
            self.thread_list.setCurrentItem(selected_item)
            self.thread_list.blockSignals(False)
            return

        first = next(
            (
                item
                for index in range(self.thread_list.count())
                if (item := self.thread_list.item(index)).data(THREAD_ROLE)
                and not item.isHidden()
            ),
            None,
        )
        # A brand-new conversation stays a draft until it is sent, so opening
        # Loom no longer leaves an empty thread behind every time.
        if first is not None and not self._draft_workspace:
            self.thread_list.setCurrentItem(first)
            self.load_thread(fmt.text((first.data(THREAD_ROLE) or {}).get("id")))
            return

        if self._thread_view == "archived":
            self._show_empty_archive_state()
            return
        if first is None and not self._draft_workspace:
            self.begin_draft(Path(self.current_workspace or self.default_workspace))

    def _group_threads(
        self, records: list[Any]
    ) -> list[tuple[str, list[dict[str, Any]]]]:
        """Group rows by project, current workspace first, newest within a group.

        Every row used to repeat its workspace; naming it once per run of rows
        says the same thing without the noise.
        """
        groups: dict[str, list[dict[str, Any]]] = {}
        for record in records:
            if not isinstance(record, dict):
                continue
            workspace = fmt.text(record.get("workspace"))
            groups.setdefault(fmt.short_path(workspace) if workspace else "No project", []).append(
                record
            )

        current = fmt.short_path(self.current_workspace) if self.current_workspace else ""
        ordered = sorted(
            groups.items(),
            key=lambda entry: (entry[0] != current, entry[0].casefold()),
        )
        return [
            (
                name,
                sorted(items, key=lambda r: fmt.text(r.get("updatedAt")), reverse=True),
            )
            for name, items in ordered
        ]

    def _add_group_header(self, title: str) -> None:
        item = QListWidgetItem()
        item.setFlags(Qt.ItemFlag.NoItemFlags)
        item.setData(GROUP_ROLE, title)
        widget = ThreadGroupHeader(title, self.thread_list)
        item.setSizeHint(widget.sizeHint())
        self.thread_list.addItem(item)
        self.thread_list.setItemWidget(item, widget)

    def _apply_thread_filter(self, _value: str | None = None) -> None:
        query = " ".join(self.thread_search.text().split()).casefold()
        visible = 0
        total = 0
        group_has_rows: dict[str, bool] = {}
        headers: list[QListWidgetItem] = []

        for index in range(self.thread_list.count()):
            item = self.thread_list.item(index)
            record = item.data(THREAD_ROLE)
            group = fmt.text(item.data(GROUP_ROLE))
            if not isinstance(record, dict):
                headers.append(item)
                group_has_rows.setdefault(group, False)
                continue
            total += 1
            haystack = " ".join(
                (
                    fmt.text(record.get("title")),
                    fmt.text(record.get("workspace")),
                    fmt.text(record.get("status")),
                )
            ).casefold()
            hidden = bool(query) and query not in haystack
            item.setHidden(hidden)
            if not hidden:
                visible += 1
                group_has_rows[group] = True

        # A project heading with nothing under it is just a stray label.
        for header in headers:
            header.setHidden(not group_has_rows.get(fmt.text(header.data(GROUP_ROLE)), False))
        label = "ARCHIVED" if self._thread_view == "archived" else "CHATS"
        self.thread_section_label.setText(
            f"{label}  {visible}/{total}" if query and visible != total else f"{label}  {total}"
        )
        archived = self._thread_counts.get("archived", 0)
        self.archive_view_button.setText(f"Archived {archived}" if archived else "Archived")

    def _focus_thread_search(self) -> None:
        self.thread_search.setFocus(Qt.FocusReason.ShortcutFocusReason)
        self.thread_search.selectAll()

    def _toggle_archive_view(self, checked: bool) -> None:
        if not self._thread_management_supported:
            self.archive_view_button.setChecked(False)
            return
        self._thread_view = "archived" if checked else "active"
        self.thread_search.clear()
        self.archive_view_button.setToolTip(
            "Show active conversations" if checked else "Show archived conversations"
        )
        self.refresh_threads()

    def _selected_record(self) -> dict[str, Any] | None:
        item = self.thread_list.currentItem()
        if item is None:
            return None
        record = item.data(THREAD_ROLE)
        return record if isinstance(record, dict) else None

    def _sync_thread_actions(self) -> None:
        self.thread_actions_button.setEnabled(
            self._thread_management_supported and self._selected_record() is not None
        )

    def _show_thread_context_menu(self, position: Any) -> None:
        item = self.thread_list.itemAt(position)
        if item is None:
            return
        self.thread_list.setCurrentItem(item)
        self._open_thread_menu(self.thread_list.viewport().mapToGlobal(position))

    def _show_selected_thread_menu(self) -> None:
        if self._selected_record() is None:
            return
        anchor = self.thread_actions_button
        self._open_thread_menu(anchor.mapToGlobal(anchor.rect().bottomLeft()))

    def _open_thread_menu(self, global_position: Any) -> None:
        record = self._selected_record()
        if record is None or not self._thread_management_supported:
            return
        menu = QMenu(self)
        rename = menu.addAction("Rename")
        rename.setShortcut(QKeySequence("F2"))
        archived = bool(record.get("archived"))
        archive = menu.addAction("Restore" if archived else "Archive")
        menu.addSeparator()
        delete = menu.addAction("Delete permanently…")
        chosen = menu.exec(global_position)
        if chosen is rename:
            self._rename_selected_thread()
        elif chosen is archive:
            self._archive_selected_thread()
        elif chosen is delete:
            self._delete_selected_thread()

    def _thread_action(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        request = getattr(self.client, "request", None)
        if not callable(request):
            raise RuntimeError("connected App Server does not support conversation management")
        result = request(method, params)
        return dict(result) if isinstance(result, dict) else {}

    def _rename_selected_thread(self) -> None:
        record = self._selected_record()
        if record is None or not self._thread_management_supported:
            return
        current = fmt.text(record.get("title")).strip() or "New conversation"
        title, accepted = QInputDialog.getText(
            self, "Rename conversation", "Conversation name", QLineEdit.EchoMode.Normal, current
        )
        title = " ".join(title.split()) if accepted else ""
        if not accepted or not title or title == current:
            return
        thread_id = fmt.text(record.get("id"))
        self.rpc.submit(
            f"thread-rename:{thread_id}",
            lambda: self._thread_action("thread/rename", {"threadId": thread_id, "title": title}),
        )

    def _archive_selected_thread(self) -> None:
        record = self._selected_record()
        if record is None or not self._thread_management_supported:
            return
        thread_id = fmt.text(record.get("id"))
        archived = not bool(record.get("archived"))
        self.rpc.submit(
            f"thread-archive:{thread_id}",
            lambda: self._thread_action(
                "thread/archive", {"threadId": thread_id, "archived": archived}
            ),
        )

    def _delete_selected_thread(self) -> None:
        record = self._selected_record()
        if record is None or not self._thread_management_supported:
            return
        title = fmt.text(record.get("title")).strip() or "this conversation"
        dialog = QMessageBox(self)
        dialog.setIcon(QMessageBox.Icon.Warning)
        dialog.setWindowTitle("Delete conversation")
        dialog.setText(f"Delete “{title}” permanently?")
        dialog.setInformativeText(
            "This removes Loom's conversation history and runtime records. "
            "Files in your project workspace are not deleted."
        )
        delete_button = dialog.addButton("Delete", QMessageBox.ButtonRole.DestructiveRole)
        cancel = dialog.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
        dialog.setDefaultButton(cancel)
        dialog.exec()
        if dialog.clickedButton() is not delete_button:
            return
        thread_id = fmt.text(record.get("id"))
        self.rpc.submit(
            f"thread-delete:{thread_id}",
            lambda: self._thread_action("thread/delete", {"threadId": thread_id}),
        )

    def _show_empty_archive_state(self) -> None:
        self.state.reset()
        self.current_turn_id = ""
        self._render_transcript()
        self.thread_title_label.setText("Archived conversations")
        self.composer.setReadOnly(True)
        self.composer.setPlaceholderText("Select an archived conversation to review it")
        self.send_button.setEnabled(False)
        self.stop_button.setEnabled(False)
        self.composer_state_label.setText(
            "Archive is empty" if self.thread_list.count() == 0 else "No match"
        )

    # ------------------------------------------------------------------
    # turns
    # ------------------------------------------------------------------

    def send_prompt(self) -> None:
        text = self.composer.toPlainText().strip()
        if not text or self.state.archived:
            return
        if not self.state.thread_id:
            if self._draft_workspace is None:
                return
            # Create the thread now that the draft has content, then send.
            self.composer.clear()
            self._pending_prompt = text
            self.state.set_optimistic_user(text)
            self._render_transcript()
            self._set_status("starting")
            self._create_thread(self._draft_workspace)
            return

        self.composer.clear()
        self.state.set_optimistic_user(text)
        self._render_transcript()
        self._set_status("starting")
        thread_id = self.state.thread_id
        self.rpc.submit(f"turn:{thread_id}", lambda: self.client.turn_start(thread_id, text))

    def interrupt_turn(self) -> None:
        thread_id = self.state.thread_id
        if not thread_id:
            return
        turn_id = self.current_turn_id or None
        self.rpc.submit(
            f"interrupt:{thread_id}", lambda: self.client.turn_interrupt(thread_id, turn_id)
        )

    def respond_approval(self, call_id: str, approved: bool) -> None:
        thread_id = self.state.thread_id
        call_id = fmt.text(call_id)
        if not call_id or not thread_id:
            return
        self.rpc.submit(
            f"approval:{thread_id}:{call_id}",
            lambda: self.client.approval_respond(thread_id, call_id, approved=approved),
        )

    # ------------------------------------------------------------------
    # RPC results
    # ------------------------------------------------------------------

    def _on_rpc_result(self, tag: str, payload: Any) -> None:
        if tag == "threads":
            self._apply_thread_list(payload if isinstance(payload, dict) else {})
            return
        if tag == "snapshot":
            if isinstance(payload, dict):
                self._apply_snapshot(payload)
            return
        if tag.startswith("new:"):
            record = payload.get("thread") if isinstance(payload, dict) else None
            if isinstance(record, dict):
                thread_id = fmt.text(record.get("id"))
                self.current_workspace = fmt.text(record.get("workspace")) or self.current_workspace
                self._draft_workspace = None
                prompt, self._pending_prompt = self._pending_prompt, ""
                if prompt:
                    self.rpc.submit(
                        f"turn:{thread_id}",
                        lambda: self.client.turn_start(thread_id, prompt),
                    )
                self.refresh_threads()
                self.load_thread(thread_id)
            return
        if tag.startswith("turn:"):
            if isinstance(payload, dict):
                turn = payload.get("turn") or {}
                self.current_turn_id = fmt.text(turn.get("id")) or self.current_turn_id
            return
        if tag.startswith("approval:"):
            self.approval_frame.dismiss()
            self._set_status("running")
            return
        if tag.startswith("interrupt:"):
            self._append_activity("Interrupt requested", marker="!")
            return
        if tag.startswith("thread-rename:"):
            thread = payload.get("thread") if isinstance(payload, dict) else None
            if isinstance(thread, dict) and fmt.text(thread.get("id")) == self.state.thread_id:
                self.state.thread.update(thread)
                self.thread_title_label.setText(
                    fmt.text(thread.get("title")) or "New conversation"
                )
            self._append_activity("Conversation renamed", marker="✓")
            self.refresh_threads()
            return
        if tag.startswith("thread-archive:"):
            thread = payload.get("thread") if isinstance(payload, dict) else None
            archived = bool(thread.get("archived")) if isinstance(thread, dict) else False
            self._append_activity(
                "Conversation archived" if archived else "Conversation restored", marker="✓"
            )
            self.refresh_threads()
            return
        if tag.startswith("thread-delete:"):
            deleted = fmt.text(payload.get("threadId")) if isinstance(payload, dict) else ""
            if deleted and deleted == self.state.thread_id:
                self.state.reset()
                self.current_turn_id = ""
            self._append_activity("Conversation deleted", marker="✓")
            self.refresh_threads()

    def _on_rpc_error(self, tag: str, message: str) -> None:
        self._append_activity(f"RPC error · {message}", marker="!")
        self.notify(message)
        if tag.startswith(("turn:", "approval:", "interrupt:")):
            self._set_status(self.state.status)
        if tag.startswith("approval:"):
            self.approval_frame.set_busy(False)

    # ------------------------------------------------------------------
    # snapshot and rendering
    # ------------------------------------------------------------------

    def _apply_snapshot(self, snapshot: dict[str, Any]) -> None:
        if not self.state.apply_snapshot(snapshot):
            return
        self.current_turn_id = fmt.text(self.state.thread.get("currentTurnId"))
        self.current_workspace = self.state.workspace or self.current_workspace

        self.thread_title_label.setText(self.state.title or "New conversation")
        self._set_workspace_display(self.current_workspace)
        self._set_permission_display(
            fmt.text(self.state.thread.get("permissionMode")) or self.default_permission_mode
        )
        self.usage_label.setText(f"{self.state.total_tokens:,} tokens")

        approval = self.state.pending_approval
        if approval:
            self.approval_frame.present(approval)
        else:
            self.approval_frame.dismiss()

        self._render_transcript()
        self._render_runtime_panels()
        self._set_status(self.state.status)
        self._select_thread_item(self.state.thread_id)

    def _select_thread_item(self, thread_id: str) -> None:
        for index in range(self.thread_list.count()):
            item = self.thread_list.item(index)
            record = item.data(THREAD_ROLE) or {}
            if fmt.text(record.get("id")) == thread_id:
                self.thread_list.blockSignals(True)
                self.thread_list.setCurrentItem(item)
                self.thread_list.blockSignals(False)
                return

    def _render_transcript(self) -> None:
        entries = self.state.entries()
        if not entries:
            self.transcript.clear()
            self.transcript.hide()
            self.empty_state.show()
            return
        self.empty_state.hide()
        self.transcript.show()
        self.transcript.render(entries)

    def _set_status(self, status: str) -> None:
        status = fmt.text(status) or "idle"
        self.status_label.setText(fmt.human_status(status))
        if self.status_label.property("state") != status:
            self.status_label.setProperty("state", status)
            repolish(self.status_label)

        archived = self.state.archived
        active = status in fmt.ACTIVE_STATUSES
        has_thread = bool(self.state.thread_id)
        self.stop_button.setEnabled(active and has_thread and not archived)
        self.send_button.setEnabled(has_thread and not active and not archived)
        self.composer.setReadOnly(archived)

        if archived:
            self.composer.setPlaceholderText("Archived conversation · restore to continue")
            self.composer_state_label.setText("Archived · read-only")
            return
        self.composer.setPlaceholderText("Message Loom…")
        if status == "waiting_approval":
            state_text = "Waiting for approval"
        elif status in {"running", "starting"}:
            state_text = "Loom is working"
        elif status == "failed":
            state_text = "Turn failed"
        else:
            state_text = "Ready"
        self.composer_state_label.setText(state_text)

    def _render_runtime_panels(self) -> None:
        events = self.state.snapshot.get("events") or []
        self._activity_tail = [
            (
                fmt.short_time(event.get("createdAt")),
                fmt.event_marker(event.get("kind")),
                fmt.event_summary(event),
            )
            for event in events[-300:]
            if isinstance(event, dict)
        ]
        self._render_activity()

        processes = self.state.items_of_type("process")
        diffs = self.state.items_of_type("file_edit")
        browser = self.state.tool_items(
            lambda item: "browser" in fmt.text(item.get("toolName")).casefold()
        )
        agents = self.state.tool_items(
            lambda item: fmt.text(item.get("toolName")) in fmt.AGENT_CONTROL_TOOLS
        )

        self.terminal_view.render_items(processes[-30:])
        self.diff_view.set_diff(self._diff_text(diffs), keep_columns=bool(diffs))
        self.browser_view.render_items(browser[-30:])
        self.agents_view.render_items(agents[-30:])
        self._update_sandbox_status(processes)

    def _render_activity(self) -> None:
        rows: list[str] = []
        for when, marker, summary in self._activity_tail[-300:]:
            rows.append(
                f"<div class='event'><span class='marker {fmt.marker_tone(marker)}'>"
                f"{html.escape(marker)}</span>"
                f"<span class='summary'>{html.escape(summary)}</span>"
                f"<div class='time'>{html.escape(when or 'live')}</div></div>"
            )
        if not rows:
            rows.append(
                "<div class='quiet'><b>Runtime is quiet</b><br>"
                "<span>Model steps, tools, commands, diffs, and delegated work will appear here."
                "</span></div>"
            )
        self.activity_view.setHtml(theme.ACTIVITY_CSS + "".join(rows))
        bar = self.activity_view.verticalScrollBar()
        bar.setValue(bar.maximum())

    @staticmethod
    def _diff_text(items: list[dict[str, Any]]) -> str:
        if not items:
            return (
                "No workspace changes yet.\n\n"
                "The latest turn diff will appear here after Loom edits files."
            )
        latest = items[-1]
        paths = latest.get("paths") or []
        body = "Changed paths\n" + "\n".join(f"  {path}" for path in paths)
        if latest.get("truncated"):
            body += "\n\n(diff truncated by Runtime)"
        diff = fmt.text(latest.get("diff"))
        return body + (f"\n\n{diff}" if diff else "")

    def _update_sandbox_status(self, processes: list[dict[str, Any]]) -> None:
        sandbox = next(
            (
                item.get("sandbox")
                for item in reversed(processes)
                if isinstance(item.get("sandbox"), dict) and item.get("sandbox")
            ),
            None,
        )
        if sandbox is None:
            self.sandbox_label.setText("No process")
            state = "idle"
        else:
            enforced = bool(sandbox.get("enforced"))
            backend = fmt.text(sandbox.get("backend")) or "none"
            self.sandbox_label.setText(
                f"{'Sandboxed' if enforced else 'Not sandboxed'} · {backend}"
            )
            state = "enforced" if enforced else "unprotected"
        if self.sandbox_label.property("state") != state:
            self.sandbox_label.setProperty("state", state)
            repolish(self.sandbox_label)

    def _append_activity(self, text: str, *, marker: str = "•") -> None:
        self._activity_tail = self._activity_tail[-299:] + [("live", marker, text)]
        self._render_activity()

    # ------------------------------------------------------------------
    # notifications
    # ------------------------------------------------------------------

    def _on_notification(self, method: str, params: Any) -> None:
        if not isinstance(params, dict):
            return
        if method in {"thread/started", "thread/updated", "thread/deleted"}:
            self.schedule_thread_refresh()
            return

        thread_id = fmt.text(params.get("threadId"))
        if not thread_id:
            item = params.get("item")
            thread_id = fmt.text(item.get("threadId")) if isinstance(item, dict) else ""
        if self.state.thread_id and thread_id and thread_id != self.state.thread_id:
            return

        self._append_activity(fmt.notification_summary(method, params))

        if method == "turn/started":
            turn = params.get("turn") or {}
            self.current_turn_id = fmt.text(turn.get("id")) or self.current_turn_id
            self.state.set_status("running")
            self._set_status("running")
            return
        if method == "item/started":
            self.state.upsert_item(params.get("item"), streaming=True)
            self._render_transcript()
            return
        if method == "item/delta":
            self._apply_item_delta(params)
            return
        if method == "item/completed":
            self.state.upsert_item(params.get("item"), streaming=False)
            self._render_transcript()
            self._schedule_reconcile()
            return
        if method == "approval/requested":
            approval = self.state.set_pending_approval(params.get("approval"))
            if approval:
                self.approval_frame.present(approval)
                self.state.set_status("waiting_approval")
                self._set_status("waiting_approval")
            return
        if method == "turn/completed":
            turn = params.get("turn") or {}
            status = fmt.text(turn.get("status")) or "completed"
            self.state.finish_streaming()
            self.state.clear_optimistic_user()
            self.state.set_status(status)
            self.approval_frame.dismiss()
            self._render_transcript()
            self._set_status(status)
            self._schedule_reconcile()
            self.schedule_thread_refresh()

    def _apply_item_delta(self, params: dict[str, Any]) -> None:
        delta = params.get("delta")
        if not isinstance(delta, dict):
            return
        item_id = params.get("itemId")
        changed = False
        if "text" in delta:
            changed = bool(self.state.append_text_delta(item_id, delta.get("text")))
        stdout = fmt.text(delta.get("stdout"))
        stderr = fmt.text(delta.get("stderr"))
        if stdout or stderr:
            changed = bool(
                self.state.append_process_output(item_id, stdout=stdout, stderr=stderr)
            ) or changed
            if stdout:
                self.terminal_view.appendPlainText(stdout.rstrip("\n"))
            if stderr:
                self.terminal_view.appendPlainText(stderr.rstrip("\n"))
        if changed:
            self._render_transcript()

    def _schedule_reconcile(self) -> None:
        """Coalesce the durable re-read that bursts of items would otherwise cause."""
        if self.state.thread_id and not self._reconcile_timer.isActive():
            self._reconcile_timer.start()

    def _reconcile_now(self) -> None:
        if self.state.thread_id:
            self.load_thread(self.state.thread_id)

    # ------------------------------------------------------------------
    # transport health
    # ------------------------------------------------------------------

    def _on_server_stderr(self, line: str) -> None:
        self._append_activity(f"Server · {line}", marker="!")

    def _on_server_exit(self, message: str) -> None:
        self.protocol_label.setText("App Server · stopped")
        self.connection_dot.setProperty("state", "disconnected")
        repolish(self.connection_dot)
        self.send_button.setEnabled(False)
        self.stop_button.setEnabled(False)
        self._append_activity(message, marker="!")
        if not self._closed:
            self.notify(message)

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt override
        self._closed = True
        self.rpc.close()
        self._reconcile_timer.stop()
        self._thread_list_timer.stop()
        close = getattr(self.client, "close", None)
        if callable(close):
            close()
        event.accept()


__all__ = ["LoomDesktopWindow", "THREAD_ROLE"]
