from __future__ import annotations

import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication

from app.desktop_ui import LoomDesktopWindow, ThreadListItemWidget


class FakeClient:
    def __init__(self, workspace) -> None:
        self.workspace = str(workspace)
        self.notification_listener = None
        self.stderr_listener = None
        self.exit_listener = None
        self.closed = False
        self.requests = []

    def subscribe_notifications(self, listener):
        self.notification_listener = listener

    def subscribe_stderr(self, listener):
        self.stderr_listener = listener

    def subscribe_exit(self, listener):
        self.exit_listener = listener

    def _record(self, thread_id: str, title: str, *, archived: bool) -> dict:
        return {
            "id": thread_id,
            "title": title,
            "workspace": self.workspace,
            "permissionMode": "workspace",
            "status": "completed",
            "currentTurnId": "turn-1",
            "usage": {"inputTokens": 4, "outputTokens": 3, "totalTokens": 7},
            "archived": archived,
            "archivedAt": "2026-09-07T05:00:00+00:00" if archived else None,
        }

    def thread_list(self, *, limit=100):
        assert limit >= 1
        return {
            "threads": [self._record("thread-active", "Active conversation", archived=False)],
            "view": "active",
            "counts": {"active": 1, "archived": 1, "all": 2},
        }

    def thread_read(self, thread_id):
        archived = thread_id == "thread-archived"
        title = "Archived conversation" if archived else "Active conversation"
        return {
            "thread": self._record(thread_id, title, archived=archived),
            "messages": [{"role": "assistant", "content": "Conversation body"}],
            "pendingApproval": None,
            "events": [],
            "turns": [],
            "finalText": "Conversation body",
            "error": "",
        }

    def request(self, method, params):
        self.requests.append((method, dict(params)))
        if method == "thread/list":
            assert params["view"] == "archived"
            return {
                "threads": [
                    self._record("thread-archived", "Archived conversation", archived=True)
                ],
                "view": "archived",
                "counts": {"active": 1, "archived": 1, "all": 2},
            }
        if method == "thread/archive":
            archived = bool(params["archived"])
            return {
                "thread": self._record(
                    params["threadId"],
                    "Archived conversation" if archived else "Active conversation",
                    archived=archived,
                )
            }
        if method == "thread/rename":
            return {
                "thread": {
                    **self._record(params["threadId"], params["title"], archived=False),
                    "customTitle": True,
                }
            }
        if method == "thread/delete":
            return {"deleted": True, "threadId": params["threadId"]}
        raise AssertionError(f"unexpected request: {method}")

    def close(self):
        self.closed = True


def _initialization(workspace):
    return {
        "protocolVersion": 1,
        "serverInfo": {"name": "loom-app-server", "version": "0.1.0"},
        "capabilities": {
            "providerStreaming": True,
            "threadManagement": {
                "rename": True,
                "archive": True,
                "delete": True,
                "search": True,
            },
        },
        "runtime": {
            "defaultWorkspace": str(workspace),
            "defaultPermissionMode": "workspace",
        },
    }


def _wait_for(app, predicate, timeout=2.5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("Qt desktop condition did not become true")


def test_v7_conversation_library_search_archive_and_read_only_state(tmp_path):
    app = QApplication.instance() or QApplication([])
    client = FakeClient(tmp_path)
    window = LoomDesktopWindow(
        client=client,
        initialization=_initialization(tmp_path),
        default_workspace=tmp_path,
        default_permission_mode="workspace",
    )
    window.show()
    try:
        _wait_for(app, lambda: window.current_thread_id == "thread-active")
        assert window.thread_search.placeholderText() == "Search conversations"
        assert window.archive_view_button.isEnabled() is True
        assert window.thread_actions_button.isEnabled() is True
        assert window.thread_section_label.text() == "CHATS  1"
        row = window.thread_list.itemWidget(window.thread_list.item(0))
        assert isinstance(row, ThreadListItemWidget)

        window.thread_search.setText("missing")
        app.processEvents()
        assert window.thread_list.item(0).isHidden() is True
        assert window.thread_section_label.text() == "CHATS  0/1"
        window.thread_search.clear()

        window.archive_view_button.click()
        _wait_for(app, lambda: window.current_thread_id == "thread-archived")
        assert any(method == "thread/list" for method, _params in client.requests)
        assert window.thread_section_label.text() == "ARCHIVED  1"
        assert window.composer.isReadOnly() is True
        assert window.send_button.isEnabled() is False
        assert window.composer_state_label.text() == "Archived · read-only"
    finally:
        window.close()
        app.processEvents()
    assert client.closed is True
