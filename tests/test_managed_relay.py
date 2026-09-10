from __future__ import annotations

import json

import pytest

from app.ai.managed_relay import (
    MANAGED_RELAY_BASE_URL,
    MANAGED_RELAY_CREDENTIAL_ALIAS,
    MANAGED_RELAY_KEYRING_SERVICE,
    ManagedRelay,
    ManagedRelayError,
)


def _catalog(*models: str) -> bytes:
    return json.dumps({"object": "list", "data": [{"id": model} for model in models]}).encode()


def test_managed_relay_reads_one_os_credential_and_lists_server_models():
    calls: list[tuple[str, str]] = []

    def get_secret(service: str, alias: str) -> str | None:
        calls.append((service, alias))
        return "device-relay-secret"

    def fetch(req, timeout: float) -> bytes:
        assert req.full_url == MANAGED_RELAY_BASE_URL + "/models"
        assert req.get_header("Authorization") == "Bearer device-relay-secret"
        assert timeout == 5.0
        return _catalog("MiniMax-M3", "cqu-default", "cqu-default")

    relay = ManagedRelay(environ={}, secret_getter=get_secret, fetcher=fetch)

    assert relay.available_models() == ("MiniMax-M3", "cqu-default")
    assert calls == [(MANAGED_RELAY_KEYRING_SERVICE, MANAGED_RELAY_CREDENTIAL_ALIAS)]


def test_managed_relay_provision_writes_only_the_managed_keyring_slot():
    saved: list[tuple[str, str, str]] = []
    relay = ManagedRelay(
        environ={},
        secret_getter=lambda _service, _alias: None,
        secret_setter=lambda service, alias, value: saved.append((service, alias, value)),
    )

    relay.provision("customer-device-secret")

    assert saved == [
        (MANAGED_RELAY_KEYRING_SERVICE, MANAGED_RELAY_CREDENTIAL_ALIAS, "customer-device-secret")
    ]


def test_managed_relay_missing_credential_has_product_level_error():
    relay = ManagedRelay(environ={}, secret_getter=lambda _service, _alias: None)

    with pytest.raises(ManagedRelayError, match="managed access is not provisioned"):
        relay.credential()
