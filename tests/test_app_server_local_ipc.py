from __future__ import annotations

import json

import pytest

from app.app_server import LoomRpcController
from app.app_server_local_ipc import (
    LocalAppServerIpcServer,
    LocalAppServerSecurityError,
    LocalAppServerUnavailable,
    LoomLocalAppServerClient,
    local_app_server_descriptor_path,
)


class FakeService:
    def runtime_status(self):
        return {"model": "shared-runtime", "activeThreadIds": []}


class FakeController(LoomRpcController):
    """Exercise the transport without pretending FakeService is a full App Server."""

    def _dispatch(self, method, params):
        if method == "runtime/status":
            return self.service.runtime_status()
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
