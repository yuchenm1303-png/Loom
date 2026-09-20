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


def test_managed_profiles_keep_minimax_official_and_relay_separate(tmp_path, monkeypatch):
    store = _store(tmp_path)

    monkeypatch.setattr(bridge, "_managed_relay_key", lambda _store, environ=None, repo_root=None: "relay-secret")
    monkeypatch.setattr(
        bridge,
        "_fetch_managed_model_ids",
        lambda api_key, environ=None, timeout=3.5: ["cqu-default", "MiniMax-M3", "custom-agent"],
    )

    profiles = bridge._managed_profiles(store)
    by_model = {profile["model"]: profile for profile in profiles}

    assert by_model["MiniMax-M3"]["selection"] == bridge.PRIMARY_SELECTION
    assert by_model["MiniMax-M3"]["baseUrl"] == bridge.MINIMAX_BASE_URL
    assert by_model["MiniMax-M2.7"]["baseUrl"] == bridge.MINIMAX_BASE_URL
    assert by_model["MiniMax-M2.5"]["baseUrl"] == bridge.MINIMAX_BASE_URL
    assert by_model["cqu-default"]["selection"] == bridge.CQU_SELECTION
    assert by_model["cqu-default"]["name"] == "CQU-弘深深"
    assert by_model["cqu-default"]["baseUrl"] == bridge.MANAGED_RELAY_BASE_URL
    assert by_model["custom-agent"]["selection"] == "managed:custom-agent"
    assert by_model["custom-agent"]["baseUrl"] == bridge.MANAGED_RELAY_BASE_URL


def test_unprovisioned_profiles_show_builtin_minimax_and_deepseek_models(tmp_path, monkeypatch):
    store = _store(tmp_path)
    monkeypatch.setattr(bridge, "_credential_get", lambda _alias: None)
    monkeypatch.setattr(bridge, "_credential_set", lambda _alias, _value: None)

    profiles = bridge._managed_profiles(store, {"MINIMAX_API_KEY": "minimax-secret"})
    by_model = {profile["model"]: profile for profile in profiles}

    assert all(model in by_model for model in bridge.MINIMAX_MODEL_IDS)
    assert all(model in by_model for model in bridge.DEEPSEEK_FALLBACK_MODEL_IDS)
    assert all(by_model[model]["baseUrl"] == bridge.MINIMAX_BASE_URL for model in bridge.MINIMAX_MODEL_IDS)
    assert all(by_model[model]["baseUrl"] == bridge.DEEPSEEK_BASE_URL for model in bridge.DEEPSEEK_FALLBACK_MODEL_IDS)
    assert by_model[bridge.DEEPSEEK_DEFAULT_MODEL]["selection"] == bridge.DEEPSEEK_SELECTION


def test_builtin_deepseek_profiles_follow_official_model_discovery(tmp_path, monkeypatch):
    store = _store(tmp_path)
    monkeypatch.setattr(bridge, "_deepseek_key", lambda _store, environ=None: "deepseek-secret")
    monkeypatch.setattr(
        bridge,
        "_fetch_deepseek_model_ids",
        lambda api_key, environ=None, timeout=3.5: [
            "deepseek-flash",
            "deepseek-v4-pro",
            "deepseek-future",
        ],
    )
    monkeypatch.setattr(bridge, "_managed_relay_key", lambda _store, environ=None, repo_root=None: "")

    profiles = bridge._managed_profiles(store)
    by_model = {profile["model"]: profile for profile in profiles}

    assert by_model["deepseek-flash"]["selection"] == bridge.DEEPSEEK_SELECTION
    assert by_model["deepseek-v4-pro"]["selection"] == "builtin:deepseek:deepseek-v4-pro"
    assert by_model["deepseek-future"]["selection"] == "builtin:deepseek:deepseek-future"
    assert all(
        by_model[model]["baseUrl"] == bridge.DEEPSEEK_BASE_URL
        for model in ("deepseek-flash", "deepseek-v4-pro", "deepseek-future")
    )
    assert all(
        by_model[model]["kind"] == "builtin"
        for model in ("deepseek-flash", "deepseek-v4-pro", "deepseek-future")
    )


def test_persist_active_does_not_rebuild_remote_catalog(tmp_path, monkeypatch):
    store = _store(tmp_path)
    selection_store = ModelSelectionStore(tmp_path)

    def fail_snapshot(*_args, **_kwargs):
        raise AssertionError("persist-active must not rebuild the model snapshot")

    monkeypatch.setattr(bridge, "_snapshot", fail_snapshot)

    result = bridge._persist_active(
        store,
        selection_store,
        {"selection": bridge.DEEPSEEK_SELECTION},
    )

    assert result == {"selection": bridge.DEEPSEEK_SELECTION}
    assert selection_store.get() == bridge.DEEPSEEK_SELECTION


def test_resolve_minimax_uses_official_key_even_when_relay_exists(tmp_path, monkeypatch):
    store = _store(tmp_path)
    reasoning_store = ReasoningConfigStore(tmp_path)
    selection_store = ModelSelectionStore(tmp_path)

    monkeypatch.setattr(bridge, "_managed_relay_key", lambda _store, environ=None, repo_root=None: "relay-secret")
    monkeypatch.setattr(bridge, "_primary_minimax_key", lambda environ=None: "minimax-secret")

    resolved = bridge._resolve(store, reasoning_store, selection_store, bridge.PRIMARY_SELECTION)

    assert resolved["name"] == "MiniMax"
    assert resolved["model"] == bridge.MINIMAX_DEFAULT_MODEL
    assert resolved["baseUrl"] == bridge.MINIMAX_BASE_URL
    assert resolved["apiKey"] == "minimax-secret"
    assert resolved["provider"] == "openai-compatible"


def test_resolve_deepseek_uses_official_key(tmp_path, monkeypatch):
    store = _store(tmp_path)
    reasoning_store = ReasoningConfigStore(tmp_path)
    selection_store = ModelSelectionStore(tmp_path)

    monkeypatch.setattr(bridge, "_deepseek_key", lambda _store, environ=None: "deepseek-secret")

    resolved = bridge._resolve(store, reasoning_store, selection_store, bridge.DEEPSEEK_SELECTION)

    assert resolved["name"] == "DeepSeek Flash"
    assert resolved["model"] == bridge.DEEPSEEK_DEFAULT_MODEL
    assert resolved["baseUrl"] == bridge.DEEPSEEK_BASE_URL
    assert resolved["apiKey"] == "deepseek-secret"
    assert resolved["provider"] == "openai-compatible"
    assert resolved["reasoning"]["value"] == "high"


def test_builtin_model_name_switch_carries_deepseek_provider_identity(tmp_path, monkeypatch):
    store = _store(tmp_path)
    reasoning_store = ReasoningConfigStore(tmp_path)

    described = bridge._describe_model(
        store,
        reasoning_store,
        bridge.PRIMARY_SELECTION,
        bridge.DEEPSEEK_DEFAULT_MODEL,
    )

    assert described["selection"] == bridge.DEEPSEEK_SELECTION
    assert described["model"] == bridge.DEEPSEEK_DEFAULT_MODEL
    assert described["baseUrl"] == bridge.DEEPSEEK_BASE_URL


def test_resolve_model_spec_cannot_mix_minimax_selection_with_deepseek_model(tmp_path, monkeypatch):
    store = _store(tmp_path)
    monkeypatch.setattr(bridge, "ModelConfigStore", lambda home=None: store)
    monkeypatch.setattr(bridge, "_deepseek_key", lambda _store, environ=None: "deepseek-secret")

    resolved = bridge.resolve_model_spec(
        bridge.PRIMARY_SELECTION,
        model=bridge.DEEPSEEK_DEFAULT_MODEL,
        home=tmp_path,
    )

    assert resolved["selection"] == bridge.DEEPSEEK_SELECTION
    assert resolved["model"] == bridge.DEEPSEEK_DEFAULT_MODEL
    assert resolved["baseUrl"] == bridge.DEEPSEEK_BASE_URL
    assert resolved["apiKey"] == "deepseek-secret"


def test_saved_connection_keeps_its_provider_for_custom_model_ids(tmp_path):
    secrets: dict[str, str] = {}
    store = _store_with_secrets(tmp_path, secrets)
    saved = store.save_model(
        display_name="Private compatible API",
        adapter="openai-compatible",
        base_url="https://example.invalid/v1",
        model="custom-model",
        api_key="private-secret",
    )
    reasoning_store = ReasoningConfigStore(tmp_path)

    described = bridge._describe_model(
        store,
        reasoning_store,
        saved.selection,
        bridge.DEEPSEEK_DEFAULT_MODEL,
    )

    assert described["selection"] == saved.selection
    assert described["baseUrl"] == "https://example.invalid/v1"
    assert described["model"] == bridge.DEEPSEEK_DEFAULT_MODEL


def test_saved_deepseek_connection_is_promoted_to_builtin_credential(tmp_path, monkeypatch):
    model_secrets: dict[str, str] = {}
    builtin_secrets: dict[str, str] = {}
    store = _store_with_secrets(tmp_path, model_secrets)
    store.save_model(
        display_name="DeepSeek",
        adapter="openai-compatible",
        base_url=bridge.DEEPSEEK_BASE_URL,
        model="deepseek-flash",
        api_key="deepseek-secret",
    )
    monkeypatch.setattr(bridge, "_credential_get", lambda alias: builtin_secrets.get(alias))
    monkeypatch.setattr(bridge, "_credential_set", lambda alias, value: builtin_secrets.__setitem__(alias, value))

    assert bridge._deepseek_key(store) == "deepseek-secret"
    assert builtin_secrets[bridge._DEEPSEEK_CREDENTIAL_ALIAS] == "deepseek-secret"


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
