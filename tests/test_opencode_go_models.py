from __future__ import annotations

from app.ai.model_selection_store import ModelSelectionStore
from app.ai.model_store import ModelConfigStore
from app.ai.reasoning_store import ReasoningConfigStore
import loom_model_bridge as bridge


def _stores(tmp_path):
    store = ModelConfigStore(
        tmp_path,
        secret_getter=lambda _alias: None,
        secret_setter=lambda _alias, _value: None,
        secret_deleter=lambda _alias: None,
    )
    return store, ReasoningConfigStore(tmp_path), ModelSelectionStore(tmp_path)


def test_opencode_go_profiles_are_grouped_without_exposing_secret(monkeypatch, tmp_path) -> None:
    store, reasoning, selection = _stores(tmp_path)
    monkeypatch.setattr(bridge, "_fetch_opencode_go_model_ids", lambda: ["gpt-5.6-luna", "kimi-k3"])
    monkeypatch.setattr(bridge, "_opencode_go_key", lambda *_args, **_kwargs: "secret-present")
    monkeypatch.setattr(bridge, "_deepseek_key", lambda *_args, **_kwargs: "")
    monkeypatch.setattr(bridge, "_managed_relay_key", lambda *_args, **_kwargs: "")

    snapshot = bridge._snapshot(store, reasoning, selection)
    opencode = [
        profile for profile in snapshot["profiles"]
        if profile.get("groupId") == "opencode-go"
    ]

    assert [profile["model"] for profile in opencode] == ["gpt-5.6-luna", "kimi-k3"]
    assert all(profile["groupName"] == "OpenCode Go" for profile in opencode)
    assert all(profile["configured"] is True for profile in opencode)
    assert {profile["protocol"] for profile in opencode} == {"responses", "chat-completions"}
    assert "secret-present" not in repr(snapshot)


def test_opencode_go_uses_model_catalog_when_provider_omits_limits(
    monkeypatch, tmp_path
) -> None:
    store, reasoning, selection = _stores(tmp_path)
    monkeypatch.setattr(bridge, "_fetch_opencode_go_model_ids", lambda: ["deepseek-v4.1-flash"])
    monkeypatch.setattr(bridge, "_opencode_go_key", lambda *_args, **_kwargs: "test-provider-key")
    monkeypatch.setattr(bridge, "_deepseek_key", lambda *_args, **_kwargs: "")
    monkeypatch.setattr(bridge, "_managed_relay_key", lambda *_args, **_kwargs: "")

    resolved = bridge._resolve(
        store,
        reasoning,
        selection,
        bridge._opencode_go_selection_for_model("deepseek-v4.1-flash"),
    )

    assert resolved["contextLimits"] == {"contextWindowTokens": 1_000_000}
    assert resolved["contextLimitsSource"] == "models.dev/opencode-go"
    assert resolved["maxOutputTokens"] == 384_000


def test_opencode_go_prefers_provider_published_context_limits(monkeypatch) -> None:
    bridge._DISCOVERED_CONTEXT_LIMITS.clear()
    response = {
        "data": [{"id": "future-model", "context_window": 131_072, "max_output_tokens": 16_384}]
    }

    class _Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            import json

            return json.dumps(response).encode()

    monkeypatch.setattr(bridge.urllib.request, "urlopen", lambda *_args, **_kwargs: _Response())

    assert bridge._fetch_opencode_go_model_ids() == ["future-model"]
    profile = bridge._safe_opencode_go("future-model", configured=True)
    assert profile["contextLimits"]["contextWindowTokens"] == 131_072
    assert "outputReserveTokens" not in profile["contextLimits"]


def test_every_current_opencode_go_model_has_catalog_limits() -> None:
    profiles = [
        bridge._safe_opencode_go(model, configured=True)
        for model in bridge.OPENCODE_GO_FALLBACK_MODEL_IDS
    ]

    assert len(profiles) == 37
    assert all(profile["contextLimitsSource"] == "models.dev/opencode-go" for profile in profiles)
    assert all(profile["contextLimits"]["contextWindowTokens"] >= 200_000 for profile in profiles)


def test_catalog_aliases_share_the_verified_model_limits() -> None:
    alias = bridge._safe_opencode_go("deepseek-flash", configured=True)
    canonical = bridge._safe_opencode_go("deepseek-v4.1-flash", configured=True)
    preview = bridge._safe_opencode_go("hy3-preview", configured=True)

    assert alias["contextLimits"] == canonical["contextLimits"] == {
        "contextWindowTokens": 1_000_000
    }
    assert preview["contextLimits"] == {"contextWindowTokens": 256_000}


def test_opencode_go_resolve_uses_one_provider_key_for_all_models(monkeypatch, tmp_path) -> None:
    store, reasoning, selection = _stores(tmp_path)
    monkeypatch.setattr(bridge, "_opencode_go_key", lambda *_args, **_kwargs: "test-provider-key")

    first = bridge._resolve(
        store,
        reasoning,
        selection,
        bridge._opencode_go_selection_for_model("gpt-5.6-luna"),
    )
    second = bridge._resolve(
        store,
        reasoning,
        selection,
        bridge._opencode_go_selection_for_model("minimax-m3"),
    )

    assert first["provider"] == "opencode-go"
    assert second["provider"] == "opencode-go"
    assert first["apiKey"] == "test-provider-key"
    assert second["apiKey"] == "test-provider-key"
    assert first["protocol"] == "responses"
    assert second["protocol"] == "messages"


def test_opencode_go_profiles_can_be_browsed_before_key_is_configured(monkeypatch, tmp_path) -> None:
    store, reasoning, selection = _stores(tmp_path)
    monkeypatch.setattr(bridge, "_fetch_opencode_go_model_ids", lambda: ["glm-5.3"])
    monkeypatch.setattr(bridge, "_opencode_go_key", lambda *_args, **_kwargs: "")
    monkeypatch.setattr(bridge, "_deepseek_key", lambda *_args, **_kwargs: "")
    monkeypatch.setattr(bridge, "_managed_relay_key", lambda *_args, **_kwargs: "")

    snapshot = bridge._snapshot(store, reasoning, selection)
    profile = next(profile for profile in snapshot["profiles"] if profile.get("groupId") == "opencode-go")

    assert profile["configured"] is False
    assert profile["model"] == "glm-5.3"

    try:
        bridge._resolve(store, reasoning, selection, profile["selection"])
    except RuntimeError as exc:
        assert "OpenCode Go API key is not configured" in str(exc)
    else:
        raise AssertionError("unconfigured OpenCode Go profile unexpectedly resolved")


def test_opencode_go_vision_follows_the_probed_capability(monkeypatch, tmp_path) -> None:
    """Image support is per model, and the answer came from the gateway itself.

    `scripts/probe_opencode_vision.py` sends each model one image and records
    which accept it.  Both directions matter here: a model that accepts images
    must not be blocked in the composer, and one that does not must not be
    offered, because the attachment reaches the provider and comes back 400.
    """

    store, reasoning, selection = _stores(tmp_path)
    probed = ["kimi-k3", "minimax-m3", "gpt-5.6-luna", "grok-4.6", "glm-5.3", "deepseek-v4-flash"]
    monkeypatch.setattr(bridge, "_fetch_opencode_go_model_ids", lambda: probed)
    monkeypatch.setattr(bridge, "_opencode_go_key", lambda *_args, **_kwargs: "test-provider-key")
    monkeypatch.setattr(bridge, "_deepseek_key", lambda *_args, **_kwargs: "")
    monkeypatch.setattr(bridge, "_managed_relay_key", lambda *_args, **_kwargs: "")

    snapshot = bridge._snapshot(store, reasoning, selection)
    vision = {
        profile["model"]: profile["vision"]
        for profile in snapshot["profiles"]
        if profile.get("groupId") == "opencode-go"
    }

    assert vision == {
        "kimi-k3": True,
        "minimax-m3": True,
        "gpt-5.6-luna": True,
        "grok-4.6": False,
        "glm-5.3": False,
        "deepseek-v4-flash": False,
    }


def test_opencode_go_vision_set_spans_every_protocol(monkeypatch, tmp_path) -> None:
    """The probe reached all three dialects, so the recorded set must show it.

    The allowlist this replaced held two chat-completions ids, which is what a
    guess looks like: no model on the `responses` or `messages` endpoint could
    ever have been vision-capable under it.
    """

    from app.ai.opencode_go_runtime import opencode_go_protocol

    protocols = {opencode_go_protocol(model) for model in bridge._OPENCODE_GO_VISION_MODELS}

    assert protocols == {"responses", "messages", "chat-completions"}


def test_custom_model_name_keeps_opencode_go_provider_identity() -> None:
    current = bridge._opencode_go_selection_for_model("glm-5.3")
    described = bridge._canonical_builtin_selection(current, "minimax-m3")

    assert bridge._opencode_go_model_from_selection(described) == "minimax-m3"
    assert bridge._minimax_model_from_selection(described) is None
