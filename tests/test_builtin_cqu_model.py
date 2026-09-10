from __future__ import annotations

import json

import pytest

from app.ai.managed_relay import MANAGED_RELAY_BASE_URL, ManagedRelay, ManagedRelayError
from app.ai.model_selection_store import ModelSelectionStore
from app.ai.model_store import ModelConfigStore
from app.ai.reasoning_store import ReasoningConfigStore
from loom_model_bridge import (
    CQU_BASE_URL,
    CQU_DEFAULT_MODEL,
    CQU_SELECTION,
    MINIMAX_BASE_URL,
    MINIMAX_DEFAULT_MODEL,
    PRIMARY_SELECTION,
    _provision_managed_relay,
    _resolve,
    _set_active,
    _snapshot,
)


def _stores(tmp_path):
    model_store = ModelConfigStore(tmp_path, secret_getter=lambda _alias: None, secret_setter=lambda _alias, _value: None)
    reasoning_store = ReasoningConfigStore(tmp_path)
    selection_store = ModelSelectionStore(tmp_path)
    return model_store, reasoning_store, selection_store


def _relay(*models: str, credential: str = "managed-device-key") -> ManagedRelay:
    return ManagedRelay(
        environ={},
        secret_getter=lambda _service, _alias: credential,
        secret_setter=lambda _service, _alias, _value: None,
        fetcher=lambda _req, _timeout: json.dumps(
            {"object": "list", "data": [{"id": model} for model in models]}
        ).encode(),
    )


def test_cqu_is_exposed_as_managed_builtin_without_changing_default(tmp_path):
    model_store, reasoning_store, selection_store = _stores(tmp_path)
    snapshot = _snapshot(
        model_store,
        reasoning_store,
        selection_store,
        relay=_relay(MINIMAX_DEFAULT_MODEL, CQU_DEFAULT_MODEL),
    )

    assert snapshot["activeModelId"] == "minimax-primary"
    cqu = next(profile for profile in snapshot["profiles"] if profile["selection"] == CQU_SELECTION)
    assert cqu["kind"] == "builtin"
    assert cqu["managed"] is True
    assert cqu["available"] is True
    assert cqu["name"] == "CQU-弘深深"
    assert cqu["baseUrl"] == CQU_BASE_URL == MANAGED_RELAY_BASE_URL
    assert cqu["model"] == CQU_DEFAULT_MODEL


def test_minimax_and_cqu_resolve_through_same_managed_relay_credential(tmp_path):
    model_store, reasoning_store, selection_store = _stores(tmp_path)
    relay = _relay(MINIMAX_DEFAULT_MODEL, CQU_DEFAULT_MODEL, credential="one-customer-key")

    minimax = _resolve(model_store, reasoning_store, selection_store, PRIMARY_SELECTION, relay=relay)
    cqu = _resolve(model_store, reasoning_store, selection_store, CQU_SELECTION, relay=relay)

    assert minimax["baseUrl"] == MINIMAX_BASE_URL == MANAGED_RELAY_BASE_URL
    assert cqu["baseUrl"] == CQU_BASE_URL == MANAGED_RELAY_BASE_URL
    assert minimax["apiKey"] == cqu["apiKey"] == "one-customer-key"
    assert minimax["model"] == MINIMAX_DEFAULT_MODEL
    assert cqu["model"] == CQU_DEFAULT_MODEL


def test_server_catalog_can_disable_cqu_without_removing_the_builtin_card(tmp_path):
    model_store, reasoning_store, selection_store = _stores(tmp_path)
    relay = _relay(MINIMAX_DEFAULT_MODEL)
    snapshot = _snapshot(model_store, reasoning_store, selection_store, relay=relay)

    cqu = next(profile for profile in snapshot["profiles"] if profile["selection"] == CQU_SELECTION)
    assert cqu["available"] is False
    assert "not enabled" in cqu["availabilityReason"]

    with pytest.raises(ManagedRelayError, match="not enabled"):
        _resolve(model_store, reasoning_store, selection_store, CQU_SELECTION, relay=relay)


def test_cqu_builtin_selection_persists_only_when_server_allows_it(tmp_path):
    model_store, reasoning_store, selection_store = _stores(tmp_path)
    relay = _relay(MINIMAX_DEFAULT_MODEL, CQU_DEFAULT_MODEL)

    _set_active(
        model_store,
        reasoning_store,
        selection_store,
        {"selection": CQU_SELECTION},
        relay=relay,
    )
    assert selection_store.get() == CQU_SELECTION

    snapshot = _snapshot(
        model_store,
        reasoning_store,
        ModelSelectionStore(tmp_path),
        relay=relay,
    )
    assert snapshot["activeModelId"] == "cqu-builtin"

    _set_active(
        model_store,
        reasoning_store,
        selection_store,
        {"selection": PRIMARY_SELECTION},
        relay=relay,
    )
    assert selection_store.get() == PRIMARY_SELECTION


def test_managed_relay_provision_command_never_echoes_the_credential():
    saved: list[str] = []
    relay = ManagedRelay(
        environ={},
        secret_getter=lambda _service, _alias: None,
        secret_setter=lambda _service, _alias, value: saved.append(value),
    )

    result = _provision_managed_relay({"credential": "one-time-provision-secret"}, relay=relay)

    assert result == {"provisioned": True}
    assert saved == ["one-time-provision-secret"]
    assert "one-time-provision-secret" not in repr(result)
