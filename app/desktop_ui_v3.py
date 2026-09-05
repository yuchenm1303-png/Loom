from __future__ import annotations

import ctypes
import sys
from pathlib import Path
from typing import Any

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtWidgets import QHBoxLayout, QLabel, QListWidgetItem, QVBoxLayout, QWidget

from app import desktop_ui_v2 as v2


DesktopEventBridge = v2.DesktopEventBridge
ComposerTextEdit = v2.ComposerTextEdit


class ThreadListItemWidget(QWidget):
    """Quieter thread row: completed/idle are metadata, active/problem states stay prominent."""

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

        title_text = v2._text(record.get("title")).strip() or "New thread"
        title = QLabel(title_text)
        title.setObjectName("threadItemTitle")
        title.setTextFormat(Qt.TextFormat.PlainText)
        title.setToolTip(title_text)
        title.setMaximumHeight(21)
        layout.addWidget(title)

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
        status_value = v2._text(record.get("status")) or "idle"
        status = QLabel(v2._human_status(status_value))
        status.setObjectName("threadStatus")
        status.setProperty("state", status_value)
        meta.addWidget(status)
        layout.addLayout(meta)


class LoomDesktopWindow(v2.LoomDesktopWindow):
    """Third-pass native polish without changing the App Server/runtime contract."""

    def _build_ui(self) -> None:
        super()._build_ui()

        # Give the conversation more authority and keep inspectors compact.
        self.sidebar_panel.setMinimumWidth(248)
        self.sidebar_panel.setMaximumWidth(326)
        self.activity_panel.setMinimumWidth(276)
        self.activity_panel.setMaximumWidth(410)
        self.main_splitter.setHandleWidth(1)
        self.main_splitter.setSizes([270, 1030, 300])

        # Panel controls should read as controls, not status badges.
        self.sidebar_toggle_button.setText("‹")
        self.sidebar_toggle_button.setFixedWidth(30)
        self.sidebar_toggle_button.setToolTip("Hide Threads")
        self.runtime_toggle_button.setText("›")
        self.runtime_toggle_button.setFixedWidth(30)
        self.runtime_toggle_button.setToolTip("Hide Runtime")

        # A compact composer should grow only when the prompt actually needs it.
        self.composer.setMinimumHeight(56)
        self.composer.setMaximumHeight(142)
        self.composer.textChanged.connect(self._resize_composer)
        self._resize_composer()

        self.activity_tabs.tabBar().setExpanding(False)

    def _apply_style(self) -> None:
        super()._apply_style()
        self.setStyleSheet(
            self.styleSheet()
            + """
            QMainWindow, QWidget {
                background: #07080b;
                color: #edf0f5;
            }
            QFrame#sidebar, QFrame#activityPanel { background: #090b0f; }
            QFrame#conversationPanel { background: #07080b; }

            QLabel#brandMark { background: #655bd0; }
            QLabel#brandSubtitle { color: #687083; }
            QLabel#threadTitle { font-size: 18px; }
            QLabel#mutedLabel { color: #6c7384; }

            QFrame#workspaceHeader { border-bottom-color: #151821; }
            QLabel#statusChip[state="idle"] { background: transparent; color: #697183; padding-left: 2px; padding-right: 2px; }

            QListWidget#threadList::item:hover { background: #0d1015; }
            QListWidget#threadList::item:selected { background: #12141f; border-color: #272942; }
            QLabel#threadItemTitle { font-size: 13px; }
            QLabel#threadItemMeta { color: #626a7c; font-size: 10px; }
            QLabel#threadStatus[state="idle"],
            QLabel#threadStatus[state="completed"] {
                background: transparent;
                border: none;
                color: #687083;
                padding: 0;
                font-weight: 550;
            }
            QLabel#threadStatus[state="completed"] { color: #6f8d82; }
            QLabel#threadStatus[state="running"],
            QLabel#threadStatus[state="starting"] {
                color: #9dc9fb;
                background: #101824;
            }
            QLabel#threadStatus[state="waiting_approval"] {
                color: #edbd70;
                background: #1f180f;
            }
            QLabel#threadStatus[state="failed"],
            QLabel#threadStatus[state="cancelled"] {
                color: #e79ca3;
                background: #211416;
            }

            QLabel#protocolLabel { color: #636b7c; font-size: 10px; }

            QFrame#emptyStateContent { min-width: 500px; max-width: 640px; }
            QLabel#emptyKicker { color: #786ee0; }
            QLabel#emptyTitle { font-size: 25px; }
            QLabel#emptyBody { color: #7b8293; font-size: 13px; }
            QPushButton#promptSuggestion {
                background: transparent;
                border: 1px solid transparent;
                border-radius: 9px;
                min-height: 36px;
                color: #a9afbb;
                padding: 0 11px;
            }
            QPushButton#promptSuggestion:hover {
                background: #0e1117;
                border-color: #1c222d;
                color: #eef0f4;
            }

            QFrame#composerFrame {
                background: #0b0d12;
                border-color: #1b202a;
                border-radius: 13px;
            }
            QTextEdit#composer {
                font-size: 14px;
                padding: 2px 2px 4px 2px;
            }
            QLabel#composerHint { color: #596172; }
            QLabel#composerChip {
                background: transparent;
                color: #737b8d;
                padding: 1px 4px;
            }
            QLabel#composerState { color: #858c9c; }

            QPushButton#newThreadButton, QPushButton#sendButton, QPushButton#allowButton {
                background: #5f57c9;
                border-color: #6961d1;
            }
            QPushButton#newThreadButton:hover, QPushButton#sendButton:hover, QPushButton#allowButton:hover {
                background: #6a62d7;
            }
            QPushButton#panelToggle {
                min-width: 28px;
                max-width: 30px;
                min-height: 28px;
                max-height: 28px;
                padding: 0;
                border: none;
                border-radius: 7px;
                background: transparent;
                color: #687083;
                font-size: 17px;
                font-weight: 500;
            }
            QPushButton#panelToggle:hover { background: #11141b; color: #d7dae2; }
            QPushButton#panelToggle[active="true"] { border: none; color: #8580cf; }

            QLabel#inspectorTitle { font-size: 15px; }
            QTabBar::tab { font-size: 12px; padding: 9px 8px; }
            QTabBar::tab:selected { border-bottom-color: #655bd0; }
            QTextBrowser#activityView, QPlainTextEdit {
                background: #090b0f;
                font-size: 11px;
            }

            QSplitter::handle { background: #141720; }
            QScrollBar::handle:vertical { background: #252a34; }
            """
        )

    def _apply_initialization(self) -> None:
        protocol = self.initialization.get("protocolVersion", "?")
        server = self.initialization.get("serverInfo") or {}
        capabilities = self.initialization.get("capabilities") or {}
        streaming = bool(capabilities.get("providerStreaming"))
        server_version = v2._text(server.get("version")) or "?"

        self.protocol_label.setText("Connected")
        self.protocol_label.setToolTip(
            f"App Server v{server_version} · protocol {protocol}\n"
            f"Provider streaming · {'on' if streaming else 'fallback'}"
        )
        self.connection_dot.setProperty("state", "connected")
        v2._repolish(self.connection_dot)

    def _apply_thread_list(self, payload: dict[str, Any]) -> None:
        records = payload.get("threads") or []
        if not isinstance(records, list):
            records = []
        selected_id = self.current_thread_id
        self._threads_by_id = {
            v2._text(record.get("id")): record
            for record in records
            if isinstance(record, dict) and v2._text(record.get("id"))
        }
        self.thread_section_label.setText(f"THREADS  {len(records)}")
        self.thread_list.blockSignals(True)
        self.thread_list.clear()
        selected_item: QListWidgetItem | None = None
        for record in records:
            if not isinstance(record, dict):
                continue
            item = QListWidgetItem()
            item.setSizeHint(QSize(0, 56))
            item.setToolTip(v2._text(record.get("workspace")))
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

        if selected_item is not None:
            self.thread_list.setCurrentItem(selected_item)
            return
        if records:
            first = self.thread_list.item(0)
            self.thread_list.setCurrentItem(first)
            record = first.data(v2._THREAD_ROLE) or {}
            self.load_thread(v2._text(record.get("id")))
            self._startup_autocreate = False
            return
        if self._startup_autocreate:
            self._startup_autocreate = False
            self._create_thread(self.default_workspace)

    def _apply_snapshot(self, snapshot: dict[str, Any]) -> None:
        super()._apply_snapshot(snapshot)
        thread = snapshot.get("thread") or {}
        usage = thread.get("usage") or {}
        total = int(usage.get("totalTokens") or 0) if isinstance(usage, dict) else 0
        self.usage_label.setVisible(total > 0)

    def _set_status(self, status: str) -> None:
        super()._set_status(status)
        normalized = status or "idle"
        self.status_label.setVisible(normalized != "idle")
        self.composer_state_label.setVisible(self.composer_state_label.text() != "Ready")

    def _update_sandbox_status(self, processes: list[dict[str, Any]]) -> None:
        super()._update_sandbox_status(processes)
        self.sandbox_label.setVisible(self.sandbox_label.text() != "No process")

    def toggle_sidebar(self) -> None:
        super().toggle_sidebar()
        self._sync_panel_controls()

    def toggle_runtime(self) -> None:
        super().toggle_runtime()
        self._sync_panel_controls()

    def _sync_panel_controls(self) -> None:
        if self._sidebar_visible:
            self.sidebar_toggle_button.setText("‹")
            self.sidebar_toggle_button.setToolTip("Hide Threads")
        else:
            self.sidebar_toggle_button.setText("›")
            self.sidebar_toggle_button.setToolTip("Show Threads")
        if self._runtime_visible:
            self.runtime_toggle_button.setText("›")
            self.runtime_toggle_button.setToolTip("Hide Runtime")
        else:
            self.runtime_toggle_button.setText("‹")
            self.runtime_toggle_button.setToolTip("Show Runtime")

    def _resize_composer(self) -> None:
        blocks = max(1, self.composer.document().blockCount())
        target = max(56, min(142, 36 + blocks * 20))
        self.composer.setFixedHeight(target)

    def showEvent(self, event) -> None:  # noqa: N802 - Qt override
        super().showEvent(event)
        QTimer.singleShot(0, self._apply_native_window_chrome)

    def _apply_native_window_chrome(self) -> None:
        """Best-effort Windows dark caption while retaining the native frame and behavior."""
        if sys.platform != "win32":
            return
        try:
            hwnd = int(self.winId())
            dwm = ctypes.windll.dwmapi

            enabled = ctypes.c_int(1)
            for attribute in (20, 19):  # DWMWA_USE_IMMERSIVE_DARK_MODE, older fallback
                try:
                    result = dwm.DwmSetWindowAttribute(
                        hwnd,
                        attribute,
                        ctypes.byref(enabled),
                        ctypes.sizeof(enabled),
                    )
                    if result == 0:
                        break
                except OSError:
                    continue

            # Windows 11 supports explicit native caption/border colors. Ignore on older builds.
            caption = ctypes.c_uint(0x000B0807)  # COLORREF for #07080b
            border = ctypes.c_uint(0x00171412)   # restrained dark native border
            text = ctypes.c_uint(0x00F2F0ED)     # near-white caption text
            for attribute, value in ((35, caption), (34, border), (36, text)):
                try:
                    dwm.DwmSetWindowAttribute(
                        hwnd,
                        attribute,
                        ctypes.byref(value),
                        ctypes.sizeof(value),
                    )
                except OSError:
                    pass
        except (AttributeError, OSError, TypeError, ValueError):
            return
