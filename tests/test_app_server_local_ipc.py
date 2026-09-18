from __future__ import annotations

import json
import socket

import pytest

from app.app_server import LoomRpcController
from app.app_server_client import AppServerClientError
from app.app_server_local_ipc import (
    LocalAppServerIpcServer,
    LocalAppServerSecurityError,
    LocalAppServerUnavailable,
    LoomLocalAppServerClient,
    local_app_server_descriptor_path,
)


class FakeService:
    def __init__(self, model="shared-runtime"):
        self.model = model
        self.turn_start_attempts = 0
        self.turn_start_executions = 0
        self.turn_steer_executions = 0
        self._turn_replays = {}

    def runtime_status(self):
        return {"model": self.model, "activeThreadIds": []}

    def turn_start(self, params):
        self.turn_start_attempts += 1
        key = str(params.get("clientInputId") or "")
        if key and key in self._turn_replays:
            return {
                "turn": {"id": self._turn_replays[key], "status": "starting"},
                "idempotentReplay": True,
            }
        self.turn_start_executions += 1
        turn_id = f"turn-{self.turn_start_executions}"
        if key:
            self._turn_replays[key] = turn_id
        return {
            "turn": {"id": turn_id, "status": "starting"},
            "idempotentReplay": False,
        }

    def turn_steer(self, params):
        self.turn_steer_executions += 1
        return {
            "threadId": str(params.get("threadId") or ""),
            "turnId": str(params.get("turnId") or ""),
            "accepted": True,
        }


class FakeController(LoomRpcController):
    """Exercise the transport without pretending FakeService is a full App Server."""

    def _dispatch(self, method, params):
        if method == "runtime/status":
            return self.service.runtime_status()
        if method == "turn/start":
            return self.service.turn_start(params)
        if method == "turn/steer":
            return self.service.turn_steer(params)
        raise ValueError(f"unsupported fake method: {method}")


def test_local_ipc_shares_one_service_and_cleans_descriptor(tmp_path):
    service = FakeService()
    server = LocalAppServerIpcServer(
        service,
        controller_factory=FakeController,
        runtime_home=tmp_path,
    )
    descriptor = server.start()
    target = local_app_server_descriptor_path(tmp_path)
    try:
        assert descriptor["host"] == "127.0.0.1"
        assert descriptor["transport"] == "jsonl-tcp-loopback"
        assert target.exists()

        client = LoomLocalAppServerClient(tmp_path, request_timeout_seconds=2)
        try:
            initialized = client.connect_and_initialize(
                client_name="pytest-local-remote",
                client_version="1",
            )
            status = client.runtime_status()
        finally:
            client.close()

        assert initialized["protocolVersion"] == 1
        assert status["model"] == "shared-runtime"
    finally:
        server.close()

    assert not target.exists()


def test_local_ipc_rejects_tampered_descriptor_token(tmp_path):
    server = LocalAppServerIpcServer(
        FakeService(),
        controller_factory=FakeController,
        runtime_home=tmp_path,
    )
    server.start()
    target = local_app_server_descriptor_path(tmp_path)
    original = json.loads(target.read_text(encoding="utf-8"))
    tampered = dict(original)
    tampered["token"] = "not-the-real-token"
    target.write_text(json.dumps(tampered), encoding="utf-8")

    client = LoomLocalAppServerClient(tmp_path, request_timeout_seconds=1)
    try:
        with pytest.raises(LocalAppServerSecurityError):
            client.connect()
    finally:
        client.close()
        target.write_text(json.dumps(original), encoding="utf-8")
        server.close()


def test_local_client_refuses_non_loopback_descriptor(tmp_path):
    target = local_app_server_descriptor_path(tmp_path)
    target.parent.mkdir(parents=True)
    target.write_text(
        json.dumps(
            {
                "version": 1,
                "transport": "jsonl-tcp-loopback",
                "host": "0.0.0.0",
                "port": 12345,
                "token": "secret",
                "pid": 1,
            }
        ),
        encoding="utf-8",
    )

    client = LoomLocalAppServerClient(tmp_path, request_timeout_seconds=1)
    with pytest.raises(LocalAppServerSecurityError):
        client.connect()


def test_missing_local_descriptor_is_the_only_normal_fallback_state(tmp_path):
    client = LoomLocalAppServerClient(tmp_path, request_timeout_seconds=1)

    with pytest.raises(LocalAppServerUnavailable):
        client.connect()


def test_published_but_unreachable_endpoint_fails_closed(tmp_path):
    import socket

    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.bind(("127.0.0.1", 0))
    _host, port = probe.getsockname()
    probe.close()

    target = local_app_server_descriptor_path(tmp_path)
    target.parent.mkdir(parents=True)
    target.write_text(
        json.dumps(
            {
                "version": 1,
                "transport": "jsonl-tcp-loopback",
                "host": "127.0.0.1",
                "port": port,
                "token": "published-but-dead",
                "pid": 1,
            }
        ),
        encoding="utf-8",
    )

    client = LoomLocalAppServerClient(tmp_path, request_timeout_seconds=1)
    with pytest.raises(LocalAppServerSecurityError, match="refusing to start a second Runtime"):
        client.connect()


def test_only_one_local_app_server_can_own_control_endpoint(tmp_path):
    first = LocalAppServerIpcServer(
        FakeService(),
        controller_factory=FakeController,
        runtime_home=tmp_path,
    )
    second = LocalAppServerIpcServer(
        FakeService(),
        controller_factory=FakeController,
        runtime_home=tmp_path,
    )

    first.start()
    try:
        with pytest.raises(RuntimeError, match="already owns"):
            second.start()
    finally:
        second.close()
        first.close()

    replacement = LocalAppServerIpcServer(
        FakeService(),
        controller_factory=FakeController,
        runtime_home=tmp_path,
    )
    try:
        descriptor = replacement.start()
        assert descriptor["host"] == "127.0.0.1"
    finally:
        replacement.close()


def test_local_client_reconnects_when_canonical_descriptor_changes(tmp_path):
    first = LocalAppServerIpcServer(
        FakeService("runtime-one"),
        controller_factory=FakeController,
        runtime_home=tmp_path,
    )
    first.start()
    client = LoomLocalAppServerClient(tmp_path, request_timeout_seconds=2)
    try:
        client.connect_and_initialize(
            client_name="pytest-reconnect",
            client_version="1",
        )
        assert client.runtime_status()["model"] == "runtime-one"

        first.close()
        second = LocalAppServerIpcServer(
            FakeService("runtime-two"),
            controller_factory=FakeController,
            runtime_home=tmp_path,
        )
        second.start()
        try:
            assert client.runtime_status()["model"] == "runtime-two"
        finally:
            second.close()
    finally:
        client.close()
        first.close()


def test_local_client_reports_offline_when_descriptor_disappears(tmp_path):
    server = LocalAppServerIpcServer(
        FakeService(),
        controller_factory=FakeController,
        runtime_home=tmp_path,
    )
    server.start()
    client = LoomLocalAppServerClient(tmp_path, request_timeout_seconds=1)
    try:
        client.connect_and_initialize(client_name="pytest-offline")
        server.close()

        with pytest.raises(LocalAppServerUnavailable):
            client.runtime_status()

        assert client.running is False
    finally:
        client.close()
        server.close()


def test_connected_client_fails_closed_when_descriptor_is_tampered(tmp_path):
    server = LocalAppServerIpcServer(
        FakeService(),
        controller_factory=FakeController,
        runtime_home=tmp_path,
    )
    server.start()
    client = LoomLocalAppServerClient(tmp_path, request_timeout_seconds=1)
    target = local_app_server_descriptor_path(tmp_path)
    original = json.loads(target.read_text(encoding="utf-8"))
    try:
        client.connect_and_initialize(client_name="pytest-tamper")
        tampered = dict(original)
        tampered["token"] = "attacker-replaced-token"
        target.write_text(json.dumps(tampered), encoding="utf-8")

        with pytest.raises(LocalAppServerSecurityError):
            client.runtime_status()

        assert client.running is False
    finally:
        client.close()
        target.write_text(json.dumps(original), encoding="utf-8")
        server.close()


def test_read_request_recovers_from_stale_idle_socket(tmp_path):
    server = LocalAppServerIpcServer(
        FakeService("recovered-read"),
        controller_factory=FakeController,
        runtime_home=tmp_path,
    )
    server.start()
    client = LoomLocalAppServerClient(tmp_path, request_timeout_seconds=1)
    try:
        client.connect_and_initialize(client_name="pytest-read-retry")
        assert client._socket is not None
        client._socket.shutdown(socket.SHUT_RDWR)

        status = client.runtime_status()

        assert status["model"] == "recovered-read"
        assert client.running is True
    finally:
        client.close()
        server.close()


def test_unsafe_write_is_not_blindly_replayed_after_transport_loss(tmp_path, monkeypatch):
    service = FakeService()
    server = LocalAppServerIpcServer(
        service,
        controller_factory=FakeController,
        runtime_home=tmp_path,
    )
    server.start()
    client = LoomLocalAppServerClient(tmp_path, request_timeout_seconds=1)
    try:
        client.connect_and_initialize(client_name="pytest-write-no-retry")
        original = client._request_once_locked
        attempts = 0

        def lose_response(method, params, *, timeout_seconds=None):
            nonlocal attempts
            if method == "turn/steer":
                attempts += 1
                service.turn_steer(params)
                raise AppServerClientError("simulated response loss")
            return original(method, params, timeout_seconds=timeout_seconds)

        monkeypatch.setattr(client, "_request_once_locked", lose_response)

        with pytest.raises(AppServerClientError, match="refused to replay"):
            client.turn_steer(
                "thread-1",
                "turn-1",
                "continue",
                client_input_id="steer-key-is-not-durable-here",
            )

        assert attempts == 1
        assert service.turn_steer_executions == 1
    finally:
        client.close()
        server.close()


def test_durable_turn_start_retries_safely_after_response_loss(tmp_path, monkeypatch):
    service = FakeService()
    server = LocalAppServerIpcServer(
        service,
        controller_factory=FakeController,
        runtime_home=tmp_path,
    )
    server.start()
    client = LoomLocalAppServerClient(tmp_path, request_timeout_seconds=1)
    try:
        client.connect_and_initialize(client_name="pytest-durable-retry")
        original = client._request_once_locked
        dropped = False

        def lose_first_start_response(method, params, *, timeout_seconds=None):
            nonlocal dropped
            if method == "turn/start" and not dropped:
                dropped = True
                service.turn_start(params)
                raise AppServerClientError("simulated response loss after durable adoption")
            return original(method, params, timeout_seconds=timeout_seconds)

        monkeypatch.setattr(
            client,
            "_request_once_locked",
            lose_first_start_response,
        )

        result = client.turn_start(
            "thread-1",
            "execute once",
            client_input_id="durable-start-key",
        )

        assert result["turn"]["id"] == "turn-1"
        assert result["idempotentReplay"] is True
        assert service.turn_start_attempts == 2
        assert service.turn_start_executions == 1
    finally:
        client.close()
        server.close()


def test_unkeyed_turn_start_is_not_retried_after_response_loss(tmp_path, monkeypatch):
    service = FakeService()
    server = LocalAppServerIpcServer(
        service,
        controller_factory=FakeController,
        runtime_home=tmp_path,
    )
    server.start()
    client = LoomLocalAppServerClient(tmp_path, request_timeout_seconds=1)
    try:
        client.connect_and_initialize(client_name="pytest-unkeyed-no-retry")
        original = client._request_once_locked
        attempts = 0

        def lose_response(method, params, *, timeout_seconds=None):
            nonlocal attempts
            if method == "turn/start":
                attempts += 1
                service.turn_start(params)
                raise AppServerClientError("simulated response loss")
            return original(method, params, timeout_seconds=timeout_seconds)

        monkeypatch.setattr(client, "_request_once_locked", lose_response)

        with pytest.raises(AppServerClientError, match="refused to replay"):
            client.turn_start("thread-1", "unsafe unkeyed start")

        assert attempts == 1
        assert service.turn_start_executions == 1
    finally:
        client.close()
        server.close()
