from __future__ import annotations

import json
import socket
import threading
import time
from urllib.error import HTTPError
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.request import Request, urlopen

import pytest

from app.agent_runtime.browser_extension_bridge import BrowserExtensionBridge
from app.agent_runtime.browser_session import BrowserError


TOKEN = "shared-bridge-test-credential"


def request(bridge, path, body=None):
    if path.startswith("poll?"):
        path += "&version=0.1.19"
    data = json.dumps(body).encode() if body is not None else None
    with urlopen(Request(bridge.url + "/browser-extension/v1/" + path, data=data,
                         headers={"X-Loom-Token": TOKEN, "Content-Type": "application/json"}), timeout=5) as response:
        return json.load(response)


def until(predicate):
    deadline = time.monotonic() + 3
    while not predicate():
        assert time.monotonic() < deadline, "bridge operation did not become ready"
        time.sleep(0.01)


@pytest.fixture
def broker():
    service = BrowserExtensionBridge(port=0, token=TOKEN, poll_timeout=1)
    service.start()
    yield service
    service.stop()


def client(broker):
    instance = BrowserExtensionBridge(port=broker.port, token=TOKEN, command_timeout=5)
    instance.start()
    assert instance.port_conflict == ""
    return instance


def finish(bridge, command, value):
    args = command["args"]
    request(bridge, "result", {"id": command["id"], "ok": True, "result": {
        "value": value, "page_info": {"tab_id": "7", "session_id": args["session_id"], "broker_id": args["broker_id"]},
    }})


def call_in_thread(instance, output, key):
    def call():
        try:
            output[key] = instance.call("state", {"session_id": "same-local-id"})
        except BrowserError as exc:
            output[key] = str(exc)
    thread = threading.Thread(target=call)
    thread.start()
    return thread


def test_remote_commands_share_one_listener_and_route_results_independently(broker):
    a, b = client(broker), client(broker)
    results = {}
    threads = [call_in_thread(a, results, "a"), call_in_thread(b, results, "b")]
    try:
        until(lambda: broker.status()["pending_commands"] == 2)
        commands = [request(broker, "poll?client_id=extension")["command"] for _ in range(2)]
        assert commands[0]["args"]["session_id"] != commands[1]["args"]["session_id"]
        for command in reversed(commands):
            owner = "a" if command["args"]["session_id"].startswith(a._runtime_id + ":") else "b"
            finish(broker, command, owner)
        for thread in threads:
            thread.join(3)
            assert not thread.is_alive()
        assert results["a"]["value"] == "a"
        assert results["b"]["value"] == "b"
        assert results["a"]["page_info"]["session_id"] == "same-local-id"
        assert a.connected and b.connected
        assert a.status()["bridge_role"] == b.status()["bridge_role"] == "client"
    finally:
        a.stop()
        b.stop()


def test_detach_cancels_only_own_pending_command_and_releases_only_own_session(broker):
    a, b = client(broker), client(broker)
    output = {}
    threads = [call_in_thread(a, output, "a"), call_in_thread(b, output, "b")]
    try:
        until(lambda: broker.status()["pending_commands"] == 2)
        a.stop()
        threads[0].join(3)
        assert output["a"] == "browser runtime disconnected"
        commands = [request(broker, "poll?client_id=extension")["command"] for _ in range(2)]
        state = next(command for command in commands if command["action"] == "state")
        release = next(command for command in commands if command["action"] == "release_tabs")
        assert state["args"]["session_id"].startswith(b._runtime_id + ":")
        assert release["args"]["session_id"].startswith(a._runtime_id + ":")
        finish(broker, state, "still working")
        request(broker, "result", {"id": release["id"], "ok": True, "result": {"released": 1}})
        threads[1].join(3)
        assert output["b"]["value"] == "still working"
        assert broker.status()["runtime_clients"] == 1
    finally:
        a.stop()
        b.stop()


def test_disconnected_clients_are_reaped_without_touching_a_live_client(broker):
    a, b = client(broker), client(broker)
    try:
        broker._runtime_sessions[a._runtime_id] = {a._runtime_id + ":old-tab"}
        broker._runtime_clients[a._runtime_id] -= 100
        assert broker.reap_runtimes() == 1
        assert b._runtime_id in broker._runtime_clients
        command = request(broker, "poll?client_id=extension")["command"]
        assert command["action"] == "release_tabs"
        assert command["args"]["session_id"] == a._runtime_id + ":old-tab"
        request(broker, "result", {"id": command["id"], "ok": True, "result": {}})
    finally:
        a.stop()
        b.stop()


def test_incompatible_pairing_is_not_misreported_as_capacity_busy(broker):
    other = BrowserExtensionBridge(port=broker.port, token="different-credential")
    try:
        other.start()
        assert other._server is None
        assert "different pairing credential" in other.port_conflict
        with pytest.raises(BrowserError, match="different pairing credential"):
            other.call("state")
    finally:
        other.stop()


def test_web_origins_cannot_use_runtime_command_endpoint(broker):
    instance = client(broker)
    try:
        req = Request(broker.url + "/browser-extension/v1/runtime/call", data=b"{}", headers={
            "X-Loom-Token": TOKEN, "X-Loom-Runtime-ID": instance._runtime_id,
            "Origin": "https://untrusted.example",
        })
        with pytest.raises(HTTPError) as error:
            urlopen(req, timeout=3)
        assert error.value.code == 401
        assert broker.status()["pending_commands"] == 0
    finally:
        instance.stop()


def test_old_extension_never_receives_shared_commands(broker):
    instance = client(broker)
    output = {}
    caller = call_in_thread(instance, output, "old")
    try:
        until(lambda: broker.status()["pending_commands"] == 1)
        with urlopen(Request(broker.url + "/browser-extension/v1/poll?client_id=old&version=0.1.18",
                             headers={"X-Loom-Token": TOKEN}), timeout=3) as response:
            assert json.load(response)["command"] is None
        caller.join(3)
        assert "0.1.19 or newer" in output["old"]
    finally:
        instance.stop()


@pytest.mark.parametrize("health", [{"ok": True, "protocol_version": 1}, {"other_service": True}])
def test_old_or_unrelated_listener_is_not_silently_taken_over(health):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(json.dumps(health).encode())
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    instance = BrowserExtensionBridge(port=server.server_port, token=TOKEN, shared_service=True)
    try:
        instance.start()
        assert instance._server is None
        expected = "does not support shared runtimes" if health.get("ok") else "not a compatible Loom browser bridge"
        assert expected in instance.port_conflict
    finally:
        instance.stop()
        server.shutdown()
        server.server_close()


@pytest.mark.parametrize("concurrent", [False, True])
def test_standalone_service_survives_first_runtime_detach(monkeypatch, concurrent):
    import app.agent_runtime.browser_extension_bridge as module

    processes = []
    original = module.subprocess.Popen
    def launch(*args, **kwargs):
        process = original(*args, **kwargs)
        processes.append(process)
        return process
    monkeypatch.setattr(module.subprocess, "Popen", launch)
    with socket.socket() as available:
        available.bind(("127.0.0.1", 0))
        port = available.getsockname()[1]
    a = BrowserExtensionBridge(port=port, token=TOKEN, shared_service=True)
    b = BrowserExtensionBridge(port=port, token=TOKEN, shared_service=True)
    try:
        if concurrent:
            starts = [threading.Thread(target=a.start), threading.Thread(target=b.start)]
            for thread in starts:
                thread.start()
            for thread in starts:
                thread.join(8)
                assert not thread.is_alive()
        else:
            a.start()
            b.start()
        assert a.port_conflict == b.port_conflict == ""
        assert a._remote and b._remote
        assert 1 <= len(processes) <= (2 if concurrent else 1)
        a.stop()
        assert any(process.poll() is None for process in processes)
        assert b.status()["runtime_clients"] == 1
        output = {}
        caller = call_in_thread(b, output, "b")
        command = request(b, "poll?client_id=extension")["command"]
        finish(b, command, "works after first runtime stops")
        caller.join(3)
        assert output["b"]["value"] == "works after first runtime stops"
    finally:
        a.stop()
        b.stop()
        for process in processes:
            if process.poll() is None:
                process.terminate()
            process.wait(timeout=5)
