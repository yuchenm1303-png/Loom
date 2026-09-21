"""Loom must not impose limits it invented, and must use the ones providers publish.

Two halves of the same rule. Session 37f1dd69 was truncated twice at exactly
4,096 output tokens — `AgentLimits.output_reserve_tokens`, a number nobody
chose — while the model routinely wanted 2,700-3,400 and spent a further 16,518
characters on reasoning from the same budget. Meanwhile the provider's own
`/models` listing, which Loom already fetches, was parsed for `id` and nothing
else.
"""
from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from app.agent_runtime.context_limits import resolve_context_limits
from app.agent_runtime.contracts import AgentLimits
from app.ai.model_context import model_context_limits_from_provider_listing
from app.ai.profiles import ModelContextLimits


class Registry:
    def __init__(self, limits):
        self._limits = limits

    def get(self, _profile_id):
        return SimpleNamespace(context_limits=self._limits)


def _runtime(*, runtime_window=None, runtime_reserve=None, profile_limits=None):
    return SimpleNamespace(
        limits=SimpleNamespace(
            context_window_tokens=runtime_window,
            output_reserve_tokens=runtime_reserve,
        ),
        platform=SimpleNamespace(registry=Registry(profile_limits)),
    )


def _resolve(runtime):
    return resolve_context_limits(runtime, SimpleNamespace(profile_id="agent.fast"))


@pytest.fixture(autouse=True)
def _no_env_overrides(monkeypatch):
    for name in (
        "LOOM_CONTEXT_WINDOW_TOKENS",
        "LOOM_OUTPUT_RESERVE_TOKENS",
    ):
        monkeypatch.delenv(name, raising=False)


# --- an undeclared output cap is Loom's bookkeeping, not the user's choice ---


def test_an_undeclared_output_reserve_is_not_imposed_on_the_provider():
    resolved = _resolve(_runtime())

    # Still reserved for input budgeting...
    assert resolved.output_reserve_tokens > 0
    # ...but never sent as max_tokens.
    assert resolved.output_reserve_declared is False


def test_a_profile_declared_output_reserve_is_imposed():
    resolved = _resolve(
        _runtime(
            profile_limits=ModelContextLimits(
                context_window_tokens=64_000, output_reserve_tokens=8_000
            )
        )
    )

    assert resolved.output_reserve_declared is True
    assert resolved.output_reserve_tokens == 8_000


def test_an_env_declared_output_reserve_is_imposed(monkeypatch):
    monkeypatch.setenv("LOOM_OUTPUT_RESERVE_TOKENS", "12000")

    resolved = _resolve(_runtime())

    assert resolved.output_reserve_declared is True
    assert resolved.output_reserve_tokens == 12_000


def test_a_host_constructed_output_reserve_is_imposed():
    resolved = _resolve(_runtime(runtime_window=64_000, runtime_reserve=6_000))

    assert resolved.output_reserve_declared is True
    assert resolved.output_reserve_tokens == 6_000


def test_agent_limits_default_to_declaring_nothing():
    # A default that looks like a real number is indistinguishable from one, and
    # that ambiguity is what produced a 32k budget for much larger models.
    limits = AgentLimits()

    assert limits.context_window_tokens is None
    assert limits.output_reserve_tokens is None


def test_agent_limits_still_validate_a_declared_pair():
    with pytest.raises(ValueError, match="output reserve must be smaller"):
        AgentLimits(context_window_tokens=1_000, output_reserve_tokens=1_000)


def test_agent_limits_reject_non_positive_declarations():
    with pytest.raises(ValueError, match="context_window_tokens must be positive"):
        AgentLimits(context_window_tokens=0)


# --- prefer what the provider publishes over anything Loom would guess -------


@pytest.mark.parametrize(
    "entry,window",
    [
        ({"id": "m", "context_length": 65_536}, 65_536),
        ({"id": "m", "context_window": 128_000}, 128_000),
        ({"id": "m", "max_model_len": 32_768}, 32_768),
        ({"id": "m", "max_input_tokens": 200_000}, 200_000),
        ({"id": "m", "limits": {"context_length": 1_000_000}}, 1_000_000),
        ({"id": "m", "meta": {"max_context_window": 8_192}}, 8_192),
    ],
)
def test_a_published_window_is_read_from_the_listing(entry, window):
    assert model_context_limits_from_provider_listing(entry).context_window_tokens == window


@pytest.mark.parametrize(
    "entry",
    [
        {"id": "m", "max_output_tokens": 8_192},
        {"id": "m", "max_completion_tokens": 64_000},
    ],
)
def test_a_published_output_ceiling_is_not_mistaken_for_request_reserve(entry):
    assert model_context_limits_from_provider_listing(entry).output_reserve_tokens is None


def test_bare_max_tokens_is_ignored_because_gateways_disagree_about_it():
    # Some listings use it for the context length, others for the completion cap.
    # Guessing wrong here reintroduces exactly the invented limit this avoids.
    limits = model_context_limits_from_provider_listing({"id": "m", "max_tokens": 4_096})

    assert limits.context_window_tokens is None
    assert limits.output_reserve_tokens is None


def test_a_listing_that_publishes_nothing_leaves_the_window_undeclared():
    limits = model_context_limits_from_provider_listing({"id": "m", "object": "model"})

    assert limits.context_window_tokens is None
    assert limits.output_reserve_tokens is None


@pytest.mark.parametrize("entry", [None, "not-a-mapping", 42, ["m"]])
def test_a_malformed_listing_entry_is_survivable(entry):
    assert model_context_limits_from_provider_listing(entry) == ModelContextLimits()


def test_nonsense_values_are_refused_rather_than_believed():
    limits = model_context_limits_from_provider_listing(
        {"id": "m", "context_length": 0, "max_output_tokens": -5}
    )

    assert limits.context_window_tokens is None
    assert limits.output_reserve_tokens is None


def test_a_discovered_window_makes_the_window_known():
    discovered = model_context_limits_from_provider_listing(
        {"id": "m", "context_length": 65_536, "max_output_tokens": 8_192}
    )

    resolved = _resolve(_runtime(profile_limits=discovered))

    assert resolved.window_known is True
    assert resolved.context_window_tokens == 65_536
    assert resolved.output_reserve_declared is False
    assert resolved.source == "model_profile"


# --- end to end: nothing invented reaches the provider ----------------------


def _make_runtime(path, platform, **kwargs):
    from app.agent_runtime import (
        AgentRuntime,
        FileAgentSessionStore,
        SandboxManager,
        SandboxPolicy,
        ToolRegistry,
    )

    return AgentRuntime(
        platform=platform,
        store=FileAgentSessionStore(path),
        tools=ToolRegistry(()),
        sandbox_manager=SandboxManager(policy=SandboxPolicy.OFF),
        **kwargs,
    )


class Recorder:
    def __init__(self):
        self.requests = []

    def execute_chat(self, profile, request):
        from app.ai import ModelResponse

        self.requests.append(request)
        return ModelResponse(text="done", finish_reason="stop")


def test_a_default_runtime_sends_no_output_cap_at_all(tmp_path):
    platform = Recorder()
    runtime = _make_runtime(tmp_path, platform)
    session = runtime.create_session("agent.fast")

    runtime.start_turn(session.session_id, "hello")

    assert platform.requests[-1].max_output_tokens is None
    runtime.close()


def test_a_declared_runtime_reserve_is_still_sent(tmp_path):
    platform = Recorder()
    runtime = _make_runtime(
        tmp_path,
        platform,
        limits=AgentLimits(context_window_tokens=64_000, output_reserve_tokens=9_000),
    )
    session = runtime.create_session("agent.fast")

    runtime.start_turn(session.session_id, "hello")

    assert platform.requests[-1].max_output_tokens == 9_000
    runtime.close()
