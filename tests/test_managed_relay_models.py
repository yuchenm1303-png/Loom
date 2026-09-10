from __future__ import annotations

import json
from pathlib import Path

from app.ai.model_selection_store import ModelSelectionStore
from app.ai.model_store import ModelConfigStore
from app.ai.reasoning_store import ReasoningConfigStore
import loom_model_bridge as bridge


def _store(tmp_path: Path) -> ModelConfigStore:
    return ModelConfigStore(
        tmp_path,
        secret_getter=lambda _alias: None,
        secret_setter=lambda _alias, _value: None,
    )


def _store_with_secrets(
    tmp_path: Path,
    secrets: dict[str, str],
    deleted: list[str] | None = None,
) -> ModelConfigStore:
    return ModelConfigStore(
        tmp_path,
        secret_getter=secrets.get,
        secret_setter=lambda alias, value: secrets.__setitem__(alias, value),
        secret_deleter=(lambda alias: deleted.append(alias)) if deleted is not None else None,
    )


def test_relay_provisioning_file_is_stored_and_deleted(tmp_path, monkeypatch):
    provision = tmp_path / "relay-credential.json"
    provision.write_text(json.dumps({"apiKey": "relay-secret"}), encoding="utf-8")
    saved: dict[str, str] = {}

    monkeypatch.setattr(bridge, "_credential_get", lambda alias: saved.get(alias))
    monkeypatch.setattr(bridge, "_credential_set", lambda alias, value: saved.__setitem__(alias, value))

    store = _store(tmp_path)

    assert bridge._managed_relay_key(store, {bridge._PROVISIONING_FILE_ENV: str(provision)}) == "relay-secret"
    assert saved[bridge._MANAGED_RELAY_CREDENTIAL_ALIAS] == "relay-secret"
    assert not provision.exists()


def test_saved_relay_connection_is_promoted_to_managed_credential(tmp_path, monkeypatch):
    model_secrets: dict[str, str] = {}
    managed_secrets: dict[str, str] = {}
    store = _store_with_secrets(tmp_path, model_secrets)
    store.save_model(
        display_name="CQU-弘深深",
        adapter="openai-compatible",
        base_url=bridge.MANAGED_RELAY_BASE_URL,
        model=bridge.CQU_DEFAULT_MODEL,
        api_key="relay-secret",
    )

    monkeypatch.setattr(bridge, "_credential_get", lambda alias: managed_secrets.get(alias))
    monkeypatch.setattr(bridge, "_credential_set", lambda alias, value: managed_secrets.__setitem__(alias, value))

    assert bridge._managed_relay_key(store) == "relay-secret"
    assert managed_secrets[bridge._MANAGED_RELAY_CREDENTIAL_ALIAS] == "relay-secret"


def test_managed_profiles_follow_remote_models(tmp_path, monkeypatch):
    store = _store(tmp_path)

    monkeypatch.setattr(bridge, "_managed_relay_key", lambda _store, environ=None, repo_root=None: "relay-secret")
    monkeypatch.setattr(
        bridge,
        "_fetch_managed_model_ids",
        lambda api_key, environ=None, timeout=3.5: ["cqu-default", "MiniMax-M3", "custom-agent"],
    )

    profiles = bridge._managed_profiles(store)
    by_model = {profile["model"]: profile for profile in profiles}

    assert by_model["cqu-default"]["selection"] == bridge.CQU_SELECTION
    assert by_model["cqu-default"]["name"] == "CQU-弘深深"
    assert by_model["MiniMax-M3"]["selection"] == bridge.PRIMARY_SELECTION
    assert by_model["custom-agent"]["selection"] == "managed:custom-agent"


def test_unprovisioned_managed_profiles_hide_cqu_when_only_legacy_minimax_exists(tmp_path, monkeypatch):
    store = _store(tmp_path)
    monkeypatch.setattr(bridge, "_credential_get", lambda _alias: None)
    monkeypatch.setattr(bridge, "_credential_set", lambda _alias, _value: None)

    profiles = bridge._managed_profiles(store, {"MINIMAX_API_KEY": "minimax-secret"})

    assert [profile["model"] for profile in profiles] == [bridge.MINIMAX_DEFAULT_MODEL]
    assert profiles[0]["baseUrl"] == bridge.MINIMAX_BASE_URL


def test_resolve_cqu_uses_managed_relay_credential(tmp_path, monkeypatch):
    store = _store(tmp_path)
    reasoning_store = ReasoningConfigStore(tmp_path)
    selection_store = ModelSelectionStore(tmp_path)

    monkeypatch.setattr(bridge, "_managed_relay_key", lambda _store, environ=None, repo_root=None: "relay-secret")

    resolved = bridge._resolve(store, reasoning_store, selection_store, bridge.CQU_SELECTION)

    assert resolved["name"] == "CQU-弘深深"
    assert resolved["model"] == "cqu-default"
    assert resolved["baseUrl"] == bridge.MANAGED_RELAY_BASE_URL
    assert resolved["apiKey"] == "relay-secret"
    assert resolved["provider"] == "openai-compatible"


def test_delete_saved_model_removes_metadata_and_credential(tmp_path):
    secrets: dict[str, str] = {}
    deleted: list[str] = []
    store = _store_with_secrets(tmp_path, secrets, deleted)
    entry = store.save_model(
        display_name="DeepSeek",
        adapter="openai-compatible",
        base_url="https://api.deepseek.com",
        model="deepseek-flash",
        api_key="deepseek-secret",
    )
    store.set_active(entry.model_id)

    removed = store.delete_model(entry.model_id)

    assert removed.model_id == entry.model_id
    assert store.list_models() == ()
    assert store.active_model() is None
    assert deleted == [entry.credential_alias]


def test_bridge_delete_saved_model_resets_selection(tmp_path, monkeypatch):
    secrets: dict[str, str] = {}
    deleted: list[str] = []
    store = _store_with_secrets(tmp_path, secrets, deleted)
    entry = store.save_model(
        display_name="DeepSeek",
        adapter="openai-compatible",
        base_url="https://api.deepseek.com",
        model="deepseek-flash",
        api_key="deepseek-secret",
    )
    store.set_active(entry.model_id)
    reasoning_store = ReasoningConfigStore(tmp_path)
    selection_store = ModelSelectionStore(tmp_path)
    selection_store.set(entry.selection)

    monkeypatch.setattr(bridge, "_managed_relay_key", lambda _store, environ=None, repo_root=None: "")
    monkeypatch.setattr(bridge, "_primary_minimax_key", lambda environ=None: "minimax-secret")

    snapshot = bridge._delete(store, reasoning_store, selection_store, {"selection": entry.selection})

    assert store.list_models() == ()
    assert selection_store.get() == bridge.PRIMARY_SELECTION
    assert snapshot["activeModelId"] == "minimax-primary"
    assert deleted == [entry.credential_alias]
