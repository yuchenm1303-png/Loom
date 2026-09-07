from __future__ import annotations

from pathlib import Path
from typing import Any

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app import desktop_ui_v2 as v2
from app import desktop_ui_v6 as v6


DesktopEventBridge = v6.DesktopEventBridge
ComposerTextEdit = v6.ComposerTextEdit


class ThreadListItemWidget(QWidget):
    """Conversation-library row with quiet metadata and reliable title elision."""

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
        self._full_title = v2._text(record.get("title")).strip() or "New conversation"

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 9, 12, 9)
        layout.setSpacing(5)

        self.title_label = QLabel(self._full_title)
        self.title_label.setObjectName("threadItemTitle")
        self.title_label.setTextFormat(Qt.TextFormat.PlainText)
        self.title_label.setToolTip(self._full_title)
        self.title_label.setMaximumHeight(21)
        layout.addWidget(self.title_label)

        meta = QHBoxLayout()
        meta.setContentsMargins(0, 0, 0, 0)
        meta.setSpacing(7)

        record_workspace = v2._text(record.get("workspace"))
        show_workspace = True
        if active_workspace and record_workspace:
            try:
                show_workspace = Path(record_workspace).resolve() != Path(active_workspace).resolve()
            except OSError:
                show_workspace = record_workspace != str(active_workspace)
        if show_workspace and record_workspace:
            workspace = QLabel(v2._short_path(record_workspace))
            workspace.setObjectName("threadItemMeta")
            meta.addWidget(workspace)

        usage = record.get("usage") or {}
        token_total = usage.get("totalTokens") if isinstance(usage, dict) else None
        if token_total:
            token_label = QLabel(v2._format_tokens(token_total))
            token_label.setObjectName("threadItemMeta")
            meta.addWidget(token_label)

        meta.addStretch(1)
        if record.get("archived"):
            status_value = "archived"
            status_text = "Archived"
        else:
            status_value = v2._text(record.get("status")) or "idle"
            status_text = v2._human_status(status_value)
        status = QLabel(status_text)
        status.setObjectName("threadStatus")
        status.setProperty("state", status_value)
        meta.addWidget(status)
        layout.addLayout(meta)

    def resizeEvent(self, event: Any) -> None:  # noqa: N802 - Qt override
        super().resizeEvent(event)
        available = max(48, self.width() - 28)
        self.title_label.setText(
            self.title_label.fontMetrics().elidedText(
                self._full_title,
                Qt.TextElideMode.ElideRight,
                available,
            )
        )


class LoomDesktopWindow(v6.LoomDesktopWindow):
    """Desktop v7: a complete, durable conversation library over v6 visuals."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self._thread_view = "active"
        self._thread_counts = {"active": 0, "archived": 0, "all": 0}
        self._thread_management_supported = False
        super().__init__(*args, **kwargs)

    def _build_ui(self) -> None:
        super()._build_ui()

        self.thread_library_toolbar = QFrame(self.sidebar_panel)
        self.thread_library_toolbar.setObjectName("threadLibraryToolbar")
        toolbar = QHBoxLayout(self.thread_library_toolbar)
        toolbar.setContentsMargins(0, 0, 0, 0)
        toolbar.setSpacing(6)

        self.thread_search = QLineEdit(self.thread_library_toolbar)
        self.thread_search.setObjectName("threadSearch")
        self.thread_search.setPlaceholderText("Search conversations")
        self.thread_search.setClearButtonEnabled(True)
        self.thread_search.setMinimumHeight(34)
        self.thread_search.textChanged.connect(self._apply_thread_filter)
        toolbar.addWidget(self.thread_search, 1)

        self.archive_view_button = QPushButton("Archived", self.thread_library_toolbar)
        self.archive_view_button.setObjectName("archiveViewButton")
        self.archive_view_button.setCheckable(True)
        self.archive_view_button.setToolTip("Show archived conversations")
        self.archive_view_button.clicked.connect(self._toggle_archive_view)
        toolbar.addWidget(self.archive_view_button)

        self.thread_actions_button = QPushButton("•••", self.thread_library_toolbar)
        self.thread_actions_button.setObjectName("threadActionsButton")
        self.thread_actions_button.setToolTip("Conversation actions")
        self.thread_actions_button.setFixedWidth(34)
        self.thread_actions_button.clicked.connect(self._show_selected_thread_menu)
        toolbar.addWidget(self.thread_actions_button)

        sidebar_layout = self.sidebar_panel.layout()
        index = sidebar_layout.indexOf(self.thread_list)
        sidebar_layout.insertWidget(max(0, index), self.thread_library_toolbar)

        self.thread_list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.thread_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.thread_list.customContextMenuRequested.connect(self._show_thread_context_menu)
        self.thread_list.currentItemChanged.connect(lambda _current, _previous: self._sync_thread_actions())

        self.find_shortcut = QShortcut(QKeySequence("Ctrl+F"), self)
        self.find_shortcut.activated.connect(self._focus_thread_search)
        self.rename_shortcut = QShortcut(QKeySequence("F2"), self.thread_list)
        self.rename_shortcut.activated.connect(self._rename_selected_thread)
        self.delete_shortcut = QShortcut(QKeySequence("Delete"), self.thread_list)
        self.delete_shortcut.activated.connect(self._delete_selected_thread)

        self.thread_section_label.setText("CHATS")
        self._sync_thread_actions()

    def _apply_style(self) -> None:
        super()._apply_style()
        self.setStyleSheet(
            self.styleSheet()
            + """
            QFrame#threadLibraryToolbar { background:transparent; border:none; }
            QLineEdit#threadSearch {
                min-height:32px;
                background:#0e1117;
                border:1px solid #1c222c;
                border-radius:9px;
                padding:0 10px;
                color:#d9dde5;
                selection-background-color:#5f57c9;
                font-size:11px;
            }
            QLineEdit#threadSearch:hover { border-color:#29303c; background:#10141b; }
            QLineEdit#threadSearch:focus { border-color:#484669; background:#11151d; }
            QLineEdit#threadSearch::placeholder { color:#596274; }
            QPushButton#archiveViewButton {
                min-height:32px;
                padding:0 9px;
                background:#0e1117;
                border:1px solid #1c222c;
                border-radius:9px;
                color:#727c8e;
                font-size:10px;
                font-weight:650;
            }
            QPushButton#archiveViewButton:hover { color:#c9ced8; border-color:#2b3240; background:#12161d; }
            QPushButton#archiveViewButton:checked {
                color:#d9d5ff;
                background:#171624;
                border-color:#3b385d;
            }
            QPushButton#threadActionsButton {
                min-height:32px;
                max-height:32px;
                padding:0;
                background:#0e1117;
                border:1px solid #1c222c;
                border-radius:9px;
                color:#70798a;
                font-weight:800;
            }
            QPushButton#threadActionsButton:hover { color:#eef0f4; background:#141820; border-color:#303744; }
            QPushButton#threadActionsButton:disabled { color:#343b48; background:#0c0e13; border-color:#151920; }
            QListWidget#threadList::item { margin:1px 0; border-radius:9px; }
            QListWidget#threadList::item:selected { background:#141620; border-color:#2b2f40; border-left:2px solid #7168e2; }
            QLabel#threadStatus[state="archived"] { color:#7c8493; background:transparent; border:none; padding:0; font-size:9px; }
            QMenu {
                background:#101319;
                border:1px solid #282e39;
                border-radius:9px;
                padding:6px;
                color:#d5d9e1;
                font-size:11px;
            }
            QMenu::item { min-width:150px; padding:7px 16px 7px 10px; border-radius:6px; }
            QMenu::item:selected { background:#1b2029; color:#ffffff; }
            QMenu::separator { height:1px; background:#232934; margin:5px 7px; }
            """
        )

    def _apply_initialization(self) -> None:
        super()._apply_initialization()
        capabilities = self.initialization.get("capabilities") or {}
        management = capabilities.get("threadManagement") if isinstance(capabilities, dict) else None
        self._thread_management_supported = bool(management)
        self.archive_view_button.setEnabled(self._thread_management_supported)
        self.thread_actions_button.setEnabled(False)
        if self._thread_management_supported:
            self.archive_view_button.setToolTip("Show archived conversations")
        else:
            self.archive_view_button.setToolTip("Update Loom App Server to manage conversations")

    def refresh_threads(self) -> None:
        if self._thread_view == "active":
            self._run_rpc("threads", lambda: self.client.thread_list(limit=200))
            return
        request = getattr(self.client, "request", None)
        if not callable(request):
            self._apply_thread_list(
                {"threads": [], "view": "archived", "counts": dict(self._thread_counts)}
            )
            return
        self._run_rpc(
            "threads",
            lambda: dict(request("thread/list", {"limit": 200, "view": "archived"})),
        )

    def _apply_thread_list(self, payload: dict[str, Any]) -> None:
        records = payload.get("threads") or []
        if not isinstance(records, list):
            records = []
        counts = payload.get("counts") or {}
        if isinstance(counts, dict):
            for key in ("active", "archived", "all"):
                if key in counts:
                    try:
                        self._thread_counts[key] = max(0, int(counts.get(key) or 0))
                    except (TypeError, ValueError):
                        pass
        elif self._thread_view == "active":
            self._thread_counts["active"] = len(records)

        selected_id = self.current_thread_id
        self._threads_by_id = {
            v2._text(record.get("id")): record
            for record in records
            if isinstance(record, dict) and v2._text(record.get("id"))
        }
        self.thread_list.blockSignals(True)
        self.thread_list.clear()
        selected_item: QListWidgetItem | None = None
        for record in records:
            if not isinstance(record, dict):
                continue
            item = QListWidgetItem()
            item.setSizeHint(QSize(0, 60))
            item.setToolTip(v2._text(record.get("title")))
            item.setData(v2._THREAD_ROLE, record)
            self.thread_list.addItem(item)
            self.thread_list.setItemWidget(
                item,
                ThreadListItemWidget(
                    record,
                    self.thread_list,
                    active_workspace=self.current_workspace,
                ),
            )
            if v2._text(record.get("id")) == selected_id:
                selected_item = item
        self.thread_list.blockSignals(False)

        self._apply_thread_filter()
        self._sync_thread_actions()

        if selected_item is not None and not selected_item.isHidden():
            self.thread_list.setCurrentItem(selected_item)
            return
        if records:
            first = next(
                (self.thread_list.item(i) for i in range(self.thread_list.count()) if not self.thread_list.item(i).isHidden()),
                None,
            )
            if first is not None:
                self.thread_list.setCurrentItem(first)
                record = first.data(v2._THREAD_ROLE) or {}
                self.load_thread(v2._text(record.get("id")))
                self._startup_autocreate = False
                return

        if self._thread_view == "active":
            if self._startup_autocreate:
                self._startup_autocreate = False
                self._create_thread(self.default_workspace)
            elif selected_id:
                self.current_thread_id = ""
                self.current_turn_id = ""
                self._create_thread(self.current_workspace or self.default_workspace)
        else:
            self._show_empty_archive_state()

    def _apply_thread_filter(self, _value: str | None = None) -> None:
        if not hasattr(self, "thread_search"):
            return
        query = " ".join(self.thread_search.text().split()).casefold()
        visible = 0
        for index in range(self.thread_list.count()):
            item = self.thread_list.item(index)
            record = item.data(v2._THREAD_ROLE) or {}
            haystack = " ".join(
                (
                    v2._text(record.get("title")),
                    v2._text(record.get("workspace")),
                    v2._text(record.get("status")),
                )
            ).casefold()
            hidden = bool(query) and query not in haystack
            item.setHidden(hidden)
            if not hidden:
                visible += 1

        total = self.thread_list.count()
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
        record = item.data(v2._THREAD_ROLE)
        return record if isinstance(record, dict) else None

    def _sync_thread_actions(self) -> None:
        if not hasattr(self, "thread_actions_button"):
            return
        enabled = self._thread_management_supported and self._selected_record() is not None
        self.thread_actions_button.setEnabled(enabled)

    def _show_thread_context_menu(self, position: Any) -> None:
        item = self.thread_list.itemAt(position)
        if item is None:
            return
        self.thread_list.setCurrentItem(item)
        self._open_thread_menu(self.thread_list.viewport().mapToGlobal(position))

    def _show_selected_thread_menu(self) -> None:
        if self._selected_record() is None:
            return
        point = self.thread_actions_button.mapToGlobal(self.thread_actions_button.rect().bottomLeft())
        self._open_thread_menu(point)

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

    def _request_thread_action(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        request = getattr(self.client, "request", None)
        if not callable(request):
            raise RuntimeError("connected App Server does not support conversation management")
        result = request(method, params)
        return dict(result) if isinstance(result, dict) else {}

    def _rename_selected_thread(self) -> None:
        record = self._selected_record()
        if record is None or not self._thread_management_supported:
            return
        current = v2._text(record.get("title")).strip() or "New conversation"
        title, accepted = QInputDialog.getText(
            self,
            "Rename conversation",
            "Conversation name",
            QLineEdit.EchoMode.Normal,
            current,
        )
        if not accepted:
            return
        title = " ".join(title.split())
        if not title or title == current:
            return
        thread_id = v2._text(record.get("id"))
        self._run_rpc(
            f"thread-rename:{thread_id}",
            lambda: self._request_thread_action(
                "thread/rename",
                {"threadId": thread_id, "title": title},
            ),
        )

    def _archive_selected_thread(self) -> None:
        record = self._selected_record()
        if record is None or not self._thread_management_supported:
            return
        archived = bool(record.get("archived"))
        thread_id = v2._text(record.get("id"))
        self._run_rpc(
            f"thread-archive:{thread_id}:{int(not archived)}",
            lambda: self._request_thread_action(
                "thread/archive",
                {"threadId": thread_id, "archived": not archived},
            ),
        )

    def _delete_selected_thread(self) -> None:
        record = self._selected_record()
        if record is None or not self._thread_management_supported:
            return
        title = v2._text(record.get("title")).strip() or "this conversation"
        message = QMessageBox(self)
        message.setIcon(QMessageBox.Icon.Warning)
        message.setWindowTitle("Delete conversation")
        message.setText(f"Delete “{title}” permanently?")
        message.setInformativeText(
            "This removes Loom's conversation history and runtime records. "
            "Files in your external project workspace are not deleted."
        )
        delete_button = message.addButton("Delete", QMessageBox.ButtonRole.DestructiveRole)
        message.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
        message.setDefaultButton(message.button(QMessageBox.StandardButton.Cancel))
        message.exec()
        if message.clickedButton() is not delete_button:
            return
        thread_id = v2._text(record.get("id"))
        self._run_rpc(
            f"thread-delete:{thread_id}",
            lambda: self._request_thread_action("thread/delete", {"threadId": thread_id}),
        )

    def _on_rpc_result(self, tag: str, payload: Any) -> None:
        if tag.startswith("thread-rename:"):
            self._pending_rpc.discard(tag)
            thread = payload.get("thread") if isinstance(payload, dict) else None
            if isinstance(thread, dict) and v2._text(thread.get("id")) == self.current_thread_id:
                self.thread_title_label.setText(v2._text(thread.get("title")) or "New conversation")
                if isinstance(self.current_snapshot.get("thread"), dict):
                    self.current_snapshot["thread"].update(thread)
            self._append_activity("Conversation renamed", marker="✓")
            self.refresh_threads()
            return
        if tag.startswith("thread-archive:"):
            self._pending_rpc.discard(tag)
            thread = payload.get("thread") if isinstance(payload, dict) else None
            archived = bool(thread.get("archived")) if isinstance(thread, dict) else False
            self._append_activity("Conversation archived" if archived else "Conversation restored", marker="✓")
            self.refresh_threads()
            return
        if tag.startswith("thread-delete:"):
            self._pending_rpc.discard(tag)
            deleted_id = v2._text(payload.get("threadId")) if isinstance(payload, dict) else ""
            if deleted_id and deleted_id == self.current_thread_id:
                self.current_thread_id = ""
                self.current_turn_id = ""
            self._append_activity("Conversation deleted", marker="✓")
            self.refresh_threads()
            return
        super()._on_rpc_result(tag, payload)

    def _on_rpc_error(self, tag: str, message: str) -> None:
        if tag.startswith(("thread-rename:", "thread-archive:", "thread-delete:")):
            super()._on_rpc_error(tag, message)
            QMessageBox.warning(self, "Conversation action failed", message)
            return
        super()._on_rpc_error(tag, message)

    def _on_notification(self, method: str, params: Any) -> None:
        if method in {"thread/updated", "thread/deleted"}:
            self.refresh_threads()
            return
        super()._on_notification(method, params)

    def _apply_snapshot(self, snapshot: dict[str, Any]) -> None:
        super()._apply_snapshot(snapshot)
        thread = snapshot.get("thread") or {}
        archived = bool(thread.get("archived")) if isinstance(thread, dict) else False
        self._set_archive_read_only(archived)

    def _set_status(self, status: str) -> None:
        super()._set_status(status)
        thread = self.current_snapshot.get("thread") or {}
        archived = bool(thread.get("archived")) if isinstance(thread, dict) else False
        if archived:
            self._set_archive_read_only(True)

    def _set_archive_read_only(self, archived: bool) -> None:
        self.composer.setReadOnly(archived)
        if archived:
            self.composer.setPlaceholderText("Archived conversation · restore to continue")
            self.send_button.setEnabled(False)
            self.stop_button.setEnabled(False)
            self.composer_state_label.setText("Archived · read-only")
            self.composer_state_label.setVisible(True)
        else:
            self.composer.setPlaceholderText("Message Loom…")

    def _show_empty_archive_state(self) -> None:
        if self._thread_view != "archived":
            return
        self.current_thread_id = ""
        self.current_turn_id = ""
        self.current_snapshot = {}
        self._durable_messages = []
        self._live_assistant.clear()
        self._optimistic_user = None
        self._render_transcript()
        self.thread_title_label.setText("Archived conversations")
        self.composer.setReadOnly(True)
        self.composer.setPlaceholderText("Select an archived conversation to review it")
        self.send_button.setEnabled(False)
        self.stop_button.setEnabled(False)
        self.composer_state_label.setText("Archive is empty" if self.thread_list.count() == 0 else "No match")
        self.composer_state_label.setVisible(True)


__all__ = [
    "ComposerTextEdit",
    "DesktopEventBridge",
    "LoomDesktopWindow",
    "ThreadListItemWidget",
]
