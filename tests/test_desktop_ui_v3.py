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

    def subscribe_notifications(self, listener):
        self.notification_listener = listener

    def subscribe_stderr(self, listener):
        self.stderr_listener = listener

    def subscribe_exit(self, listener):
        self.exit_listener = listener

    def thread_list(self, *, limit=100):
        assert limit >= 1
        return {
            "threads": [
                {
                    "id": "thread-v3",
                    "title": "Polish the desktop",
                    "workspace": self.workspace,
                    "permissionMode": "workspace",
                    "status": "idle",
                    "currentTurnId": "",
                    "usage": {"inputTokens": 0, "outputTokens": 0, "totalTokens": 0},
                }
            ]
        }

    def thread_read(self, thread_id):
        assert thread_id == "thread-v3"
        return {
            "thread": {
                "id": "thread-v3",
                "title": "Polish the desktop",
                "workspace": self.workspace,
                "permissionMode": "workspace",
                "status": "idle",
                "currentTurnId": "",
                "usage": {"inputTokens": 0, "outputTokens": 0, "totalTokens": 0},
            },
            "messages": [],
            "pendingApproval": None,
            "events": [],
            "turns": [],
            "finalText": "",
            "error": "",
        }

    def close(self):
        self.closed = True


def _wait_for(app, predicate, timeout=2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("Qt desktop condition did not become true")


def _initialization(workspace):
    return {
        "protocolVersion": 1,
        "serverInfo": {"name": "loom-app-server", "version": "0.1.0"},
        "capabilities": {"providerStreaming": True},
        "runtime": {
            "defaultWorkspace": str(workspace),
            "defaultPermissionMode": "workspace",
        },
    }


def test_v3_desktop_uses_compact_chrome_and_quiet_idle_state(tmp_path):
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
        _wait_for(app, lambda: window.current_thread_id == "thread-v3")
        assert window.sidebar_toggle_button.text() == "‹"
        assert window.runtime_toggle_button.text() == "›"
        assert window.activity_panel.maximumWidth() == 410
        assert window.status_label.isVisible() is False
        assert window.usage_label.isVisible() is False
        assert window.sandbox_label.isVisible() is False
        assert window.protocol_label.text() == "Connected"
        assert "App Server v0.1.0" in window.protocol_label.toolTip()
        assert isinstance(
            window.thread_list.itemWidget(window.thread_list.item(0)),
            ThreadListItemWidget,
        )

        window.toggle_sidebar()
        assert window.sidebar_toggle_button.text() == "›"
        window.toggle_sidebar()
        assert window.sidebar_toggle_button.text() == "‹"
        window.toggle_runtime()
        assert window.runtime_toggle_button.text() == "‹"
        window.toggle_runtime()
        assert window.runtime_toggle_button.text() == "›"
    finally:
        window.close()
        app.processEvents()
    assert client.closed is True


def test_v3_composer_grows_with_multiline_prompt_and_shrinks_again(tmp_path):
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
        _wait_for(app, lambda: window.current_thread_id == "thread-v3")
        window.composer.clear()
        app.processEvents()
        compact_height = window.composer.height()
        assert compact_height <= 60

        window.composer.setPlainText("one\ntwo\nthree\nfour")
        app.processEvents()
        expanded_height = window.composer.height()
        assert expanded_height > compact_height
        assert expanded_height <= 142

        window.composer.clear()
        app.processEvents()
        assert window.composer.height() == compact_height
    finally:
        window.close()
        app.processEvents()
