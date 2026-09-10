from __future__ import annotations

from app.ai.model_selection_store import ModelSelectionStore
from app.ai.model_store import ModelConfigStore
from app.ai.reasoning_store import ReasoningConfigStore
from loom_model_bridge import (
    CQU_BASE_URL,
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
    assert cqu["baseUrl"] == CQU_BASE_URL
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
    monkeypatch.setenv("CQU_API_KEY", "test-cqu-key")

    resolved = _resolve(model_store, reasoning_store, selection_store, CQU_SELECTION)

    assert resolved["selection"] == CQU_SELECTION
    assert resolved["name"] == "CQU-弘深深"
    assert resolved["provider"] == "openai-compatible"
    assert resolved["baseUrl"] == CQU_BASE_URL
    assert resolved["model"] == CQU_DEFAULT_MODEL
    assert resolved["apiKey"] == "test-cqu-key"
