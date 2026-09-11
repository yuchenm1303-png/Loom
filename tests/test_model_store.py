from __future__ import annotations

import json

import pytest

from app.ai.credentials import CredentialSource
from app.ai.model_store import ModelConfigStore, model_id_from_selection
from app.ai.profiles import ModelContextLimits
from app.ai.provider_catalog import ProviderAdapter


def _store(tmp_path):
    secrets: dict[str, str] = {}
    store = ModelConfigStore(
        tmp_path,
        secret_getter=secrets.get,
        secret_setter=lambda alias, value: secrets.__setitem__(alias, value),
    )
    return store, secrets


def test_openai_compatible_model_metadata_is_saved_without_the_secret(tmp_path):
    store, secrets = _store(tmp_path)

    entry = store.save_model(
        display_name="Gateway A · Agent",
        adapter="openai-compatible",
        base_url="https://example.test/v1/",
        model="agent-model-x",
        api_key="sk-super-secret",
    )

    assert entry.adapter is ProviderAdapter.OPENAI_COMPATIBLE
    assert entry.base_url == "https://example.test/v1"
    assert entry.selection.startswith("profile:m")
    assert model_id_from_selection(entry.selection) == entry.model_id
    assert secrets[entry.credential_alias] == "sk-super-secret"
    assert store.secret_for(entry) == "sk-super-secret"

    persisted = (tmp_path / "models.json").read_text(encoding="utf-8")
    assert "sk-super-secret" not in persisted
    payload = json.loads(persisted)
    assert payload["models"][0]["name"] == "Gateway A · Agent"
    assert payload["models"][0]["model"] == "agent-model-x"
    assert payload["models"][0]["context_limits"]["context_window_tokens"] is None

    connection = entry.provider_connection()
    assert connection.adapter is ProviderAdapter.OPENAI_COMPATIBLE
    assert connection.base_url == "https://example.test/v1"
    assert connection.credential_ref.source is CredentialSource.OS_KEYCHAIN


def test_context_limits_round_trip_with_saved_model(tmp_path):
    store, _secrets = _store(tmp_path)
    limits = ModelContextLimits(
        context_window_tokens=131_072,
        effective_context_percent=92,
        auto_compact_token_limit=88_000,
        output_reserve_tokens=8192,
        tool_output_token_limit=6000,
    )

    entry = store.save_model(
        display_name="Large Context",
        adapter="openai-compatible",
        base_url="https://example.test/v1",
        model="agent-large",
        api_key="secret",
        context_limits=limits,
    )

    restored = store.get(entry.model_id)
    assert restored.context_limits == limits
    payload = json.loads((tmp_path / "models.json").read_text(encoding="utf-8"))
    assert payload["models"][0]["context_limits"] == limits.as_safe_dict()

    updated = store.update_model(
        entry.model_id,
        display_name=entry.display_name,
        adapter=entry.adapter,
        base_url=entry.base_url,
        model=entry.model,
        context_limits=ModelContextLimits(context_window_tokens=64_000),
    )
    assert updated.context_limits.context_window_tokens == 64_000
    assert store.get(entry.model_id).context_limits.context_window_tokens == 64_000


def test_old_v1_model_record_without_context_limits_remains_readable(tmp_path):
    store, secrets = _store(tmp_path)
    model_id = "m123456789abc"
    alias = f"model/{model_id}"
    secrets[alias] = "secret"
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "models.json").write_text(
        json.dumps(
            {
                "version": 1,
                "active_model_id": model_id,
                "models": [
                    {
                        "id": model_id,
                        "name": "Legacy",
                        "adapter": "openai-compatible",
                        "base_url": "https://legacy.example/v1",
                        "model": "legacy-model",
                        "credential_alias": alias,
                        "vision": True,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    entry = store.get(model_id)

    assert entry.display_name == "Legacy"
    assert entry.context_limits == ModelContextLimits()
    assert store.active_model() == entry


def test_active_model_round_trips_and_can_be_cleared(tmp_path):
    store, _secrets = _store(tmp_path)
    entry = store.save_model(
        display_name="Primary",
        adapter="openai-compatible",
        base_url="https://example.test/v1",
        model="model-one",
        api_key="secret",
    )

    assert store.active_model() is None
    store.set_active(entry.model_id)
    assert store.active_model_id == entry.model_id
    assert store.active_model() == entry
    assert store.model_for_selection(entry.selection) == entry

    store.set_active(None)
    assert store.active_model_id is None
    assert store.active_model() is None


def test_display_names_are_unique_even_when_model_ids_match(tmp_path):
    store, _secrets = _store(tmp_path)
    store.save_model(
        display_name="Gateway A",
        adapter="openai-compatible",
        base_url="https://a.example.test/v1",
        model="same-model",
        api_key="a",
    )
    second = store.save_model(
        display_name="Gateway B",
        adapter="openai-compatible",
        base_url="https://b.example.test/v1",
        model="same-model",
        api_key="b",
    )

    assert len(store.list_models()) == 2
    assert second.model == "same-model"
    with pytest.raises(ValueError, match="already exists"):
        store.save_model(
            display_name="gateway a",
            adapter="openai-compatible",
            base_url="https://c.example.test/v1",
            model="another-model",
            api_key="c",
        )


def test_official_openai_uses_the_default_endpoint(tmp_path):
    store, _secrets = _store(tmp_path)
    entry = store.save_model(
        display_name="OpenAI direct",
        adapter="openai",
        base_url="",
        model="gpt-test",
        api_key="secret",
    )

    assert entry.adapter is ProviderAdapter.OPENAI
    assert entry.base_url == ""
    with pytest.raises(ValueError, match="does not accept base_url"):
        store.save_model(
            display_name="Bad OpenAI",
            adapter="openai",
            base_url="https://example.test/v1",
            model="gpt-test",
            api_key="secret",
        )


def test_openai_compatible_requires_a_complete_http_endpoint(tmp_path):
    store, _secrets = _store(tmp_path)
    with pytest.raises(ValueError, match="requires base_url"):
        store.save_model(
            display_name="Missing endpoint",
            adapter="openai-compatible",
            base_url="",
            model="model",
            api_key="secret",
        )
    with pytest.raises(ValueError, match="complete http/https URL"):
        store.save_model(
            display_name="Bad endpoint",
            adapter="openai-compatible",
            base_url="example.test/v1",
            model="model",
            api_key="secret",
        )
