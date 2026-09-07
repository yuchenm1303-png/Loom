from __future__ import annotations

import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtCore import QUrl
from PySide6.QtWidgets import QApplication

from app.desktop_ui import LoomDesktopWindow


class FlowClient:
    def __init__(self, workspace) -> None:
        self.workspace = str(workspace)
        self.notification_listener = None
        self.closed = False

    def subscribe_notifications(self, listener):
        self.notification_listener = listener

    def subscribe_stderr(self, _listener):
        return None

    def subscribe_exit(self, _listener):
        return None

    def thread_list(self, *, limit=100):
        assert limit >= 1
        return {
            "threads": [
                {
                    "id": "thread-flow",
                    "title": "Flow demo",
                    "workspace": self.workspace,
                    "permissionMode": "workspace",
                    "status": "completed",
                    "currentTurnId": "turn-flow",
                    "usage": {"inputTokens": 10, "outputTokens": 4, "totalTokens": 14},
                }
            ]
        }

    def thread_read(self, thread_id):
        assert thread_id == "thread-flow"
        return {
            "thread": {
                "id": "thread-flow",
                "title": "Flow demo",
                "workspace": self.workspace,
                "permissionMode": "workspace",
                "status": "completed",
                "currentTurnId": "turn-flow",
                "usage": {"inputTokens": 10, "outputTokens": 4, "totalTokens": 14},
            },
            "messages": [],
            "pendingApproval": None,
            "events": [
                {
                    "eventId": "evt-compact",
                    "threadId": "thread-flow",
                    "turnId": None,
                    "kind": "context_checkpointed",
                    "createdAt": "2026-09-07T12:00:20+00:00",
                    "data": {
                        "summary_source": "model",
                        "archived_messages": 30,
                        "retained_messages": 12,
                    },
                }
            ],
            "turns": [
                {
                    "id": "turn-flow",
                    "threadId": "thread-flow",
                    "status": "completed",
                    "source": "user",
                    "startedAt": "2026-09-07T12:00:00+00:00",
                    "completedAt": "2026-09-07T12:00:08+00:00",
                    "items": [
                        {
                            "id": "user:flow",
                            "threadId": "thread-flow",
                            "turnId": "turn-flow",
                            "type": "user_message",
                            "status": "completed",
                            "createdAt": "2026-09-07T12:00:00+00:00",
                            "text": "Inspect it",
                        },
                        {
                            "id": "process:flow",
                            "threadId": "thread-flow",
                            "turnId": "turn-flow",
                            "type": "process",
                            "status": "completed",
                            "createdAt": "2026-09-07T12:00:02+00:00",
                            "processId": "flow",
                            "argv": ["python", "-V"],
                            "cwd": self.workspace,
                            "stdout": "Python 3.12\n",
                            "returncode": 0,
                            "sandbox": {"enforced": False, "backend": "none"},
                        },
                        {
                            "id": "tool:browser-flow",
                            "threadId": "thread-flow",
                            "turnId": "turn-flow",
                            "type": "tool_call",
                            "status": "completed",
                            "createdAt": "2026-09-07T12:00:04+00:00",
                            "toolName": "browser_navigate",
                            "arguments": {"url": "https://example.com"},
                            "ok": True,
                        },
                        {
                            "id": "diff:flow",
                            "threadId": "thread-flow",
                            "turnId": "turn-flow",
                            "type": "file_edit",
                            "status": "completed",
                            "createdAt": "2026-09-07T12:00:06+00:00",
                            "paths": ["app/demo.py"],
                            "diff": "+hello\n",
                        },
                        {
                            "id": "assistant:flow",
                            "threadId": "thread-flow",
                            "turnId": "turn-flow",
                            "type": "assistant_message",
                            "status": "completed",
                            "createdAt": "2026-09-07T12:00:07+00:00",
                            "text": "Finished.",
                        },
                    ],
                }
            ],
            "finalText": "Finished.",
            "error": "",
        }

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
    raise AssertionError("desktop flow condition did not become true")


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


def test_central_transcript_interleaves_runtime_items_and_supports_detail_toggle(tmp_path):
    app = QApplication.instance() or QApplication([])
    client = FlowClient(tmp_path)
    window = LoomDesktopWindow(
        client=client,
        initialization=_initialization(tmp_path),
        default_workspace=tmp_path,
        default_permission_mode="workspace",
    )
    window.show()
    try:
        _wait_for(app, lambda: window.current_thread_id == "thread-flow")
        text = window.transcript.toPlainText()
        assert "Worked for 8s" in text
        assert "Inspect it" in text
        assert "Ran command" in text
        assert "$ python -V" in text
        assert "Browser · Navigate" in text
        assert "https://example.com" in text
        assert "Changed 1 file" in text
        assert "app/demo.py" in text
        assert "Finished." in text
        assert "Context automatically compacted" in text
        assert "archived 30 · retained 12" in text

        window._on_flow_anchor(QUrl("loom://item/process%3Aflow"))
        app.processEvents()
        expanded = window.transcript.toPlainText()
        assert "Python 3.12" in expanded
        assert "sandbox: not enforced · none" in expanded
        assert "process:flow" in window._flow_expanded_items
    finally:
        window.close()
        app.processEvents()
    assert client.closed is True


def test_live_process_activity_appears_before_snapshot_reconciliation(tmp_path):
    app = QApplication.instance() or QApplication([])
    client = FlowClient(tmp_path)
    window = LoomDesktopWindow(
        client=client,
        initialization=_initialization(tmp_path),
        default_workspace=tmp_path,
        default_permission_mode="workspace",
    )
    window.show()
    try:
        _wait_for(app, lambda: window.current_thread_id == "thread-flow")
        client.emit(
            "turn/started",
            {
                "threadId": "thread-flow",
                "turn": {
                    "id": "turn-live",
                    "threadId": "thread-flow",
                    "status": "running",
                    "startedAt": "2026-09-07T12:01:00+00:00",
                },
            },
        )
        client.emit(
            "item/started",
            {
                "item": {
                    "id": "process:live",
                    "threadId": "thread-flow",
                    "turnId": "turn-live",
                    "type": "process",
                    "status": "running",
                    "createdAt": "2026-09-07T12:01:01+00:00",
                    "processId": "live",
                    "argv": ["pytest", "-q"],
                    "cwd": str(tmp_path),
                }
            },
        )
        _wait_for(app, lambda: "$ pytest -q" in window.transcript.toPlainText())
        assert "Running command" in window.transcript.toPlainText()

        client.emit(
            "item/delta",
            {
                "threadId": "thread-flow",
                "turnId": "turn-live",
                "itemId": "process:live",
                "delta": {"stdout": "2 passed\n"},
            },
        )
        window._on_flow_anchor(QUrl("loom://item/process%3Alive"))
        app.processEvents()
        assert "2 passed" in window.transcript.toPlainText()
    finally:
        window.close()
        app.processEvents()
