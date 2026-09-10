from __future__ import annotations

import loom_model_bridge
from app.ai.model_selection_store import ModelSelectionStore
from app.ai.model_store import ModelConfigStore
from app.ai.reasoning_store import ReasoningConfigStore
from loom_model_bridge import (
    # Renamed when the CQU upstream became the managed Smirel Relay; the import
    # error had kept this whole module from being collected at all.
    MANAGED_RELAY_BASE_URL,
    CQU_DEFAULT_MODEL,
    CQU_SELECTION,
    PRIMARY_SELECTION,
    _resolve,
    _set_active,
    _snapshot,
)


def _stores(tmp_path):
    model_store = ModelConfigStore(tmp_path, secret_getter=lambda _alias: None, secret_setter=lambda _alias, _value: None)
    reasoning_store = ReasoningConfigStore(tmp_path)
    selection_store = ModelSelectionStore(tmp_path)
    return model_store, reasoning_store, selection_store


def test_cqu_is_exposed_as_builtin_profile_without_changing_default(tmp_path):
    model_store, reasoning_store, selection_store = _stores(tmp_path)
    snapshot = _snapshot(model_store, reasoning_store, selection_store)

    assert snapshot["activeModelId"] == "minimax-primary"
    cqu = next(profile for profile in snapshot["profiles"] if profile["selection"] == CQU_SELECTION)
    assert cqu["kind"] == "builtin"
    assert cqu["name"] == "CQU-弘深深"
    assert cqu["baseUrl"] == MANAGED_RELAY_BASE_URL
    assert cqu["model"] == CQU_DEFAULT_MODEL


def test_cqu_builtin_selection_persists_across_snapshots(tmp_path):
    model_store, reasoning_store, selection_store = _stores(tmp_path)

    _set_active(model_store, reasoning_store, selection_store, {"selection": CQU_SELECTION})
    assert selection_store.get() == CQU_SELECTION

    snapshot = _snapshot(model_store, reasoning_store, ModelSelectionStore(tmp_path))
    assert snapshot["activeModelId"] == "cqu-builtin"

    _set_active(model_store, reasoning_store, selection_store, {"selection": PRIMARY_SELECTION})
    assert selection_store.get() == PRIMARY_SELECTION


def test_cqu_resolve_uses_fixed_relay_and_env_key(tmp_path, monkeypatch):
    model_store, reasoning_store, selection_store = _stores(tmp_path)
    # CQU_API_KEY is the *last* fallback in the relay credential chain, so a
    # developer machine with any earlier one set would resolve that instead and
    # fail here -- while also pulling a real credential into the assertion.
    # Clear the whole chain so this tests the alias, not the workstation.
    for name in ("LOOM_RELAY_API_KEY", "SMIREL_RELAY_API_KEY", "LOOM_CQU_API_KEY",
                 "LOOM_RELAY_PROVISIONING_FILE"):
        monkeypatch.delenv(name, raising=False)
    # Two credential lanes are checked before the environment and neither is
    # reachable through env vars: a provisioning file next to the repo, and the
    # OS keyring. On a machine that has either, this test used to resolve the
    # real relay credential -- and print it in the assertion diff.
    monkeypatch.setattr(loom_model_bridge, "_consume_provisioned_relay_key",
                        lambda *args, **kwargs: "")
    monkeypatch.setattr(loom_model_bridge, "_credential_get", lambda _alias: None)
    monkeypatch.setattr(loom_model_bridge, "_credential_set", lambda _alias, _value: None)
    monkeypatch.setenv("CQU_API_KEY", "test-cqu-key")

    resolved = _resolve(model_store, reasoning_store, selection_store, CQU_SELECTION)

    assert resolved["selection"] == CQU_SELECTION
    assert resolved["name"] == "CQU-弘深深"
    assert resolved["provider"] == "openai-compatible"
    assert resolved["baseUrl"] == MANAGED_RELAY_BASE_URL
    assert resolved["model"] == CQU_DEFAULT_MODEL
    assert resolved["apiKey"] == "test-cqu-key"
