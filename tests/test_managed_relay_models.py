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
