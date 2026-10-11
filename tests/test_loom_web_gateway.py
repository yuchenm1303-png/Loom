import asyncio
import queue
import threading
import time

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient
from starlette.testclient import WebSocketTestSession
from starlette.websockets import WebSocketDisconnect

from services.loom_web_gateway import app as gateway

WAIT_SECONDS = 15


@pytest.fixture(autouse=True)
def realistic_websocket_sessions(monkeypatch):
    """Make Starlette's WebSocket test sessions behave like a real server.

    Two gaps matter here. A session waits for the next frame with no timeout, so a
    frame that never arrives hangs the whole run. And leaving a session cancels the
    app right after the disconnect, cutting the handler's cleanup short: the Host's
    "device offline" broadcast is dropped (the test then waits for it forever) or an
    interrupted asyncio.gather raises a CancelledError that anyio does not recognise.
    A real server lets disconnect cleanup run to completion.
    """
    init, receive, leave = WebSocketTestSession.__init__, WebSocketTestSession.receive, WebSocketTestSession.__exit__

    def __init__(self, app, scope, portal_factory):
        self.handler_finished = threading.Event()

        async def tracked(*asgi):
            try:
                await app(*asgi)
            finally:
                self.handler_finished.set()

        init(self, tracked, scope, portal_factory)

    def bounded_receive(self):
        outcome = queue.Queue()

        def wait():
            try:
                outcome.put((receive(self), None))
            except BaseException as error:  # delivered to the test thread below
                outcome.put((None, error))

        threading.Thread(target=wait, daemon=True).start()
        try:
            message, error = outcome.get(timeout=WAIT_SECONDS)
        except queue.Empty:
            raise AssertionError(f"no WebSocket frame arrived within {WAIT_SECONDS}s") from None
        if error is not None:
            raise error
        return message

    def __exit__(self, *args):
        self.close(1000)
        finished = self.handler_finished.wait(WAIT_SECONDS)
        try:
            return leave(self, *args)
        finally:
            if not finished and args[0] is None:
                raise AssertionError(f"the handler did not finish within {WAIT_SECONDS}s of the disconnect")

    monkeypatch.setattr(WebSocketTestSession, "__init__", __init__)
    monkeypatch.setattr(WebSocketTestSession, "receive", bounded_receive)
    monkeypatch.setattr(WebSocketTestSession, "__exit__", __exit__)


def test_public_bundles_are_compressed_but_account_responses_are_not(tmp_path, monkeypatch):
    monkeypatch.setattr(gateway, "STATIC_DIR", tmp_path.resolve())
    assets = tmp_path / "assets"
    assets.mkdir()
    text = ".loom { color: white; }\n" * 2000
    (assets / "portal-test.css").write_bytes(text.encode("utf-8"))
    with TestClient(gateway.app) as client:
        result = client.get("/assets/portal-test.css", headers={"Accept-Encoding": "gzip"})
        assert result.headers["Content-Encoding"] == "gzip"
        assert result.text == text
        assert int(result.headers["Content-Length"]) < len(text) // 5
        assert "immutable" in result.headers["Cache-Control"]
        assert "Content-Encoding" not in client.get("/api/healthz").headers


@pytest.fixture
def relay(monkeypatch):
    state = {"active": True, "calls": []}

    async def account(method, path, *, token="", json_body=None):
        state["calls"].append(path)
        if not state["active"]:
            return 401, {}
        if path == "/auth/relay-credential":
            return 200, {"relay_token": "relay-proof"}
        if path in {"/auth/me", "/auth/relay-me"}:
            return 200, {"user": {"id": 7}}
        raise AssertionError(path)

    monkeypatch.setenv("LOOM_WEB_ORIGIN", "https://testserver")
    monkeypatch.setattr(gateway, "_account_request", account)
    monkeypatch.setattr(gateway, "hub", gateway.RelayHub())
    gateway._refresh_tasks.clear()
    with TestClient(gateway.app) as client:
        yield client, state
    gateway._refresh_tasks.clear()


def browser(client, device="a"):
    return client.websocket_connect(f"/api/ws/browser?device={device}", headers={
        "origin": "https://testserver", "cookie": "loom_web_access=access"})


def host(client, device):
    connection = client.websocket_connect("/api/ws/device", headers={"authorization": "Bearer access"})
    return connection


def test_two_hosts_stay_online_and_requests_use_selected_device(relay):
    client, _ = relay
    with host(client, "a") as a, host(client, "b") as b:
        for socket, device in [(a, "a"), (b, "b")]:
            socket.send_json({"type": "device_hello", "device": {"id": device}})
            socket.send_json({"type": "ping"})
            assert socket.receive_json()["type"] == "pong"
        with browser(client, "a") as web:
            status = web.receive_json()
            assert status["online"] is True
            assert status["selectedDeviceId"] == "a"
            assert {d["id"] for d in status["devices"]} == {"a", "b"}
            web.send_json({"type": "invoke", "id": 1, "operation": "call", "args": []})
            frame = a.receive_json()
            assert frame["type"] == "invoke"
            b.send_json({"type": "notification", "payload": {"method": "wrong-host"}})
            a.send_json({"type": "notification", "payload": {"method": "right-host"}})
            assert web.receive_json()["payload"]["method"] == "right-host"
            b.send_json({"type": "invoke_result", "browserId": frame["browserId"], "id": 1, "result": "wrong"})
            b.send_json({"type": "ping"})
            assert b.receive_json()["type"] == "pong"
            a.send_json({"type": "invoke_result", "browserId": frame["browserId"], "id": 1, "result": "ok"})
            assert web.receive_json()["result"] == "ok"


@pytest.mark.parametrize("selected", ["missing", ""])
def test_missing_target_does_not_fall_back(relay, selected):
    client, _ = relay
    with host(client, "b") as b:
        b.send_json({"type": "device_hello", "device": {"id": "b"}})
        b.send_json({"type": "ping"})
        b.receive_json()
        with browser(client, selected) as web:
            assert web.receive_json()["online"] is False
            web.send_json({"type": "invoke", "id": 1, "operation": "call"})
            assert web.receive_json()["error"]["code"] == "HOST_OFFLINE"


def test_account_mismatch_reports_no_other_account_metadata_and_recovers(relay, monkeypatch):
    client, _ = relay
    original = gateway._account_request

    async def account(method, path, *, token="", json_body=None):
        if token in {"other-account", "other-proof"}:
            if path in {"/auth/me", "/auth/relay-me"}:
                return 200, {"user": {"id": 8, "email": "private@example.com"}}
            if path == "/auth/relay-credential":
                return 200, {"relay_token": "other-proof"}
        return await original(method, path, token=token, json_body=json_body)

    monkeypatch.setattr(gateway, "_account_request", account)
    with browser(client, "a") as web:
        assert web.receive_json()["accountMismatch"] is False
        with client.websocket_connect("/api/ws/device", headers={"authorization": "Bearer other-account"}) as other:
            other.send_json({"type": "device_hello", "device": {"id": "a", "name": "Private computer"}})
            status = web.receive_json()
            assert status == {
                "type": "device_status", "online": False, "accountMismatch": True,
                "selectedDeviceId": "a", "devices": [], "device": None,
            }
            web.send_json({"type": "invoke", "id": 1, "operation": "call"})
            assert web.receive_json()["error"]["code"] == "HOST_OFFLINE"
            # Another account's unrelated device never creates a mismatch.
            with browser(client, "unrelated") as unrelated:
                assert unrelated.receive_json()["accountMismatch"] is False
        status = web.receive_json()
        assert status["online"] is False and status["accountMismatch"] is False
        with host(client, "a") as matching:
            matching.send_json({"type": "device_hello", "device": {"id": "a"}})
            status = web.receive_json()
            assert status["online"] is True and status["accountMismatch"] is False


def test_open_connection_is_closed_when_session_is_revoked(relay, monkeypatch):
    client, state = relay
    monkeypatch.setattr(gateway, "AUTH_RECHECK_SECONDS", 0.02)
    with browser(client) as web:
        web.receive_json()
        state["active"] = False
        with pytest.raises(WebSocketDisconnect) as exc:
            web.receive_json()
        assert exc.value.code == 4401


@pytest.mark.parametrize("body", [[], None, 4, "text"])
def test_auth_rejects_non_object_json(relay, body):
    client, state = relay
    before = len(state["calls"])
    response = client.post("/api/auth/login", content=__import__("json").dumps(body), headers={"content-type": "application/json"})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_JSON"
    assert len(state["calls"]) == before


def test_concurrent_refresh_is_shared_and_rotation_survives(relay, monkeypatch):
    calls = []

    async def expired(_): return 401, None
    async def refresh(_):
        calls.append(1)
        await asyncio.sleep(0)
        return 200, {"user": {"id": 7}, "access_token": "next", "refresh_token": "next-refresh"}

    monkeypatch.setattr(gateway, "_authenticated_user", expired)
    monkeypatch.setattr(gateway, "_refresh", refresh)

    class Request:
        cookies = {gateway.ACCESS_COOKIE: "expired", gateway.REFRESH_COOKIE: "previous"}

    async def run():
        results = await asyncio.gather(*(gateway._browser_identity(Request()) for _ in range(2)))
        assert all(user["id"] == 7 and rotated["refresh_token"] == "next-refresh" for user, rotated, _ in results)
    asyncio.run(run())
    assert len(calls) == 1


def test_account_outage_does_not_refresh_valid_cookie(relay, monkeypatch):
    async def outage(_): return 503, None
    async def forbidden(_): raise AssertionError("must not refresh on outage")
    monkeypatch.setattr(gateway, "_authenticated_user", outage)
    monkeypatch.setattr(gateway, "_refresh", forbidden)
    client, _ = relay
    client.cookies.set(gateway.ACCESS_COOKIE, "access")
    client.cookies.set(gateway.REFRESH_COOKIE, "refresh")
    assert client.get("/api/auth/status").json()["snapshot"]["reachable"] is False


def test_slow_socket_send_has_a_timeout(monkeypatch):
    monkeypatch.setattr(gateway, "SEND_TIMEOUT_SECONDS", 0.01)

    class Socket:
        async def send_json(self, payload): await asyncio.Event().wait()
        async def close(self, **kwargs): pass

    async def run():
        identity = gateway.RelayIdentity("proof", 7, time.monotonic() + 60)
        peer = gateway.BrowserPeer("browser", 7, Socket(), "a", identity)
        assert await peer.send({"type": "pong"}) is False
        assert peer.closed is True
    asyncio.run(run())


def test_pending_requests_are_bounded(relay):
    client, _ = relay
    with host(client, "a") as a:
        a.send_json({"type": "device_hello", "device": {"id": "a"}})
        a.send_json({"type": "ping"})
        a.receive_json()
        with browser(client) as web:
            web.receive_json()
            for request_id in range(gateway.MAX_INFLIGHT_INVOKES):
                web.send_json({"type": "invoke", "id": request_id, "operation": "call"})
                assert a.receive_json()["id"] == request_id
            web.send_json({"type": "invoke", "id": 1000, "operation": "call"})
            assert web.receive_json()["error"]["code"] == "HOST_BUSY"


def test_legacy_host_history_is_windowed_over_bound_relay(relay):
    client, _ = relay
    with host(client, "a") as a:
        a.send_json({"type": "device_hello", "device": {"id": "a"}})
        a.send_json({"type": "ping"})
        a.receive_json()
        with browser(client) as web:
            web.receive_json()
            web.send_json({"type": "invoke", "id": 7, "operation": "call", "args": [
                "thread/read", {"turnLimit": 1, "presentationOnly": True},
            ]})
            frame = a.receive_json()
            a.send_json({"type": "invoke_result", "browserId": frame["browserId"], "id": 7,
                         "result": {"turns": [{"id": "old"}, {"id": "new"}],
                                    "messages": ["duplicate"], "events": ["duplicate"]}})
            result = web.receive_json()["result"]
            assert result == {"turns": [{"id": "new"}], "hasMoreTurns": True, "oldestTurnId": "new"}
