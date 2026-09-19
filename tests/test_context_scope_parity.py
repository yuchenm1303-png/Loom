from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.agent_runtime.context_limits import resolve_context_limits


class Registry:
    def __init__(self, limits):
        self._limits = limits

    def get(self, _profile_id):
        return SimpleNamespace(context_limits=self._limits)


def _runtime(limits):
    return SimpleNamespace(
        limits=SimpleNamespace(
            context_window_tokens=10_000,
            output_reserve_tokens=1000,
        ),
        platform=SimpleNamespace(registry=Registry(limits)),
    )


def test_default_auto_compact_scope_is_total(monkeypatch):
    monkeypatch.delenv("LOOM_CONTEXT_WINDOW_TOKENS", raising=False)
    monkeypatch.delenv("LOOM_OUTPUT_RESERVE_TOKENS", raising=False)
    limits = SimpleNamespace(
        context_window_tokens=10_000,
        effective_context_percent=100,
        output_reserve_tokens=1000,
        auto_compact_token_limit=None,
        tool_output_token_limit=None,
    )

    resolved = resolve_context_limits(
        _runtime(limits),
        SimpleNamespace(profile_id="agent.fast"),
    )

    assert resolved.auto_compact_token_limit_scope == "total"
    assert resolved.auto_compact_token_limit == 9000


def test_default_auto_compact_threshold_uses_raw_window_not_effective_window(monkeypatch):
    monkeypatch.delenv("LOOM_CONTEXT_WINDOW_TOKENS", raising=False)
    monkeypatch.delenv("LOOM_OUTPUT_RESERVE_TOKENS", raising=False)
    limits = SimpleNamespace(
        context_window_tokens=1_000_000,
        effective_context_percent=95,
        output_reserve_tokens=4096,
        auto_compact_token_limit=None,
        tool_output_token_limit=None,
    )

    resolved = resolve_context_limits(
        _runtime(limits),
        SimpleNamespace(profile_id="agent.fast"),
    )

    assert resolved.context_window_tokens == 1_000_000
    assert resolved.effective_context_window_tokens == 950_000
    assert resolved.auto_compact_token_limit == 900_000
    assert resolved.input_budget_tokens == 945_904
    assert resolved.source == "model_profile"


def test_an_undeclared_window_resolves_to_the_sanity_ceiling_and_says_so(monkeypatch):
    monkeypatch.delenv("LOOM_CONTEXT_WINDOW_TOKENS", raising=False)
    monkeypatch.delenv("LOOM_OUTPUT_RESERVE_TOKENS", raising=False)
    runtime = SimpleNamespace(
        limits=SimpleNamespace(context_window_tokens=None, output_reserve_tokens=None),
        platform=SimpleNamespace(registry=Registry(None)),
    )

    resolved = resolve_context_limits(
        runtime,
        SimpleNamespace(profile_id="agent.fast"),
    )

    assert resolved.context_window_tokens == 272_000
    assert resolved.effective_context_window_tokens == 258_400
    assert resolved.input_budget_tokens == 254_304
    assert resolved.auto_compact_token_limit == 244_800
    assert resolved.source == "runtime_fallback"
    # The numbers above are a sanity ceiling, not a claim about the model, and
    # compaction decisions must be able to tell the difference.
    assert resolved.window_known is False
    assert resolved.output_reserve_declared is False


def test_an_explicitly_constructed_window_is_honoured_not_rewritten(monkeypatch):
    # Previously an explicit 32,768 was silently rewritten to 272,000 by matching
    # it against the old default. A host that says 32k means 32k.
    monkeypatch.delenv("LOOM_CONTEXT_WINDOW_TOKENS", raising=False)
    monkeypatch.delenv("LOOM_OUTPUT_RESERVE_TOKENS", raising=False)
    runtime = SimpleNamespace(
        limits=SimpleNamespace(context_window_tokens=32_768, output_reserve_tokens=4096),
        platform=SimpleNamespace(registry=Registry(None)),
    )

    resolved = resolve_context_limits(
        runtime,
        SimpleNamespace(profile_id="agent.fast"),
    )

    assert resolved.context_window_tokens == 32_768
    assert resolved.source == "runtime_limits"
    assert resolved.window_known is True
    assert resolved.output_reserve_declared is True


def test_body_after_prefix_fails_closed_until_profile_and_window_state_contract_exists(monkeypatch):
    monkeypatch.delenv("LOOM_CONTEXT_WINDOW_TOKENS", raising=False)
    monkeypatch.delenv("LOOM_OUTPUT_RESERVE_TOKENS", raising=False)
    limits = SimpleNamespace(
        context_window_tokens=10_000,
        effective_context_percent=100,
        output_reserve_tokens=1000,
        auto_compact_token_limit=2000,
        auto_compact_token_limit_scope="body_after_prefix",
        tool_output_token_limit=None,
    )

    with pytest.raises(ValueError, match="prefill-window state contract"):
        resolve_context_limits(
            _runtime(limits),
            SimpleNamespace(profile_id="agent.fast"),
        )
