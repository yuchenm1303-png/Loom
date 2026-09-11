from __future__ import annotations

from types import SimpleNamespace

import app.runtime_model_switch as model_switch
from app.ai import AGENT_FAST_ROLE, ModelContextLimits, ProviderAdapter


def test_saved_context_limits_require_exact_endpoint_and_model(monkeypatch):
    expected = ModelContextLimits(
        context_window_tokens=131_072,
        output_reserve_tokens=8192,
        tool_output_token_limit=6000,
    )
    entries = (
        SimpleNamespace(
            adapter=ProviderAdapter.OPENAI_COMPATIBLE,
            base_url="https://example.test/v1/",
            model="agent-large",
            context_limits=expected,
        ),
        SimpleNamespace(
            adapter=ProviderAdapter.OPENAI_COMPATIBLE,
            base_url="https://other.test/v1",
            model="agent-large",
            context_limits=ModelContextLimits(context_window_tokens=64_000),
        ),
    )

    class Store:
        def list_models(self):
            return entries

    monkeypatch.setattr(model_switch, "ModelConfigStore", Store)

    resolved = model_switch._stored_context_limits(
        adapter=ProviderAdapter.OPENAI_COMPATIBLE,
        base_url="https://example.test/v1",
        model="agent-large",
    )
    assert resolved == expected
    assert model_switch._stored_context_limits(
        adapter=ProviderAdapter.OPENAI_COMPATIBLE,
        base_url="https://example.test/v1",
        model="different-model",
    ) is None


def test_conflicting_saved_context_limits_fail_conservative(monkeypatch):
    entries = (
        SimpleNamespace(
            adapter=ProviderAdapter.OPENAI_COMPATIBLE,
            base_url="https://example.test/v1",
            model="agent-large",
            context_limits=ModelContextLimits(context_window_tokens=64_000),
        ),
        SimpleNamespace(
            adapter=ProviderAdapter.OPENAI_COMPATIBLE,
            base_url="https://example.test/v1/",
            model="agent-large",
            context_limits=ModelContextLimits(context_window_tokens=128_000),
        ),
    )

    class Store:
        def list_models(self):
            return entries

    monkeypatch.setattr(model_switch, "ModelConfigStore", Store)

    assert model_switch._stored_context_limits(
        adapter=ProviderAdapter.OPENAI_COMPATIBLE,
        base_url="https://example.test/v1",
        model="agent-large",
    ) is None


def test_runtime_platform_uses_recovered_saved_context_limits(monkeypatch):
    expected = ModelContextLimits(
        context_window_tokens=131_072,
        effective_context_percent=92,
        auto_compact_token_limit=88_000,
        output_reserve_tokens=8192,
        tool_output_token_limit=6000,
    )
    captured = {}

    monkeypatch.setattr(
        model_switch,
        "_stored_context_limits",
        lambda **_kwargs: expected,
    )

    def fake_build_ai_platform(configuration, **_kwargs):
        captured["limits"] = configuration.profile_for(AGENT_FAST_ROLE.role_id).context_limits
        return SimpleNamespace()

    monkeypatch.setattr(model_switch, "build_ai_platform", fake_build_ai_platform)

    platform = model_switch.build_runtime_model_platform(
        provider="openai-compatible",
        base_url="https://example.test/v1",
        model="agent-large",
        api_key="test-key",
    )

    assert captured["limits"] == expected
    assert platform._loom_model_connection["context_limits"] == expected.as_safe_dict()
