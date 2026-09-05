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
        self.approvals = []
        self.turns = []

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
                    "id": "thread-1",
                    "title": "Inspect project",
                    "workspace": self.workspace,
                    "permissionMode": "workspace",
                    "status": "completed",
                    "currentTurnId": "turn-1",
                    "usage": {"inputTokens": 4, "outputTokens": 3, "totalTokens": 7},
                }
            ]
        }

    def thread_read(self, thread_id):
        assert thread_id == "thread-1"
        return {
            "thread": {
                "id": "thread-1",
                "title": "Inspect project",
                "workspace": self.workspace,
                "permissionMode": "workspace",
                "status": "completed",
                "currentTurnId": "turn-1",
                "usage": {"inputTokens": 4, "outputTokens": 3, "totalTokens": 7},
            },
            "messages": [
                {"role": "user", "content": "Inspect the repository"},
                {"role": "assistant", "content": "The repository is ready."},
            ],
            "pendingApproval": None,
            "events": [
                {
                    "eventId": "evt-1",
                    "threadId": "thread-1",
                    "turnId": "turn-1",
                    "kind": "process_started",
                    "createdAt": "2026-09-05T12:00:00+00:00",
                    "data": {"process_id": "proc-1"},
                },
                {
                    "eventId": "evt-2",
                    "threadId": "thread-1",
                    "turnId": "turn-1",
                    "kind": "turn_diff_updated",
                    "createdAt": "2026-09-05T12:00:01+00:00",
                    "data": {"paths": ["demo.txt"]},
                },
            ],
            "turns": [
                {
                    "id": "turn-1",
                    "threadId": "thread-1",
                    "status": "completed",
                    "items": [
                        {
                            "id": "process:proc-1",
                            "threadId": "thread-1",
                            "turnId": "turn-1",
                            "type": "process",
                            "status": "completed",
                            "processId": "proc-1",
                            "argv": ["python", "-V"],
                            "cwd": self.workspace,
                            "stdout": "Python 3.12.10\n",
                            "stderr": "",
                            "sandbox": {"enforced": False, "backend": "none"},
                        },
                        {
                            "id": "diff:evt-2",
                            "threadId": "thread-1",
                            "turnId": "turn-1",
                            "type": "file_edit",
                            "status": "completed",
                            "paths": ["demo.txt"],
                            "diff": "+hello\n",
                            "truncated": False,
                        },
                        {
                            "id": "tool:browser-1",
                            "threadId": "thread-1",
                            "turnId": "turn-1",
                            "type": "tool_call",
                            "status": "completed",
                            "toolName": "browser_navigate",
                            "arguments": {"url": "https://example.com"},
                        },
                        {
                            "id": "tool:agent-1",
                            "threadId": "thread-1",
                            "turnId": "turn-1",
                            "type": "tool_call",
                            "status": "completed",
                            "toolName": "spawn_agent",
                            "arguments": {"task": "review"},
                        },
                    ],
                }
            ],
            "finalText": "The repository is ready.",
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
        self.turns.append((thread_id, text))
        return {"turn": {"id": "turn-live", "threadId": thread_id, "status": "starting"}}

    def turn_interrupt(self, thread_id, turn_id=None):
        return {"requested": True, "threadId": thread_id, "turnId": turn_id}

    def approval_respond(self, thread_id, call_id, *, approved):
        self.approvals.append((thread_id, call_id, approved))
        return {"accepted": True}

    def close(self):
        self.closed = True

    def emit(self, method, params):
        assert self.notification_listener is not None
        self.notification_listener(method, params)


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


def test_native_window_rehydrates_thread_and_protocol_backed_panels(tmp_path):
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
        _wait_for(app, lambda: window.current_thread_id == "thread-1")
        assert "Inspect the repository" in window.transcript.toPlainText()
        assert "The repository is ready." in window.transcript.toPlainText()
        assert "python -V" in window.terminal_view.toPlainText()
        assert "demo.txt" in window.diff_view.toPlainText()
        assert "browser_navigate" in window.browser_view.toPlainText()
        assert "spawn_agent" in window.agents_view.toPlainText()
        assert "process started" in window.activity_view.toPlainText()
        assert window.thread_title_label.text() == "Inspect project"
        assert window.permission_label.text() == "workspace"
        assert window.status_label.text() == "Completed"
        assert window.status_label.property("state") == "completed"
        assert window.sandbox_label.text() == "Not sandboxed · none"
        assert window.sandbox_label.property("state") == "unprotected"
        assert "Provider streaming · on" in window.protocol_label.text()
        assert window.connection_dot.property("state") == "connected"
        assert window.thread_list.count() == 1
        assert isinstance(
            window.thread_list.itemWidget(window.thread_list.item(0)),
            ThreadListItemWidget,
        )
    finally:
        window.close()
        app.processEvents()
    assert client.closed is True


def test_native_window_renders_streaming_assistant_and_approval_card(tmp_path):
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
        _wait_for(app, lambda: window.current_thread_id == "thread-1")
        client.emit(
            "turn/started",
            {"threadId": "thread-1", "turn": {"id": "turn-live", "status": "running"}},
        )
        client.emit(
            "item/started",
            {
                "item": {
                    "id": "assistant:step:step-live",
                    "threadId": "thread-1",
                    "turnId": "turn-live",
                    "type": "assistant_message",
                    "status": "streaming",
                }
            },
        )
        client.emit(
            "item/delta",
            {
                "threadId": "thread-1",
                "turnId": "turn-live",
                "itemId": "assistant:step:step-live",
                "delta": {"text": "Live provider chunk"},
            },
        )
        _wait_for(app, lambda: "Live provider chunk" in window.transcript.toPlainText())
        assert window.status_label.text() == "Running"
        assert window.status_label.property("state") == "running"
        assert window.composer_state_label.text() == "Loom is working"
        assert window.stop_button.isEnabled() is True

        client.emit(
            "approval/requested",
            {
                "threadId": "thread-1",
                "turnId": "turn-live",
                "approval": {
                    "callId": "call-approval",
                    "toolName": "run_workspace_command",
                    "arguments": {"argv": ["python", "-V"]},
                    "effect": "sensitive",
                    "reason": "process execution requires approval",
                },
            },
        )
        app.processEvents()
        assert window.approval_frame.isVisible() is True
        assert "run_workspace_command" in window.approval_title.text()
        assert "python" in window.approval_details.text()
        assert window.status_label.text() == "Waiting Approval"
        assert window.status_label.property("state") == "waiting_approval"
        assert window.composer_state_label.text() == "Waiting for approval"
    finally:
        window.close()
        app.processEvents()
