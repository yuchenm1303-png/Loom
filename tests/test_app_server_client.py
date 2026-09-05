from __future__ import annotations

import os
import sys
import textwrap
import threading

import pytest

from app.app_server_client import (
    AppServerProcessConfig,
    JsonRpcClientError,
    LoomAppServerClient,
)


_FAKE_SERVER = textwrap.dedent(
    '''
    import json
    import sys

    def send(payload):
        sys.stdout.write(json.dumps(payload, separators=(",", ":")) + "\\n")
        sys.stdout.flush()

    for raw in sys.stdin:
        request = json.loads(raw)
        method = request.get("method")
        if "id" not in request:
            if method == "initialized":
                sys.stderr.write("fake-server-ready\\n")
                sys.stderr.flush()
            continue
        request_id = request["id"]
        if method == "initialize":
            send({
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {
                    "protocolVersion": 1,
                    "serverInfo": {"name": "fake", "version": "0.1"},
                    "capabilities": {"providerStreaming": True},
                    "runtime": {
                        "defaultWorkspace": "/tmp/workspace",
                        "defaultPermissionMode": "workspace",
                    },
                },
            })
            send({
                "jsonrpc": "2.0",
                "method": "thread/started",
                "params": {"thread": {"id": "thread-1"}},
            })
        elif method == "runtime/status":
            send({
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {"model": "fake-model", "activeThreadIds": []},
            })
        elif method == "thread/list":
            send({
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {"threads": [{"id": "thread-1", "title": "Test"}]},
            })
        elif method == "explode":
            send({
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": -32009, "message": "state conflict", "data": {"retry": False}},
            })
        else:
            send({"jsonrpc": "2.0", "id": request_id, "result": {"method": method}})
    '''
)


def _client() -> LoomAppServerClient:
    return LoomAppServerClient(
        [sys.executable, "-u", "-c", _FAKE_SERVER],
        request_timeout_seconds=5.0,
    )


def test_process_config_preserves_app_server_auto_detection_and_does_not_carry_secrets(tmp_path):
    secret = "do-not-put-me-in-argv"
    os.environ["LOOM_API_KEY"] = secret
    try:
        command = AppServerProcessConfig(workspace=tmp_path).command()
    finally:
        os.environ.pop("LOOM_API_KEY", None)

    assert command[:3] == [sys.executable, "-m", "loom_app_server"]
    assert "--workspace" in command
    assert "--provider" not in command
    assert "--permission-mode" not in command
    assert secret not in command
    assert secret not in " ".join(command)


def test_client_initializes_routes_notifications_and_requests():
    client = _client()
    notifications = []
    stderr_lines = []
    notification_seen = threading.Event()

    def on_notification(method, params):
        notifications.append((method, params))
        notification_seen.set()

    client.subscribe_notifications(on_notification)
    client.subscribe_stderr(stderr_lines.append)
    try:
        initialized = client.start_and_initialize(client_name="desktop-test")
        status = client.runtime_status()
        threads = client.thread_list(limit=10)

        assert initialized["protocolVersion"] == 1
        assert initialized["capabilities"]["providerStreaming"] is True
        assert status["model"] == "fake-model"
        assert threads["threads"][0]["id"] == "thread-1"
        assert notification_seen.wait(timeout=2.0)
        assert notifications[0][0] == "thread/started"
        assert notifications[0][1]["thread"]["id"] == "thread-1"

        deadline = threading.Event()
        for _ in range(100):
            if "fake-server-ready" in client.stderr_tail:
                break
            deadline.wait(0.01)
        assert "fake-server-ready" in client.stderr_tail
        assert "fake-server-ready" in stderr_lines
    finally:
        client.close()

    assert client.running is False


def test_client_surfaces_json_rpc_error_without_losing_connection():
    client = _client()
    try:
        client.start_and_initialize(client_name="desktop-test")
        with pytest.raises(JsonRpcClientError) as exc_info:
            client.request("explode", {})
        assert exc_info.value.code == -32009
        assert exc_info.value.data == {"retry": False}
        assert client.runtime_status()["model"] == "fake-model"
    finally:
        client.close()
