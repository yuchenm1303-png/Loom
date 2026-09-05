from __future__ import annotations

import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QGraphicsOpacityEffect

from app.desktop_ui import LoomDesktopWindow


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
        return {
            "threads": [
                {
                    "id": "thread-1",
                    "title": "Motion test",
                    "workspace": self.workspace,
                    "permissionMode": "workspace",
                    "status": "completed",
                    "currentTurnId": "turn-1",
                    "usage": {"totalTokens": 12},
                }
            ][:limit]
        }

    def thread_read(self, thread_id):
        assert thread_id == "thread-1"
        return {
            "thread": {
                "id": "thread-1",
                "title": "Motion test",
                "workspace": self.workspace,
                "permissionMode": "workspace",
                "status": "completed",
                "currentTurnId": "turn-1",
                "usage": {"totalTokens": 12},
            },
            "messages": [],
            "pendingApproval": None,
            "events": [],
            "turns": [],
            "finalText": "",
            "error": "",
        }

    def thread_start(self, *, workspace, permission_mode):
        return {
            "thread": {
                "id": "thread-new",
                "title": "New thread",
                "workspace": str(workspace),
                "permissionMode": permission_mode,
                "status": "idle",
            }
        }

    def turn_start(self, thread_id, text):
        return {"turn": {"id": "turn-live", "threadId": thread_id, "status": "starting"}}

    def turn_interrupt(self, thread_id, turn_id=None):
        return {"requested": True, "threadId": thread_id, "turnId": turn_id}

    def approval_respond(self, thread_id, call_id, *, approved):
        return {"accepted": True, "threadId": thread_id, "callId": call_id, "approved": approved}

    def close(self):
        self.closed = True


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


def _wait_for(app, predicate, timeout=2.5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("Qt motion condition did not become true")


def _window(tmp_path):
    return LoomDesktopWindow(
        client=FakeClient(tmp_path),
        initialization=_initialization(tmp_path),
        default_workspace=tmp_path,
        default_permission_mode="workspace",
    )


def test_motion_system_animates_composer_panels_tabs_and_approval(tmp_path, monkeypatch):
    monkeypatch.delenv("LOOM_REDUCE_MOTION", raising=False)
    app = QApplication.instance() or QApplication([])
    window = _window(tmp_path)
    window.show()
    try:
        _wait_for(app, lambda: window.current_thread_id == "thread-1")
        assert window._motion_enabled is True

        compact_height = window.composer.height()
        window.composer.setPlainText("one\ntwo\nthree\nfour")
        _wait_for(app, lambda: window.composer.height() > compact_height)
        assert window.composer.height() <= 142

        window.toggle_sidebar()
        _wait_for(app, lambda: not window.sidebar_panel.isVisible())
        assert window._sidebar_visible is False
        window.toggle_sidebar()
        _wait_for(app, lambda: window.sidebar_panel.isVisible())
        assert window._sidebar_visible is True

        page = window.activity_tabs.widget(1)
        window.activity_tabs.setCurrentIndex(1)
        _wait_for(app, lambda: isinstance(page.graphicsEffect(), QGraphicsOpacityEffect))
        effect = page.graphicsEffect()
        assert isinstance(effect, QGraphicsOpacityEffect)
        _wait_for(app, lambda: effect.opacity() > 0.98)

        window._apply_pending_approval(
            {
                "callId": "call-1",
                "toolName": "run_workspace_command",
                "arguments": {"argv": ["python", "-V"]},
                "effect": "sensitive",
                "reason": "process execution requires approval",
            }
        )
        assert window.approval_frame.isVisible() is True
        _wait_for(app, lambda: window.approval_frame.maximumHeight() > 80)
    finally:
        window.close()
        app.processEvents()


def test_reduce_motion_switch_makes_panel_changes_immediate(tmp_path, monkeypatch):
    monkeypatch.setenv("LOOM_REDUCE_MOTION", "1")
    app = QApplication.instance() or QApplication([])
    window = _window(tmp_path)
    window.show()
    try:
        _wait_for(app, lambda: window.current_thread_id == "thread-1")
        assert window._motion_enabled is False
        window.toggle_runtime()
        app.processEvents()
        assert window._runtime_visible is False
        assert window.activity_panel.isVisible() is False
        assert not window._motion_animations
    finally:
        window.close()
        app.processEvents()
