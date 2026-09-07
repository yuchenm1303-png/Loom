from __future__ import annotations

import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QGraphicsDropShadowEffect

from app.desktop_ui import LoomDesktopWindow
from app.desktop_ui_v10 import LoomDesktopWindow as LoomDesktopWindowV10


class FakeClient:
    def __init__(self, workspace) -> None:
        self.workspace = str(workspace)
        self.notification_listener = None
        self.stderr_listener = None
        self.exit_listener = None

    def subscribe_notifications(self, listener):
        self.notification_listener = listener

    def subscribe_stderr(self, listener):
        self.stderr_listener = listener

    def subscribe_exit(self, listener):
        self.exit_listener = listener

    def thread_list(self, *, limit=100):
        return {
            "threads": [
                {
                    "id": "thread-motion-v10",
                    "title": "Motion language",
                    "workspace": self.workspace,
                    "permissionMode": "workspace",
                    "status": "completed",
                    "currentTurnId": "turn-1",
                    "usage": {"totalTokens": 12},
                    "archived": False,
                }
            ][:limit],
            "counts": {"active": 1, "archived": 0, "all": 1},
        }

    def thread_read(self, thread_id):
        assert thread_id == "thread-motion-v10"
        return {
            "thread": {
                "id": thread_id,
                "title": "Motion language",
                "workspace": self.workspace,
                "permissionMode": "workspace",
                "status": "completed",
                "currentTurnId": "turn-1",
                "usage": {"totalTokens": 12},
                "archived": False,
            },
            "messages": [],
            "pendingApproval": None,
            "events": [],
            "turns": [],
            "finalText": "",
            "error": "",
        }

    def request(self, method, params):
        if method == "thread/list":
            return {"threads": [], "view": "archived", "counts": {"active": 1, "archived": 0, "all": 1}}
        raise AssertionError(f"unexpected request: {method} {params}")

    def close(self):
        return None


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
    raise AssertionError("Qt motion condition did not become true")


def test_v10_motion_tokens_and_late_controls_share_one_language(tmp_path, monkeypatch):
    monkeypatch.delenv("LOOM_REDUCE_MOTION", raising=False)
    app = QApplication.instance() or QApplication([])
    window = LoomDesktopWindow(
        client=FakeClient(tmp_path),
        initialization=_initialization(tmp_path),
        default_workspace=tmp_path,
        default_permission_mode="workspace",
    )
    window.show()
    try:
        _wait_for(app, lambda: window.current_thread_id == "thread-motion-v10")
        assert isinstance(window, LoomDesktopWindowV10)
        assert window.MOTION_MICRO_MS < window.MOTION_FAST_MS < window.MOTION_CONTENT_MS
        assert window.MOTION_CONTENT_MS < window.MOTION_PANEL_MS
        assert window.PANEL_DURATION_MS == window.MOTION_PANEL_MS
        assert window.COMPOSER_DURATION_MS == window.MOTION_FAST_MS
        assert window.TAB_DURATION_MS == window.MOTION_BASE_MS

        assert window.archive_view_button in window._button_glows
        assert window.thread_actions_button in window._button_glows
        assert window.thread_search in window._focus_glows
        assert isinstance(window.thread_search.graphicsEffect(), QGraphicsDropShadowEffect)

        window.archive_view_button.click()
        _wait_for(app, lambda: window._thread_view == "archived")
        assert window._thread_list_transition_pending in {True, False}
    finally:
        window.close()
        app.processEvents()
