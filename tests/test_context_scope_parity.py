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
