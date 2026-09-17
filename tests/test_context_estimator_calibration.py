"""Compaction must fire on what the provider actually counts, and commit a summary.

The regression these cover was a three-minute turn that auto-compacted four times
while the provider never reported more than 21k of context, because the fallback
estimator ran ~23% hot against a context window nobody had configured.
"""
from __future__ import annotations

from dataclasses import dataclass

import pytest

from app.ai import AIMessage, MessageRole, ModelContextLimits, ModelResponse, ModelUsage
from app.agent_runtime.context_budget import estimate_tokens, prepare_context
from app.agent_runtime.context_compaction import build_compacted_history


class ScriptedExecutor:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def execute(self, _platform, profile_id, request, _token):
        self.requests.append((profile_id, request))
        if not self.responses:
            raise AssertionError("scripted compaction executor ran out of responses")
        return self.responses.pop(0)


class EmptyRouter:
    def definitions(self):
        return ()


class Step:
    tool_router = EmptyRouter()


class InstructionLoader:
    def load(self, _workspace):
        return ""


@dataclass
class Limits:
    context_window_tokens: int = 12_000
    output_reserve_tokens: int = 1_000
    max_messages: int = 160
    max_tool_result_chars: int = 20_000
    model_retries: int = 2


class Envelope:
    digest = "digest-calibration"


class Token:
    cancelled = False


@dataclass
class Event:
    kind: str
    data: dict


class Store:
    def __init__(self, events=()):
        self._events = tuple(events)

    def events(self, _session_id):
        return self._events


def _profile(auto_compact, *, window=12_000):
    class Profile:
        context_limits = ModelContextLimits(
            context_window_tokens=window,
            effective_context_percent=100,
            output_reserve_tokens=1_000,
            auto_compact_token_limit=auto_compact,
        )

    return Profile


class _FallbackProfile:
    """A model nobody declared a context window for."""

    context_limits = ModelContextLimits()


def _platform(profile_cls):
    class Registry:
        def get(self, _profile_id):
            return profile_cls()

    class Platform:
        registry = Registry()

    return Platform()


class Session:
    def __init__(self, messages):
        self.session_id = "session-calibration"
        self.messages = list(messages)
        self.workspace_dir = "/tmp/project"
        self.profile_id = "agent.fast"
        self.communication_language = "auto"
        self.usage = ModelUsage()


class FakeRuntime:
    def __init__(self, responses, *, events=(), profile_cls=None, limits=None):
        self.limits = limits or Limits()
        self.model_executor = ScriptedExecutor(responses)
        self.platform = _platform(profile_cls or _profile(auto_compact=10_800))
        self.instruction_loader = InstructionLoader()
        self.store = Store(events)
        self.commits = []

    def _context_envelope(self, _session, _step):
        return Envelope()

    def _request_context_messages(self, _session, _step, _envelope):
        return (AIMessage(role=MessageRole.SYSTEM, content="base/runtime context"),)

    def _commit_compaction_locked(
        self,
        session,
        *,
        summary,
        repaired,
        archived,
        retained,
        summary_source,
        summary_usage=None,
        replacement_override=None,
    ):
        self.commits.append({"summary": summary, "summary_source": summary_source})
        session.messages = list(
            replacement_override
            if replacement_override is not None
            else build_compacted_history(
                tuple((*archived, *retained)),
                summary,
                token_counter=lambda messages: estimate_tokens(messages),
            )
        )


def _calibration_events(pairs, *, estimated, reported, provider_total=8_000):
    """Committed model steps whose estimate/usage pairs expose estimator bias."""
    events = []
    for _ in range(pairs):
        events.append(
            Event("model_requested", {"estimated_input_tokens_after": estimated})
        )
        events.append(
            Event(
                "model_response",
                {
                    "usage": {
                        "input_tokens": reported,
                        "output_tokens": 100,
                        "total_tokens": provider_total,
                    }
                },
            )
        )
    return events


def _history_near(target_tokens, transient_tokens):
    """Build history whose raw estimate lands just above ``target_tokens``."""
    messages: list[AIMessage] = []
    while True:
        messages.append(AIMessage(role=MessageRole.USER, content="u" * 3_000))
        messages.append(AIMessage(role=MessageRole.ASSISTANT, content="a" * 3_000))
        if estimate_tokens(messages) + transient_tokens > target_tokens:
            return messages


def test_calibrated_estimate_prevents_compaction_the_provider_does_not_need():
    # Raw estimate overshoots the 11k input budget; the provider's own accounting,
    # measured at 0.75x, leaves real headroom. Summarizing here would be pure loss.
    history = _history_near(11_000, transient_tokens=20)
    runtime = FakeRuntime(
        [],  # any compaction attempt is a failure
        events=_calibration_events(4, estimated=10_000, reported=7_500),
    )
    session = Session(history)

    raw = estimate_tokens(
        [AIMessage(role=MessageRole.SYSTEM, content="base/runtime context"), *history]
    )
    assert raw > 11_000

    messages, metadata = prepare_context(runtime, session, Step(), Token())

    assert runtime.model_executor.requests == []
    assert runtime.commits == []
    assert metadata.get("auto_compacted") is None
    assert metadata["estimator_calibration"] == pytest.approx(0.75)
    assert metadata["estimator_calibration_samples"] == 4
    assert len(messages) == len(history) + 2  # transient system + language


def test_uncalibrated_session_still_compacts_on_the_raw_estimate():
    # Same history, no observed provider pairs yet: the pessimistic estimator is
    # the only signal available and must still protect the request.
    history = _history_near(11_000, transient_tokens=20)
    runtime = FakeRuntime([ModelResponse(text="summary", finish_reason="stop")])
    session = Session(history)

    _messages, metadata = prepare_context(runtime, session, Step(), Token())

    assert metadata["auto_compacted"] is True
    assert metadata["estimator_calibration"] == pytest.approx(1.0)
    assert metadata["estimator_calibration_samples"] == 0


def test_too_few_samples_do_not_move_the_budget():
    history = _history_near(11_000, transient_tokens=20)
    runtime = FakeRuntime(
        [ModelResponse(text="summary", finish_reason="stop")],
        events=_calibration_events(2, estimated=10_000, reported=5_000),
    )
    session = Session(history)

    _messages, metadata = prepare_context(runtime, session, Step(), Token())

    assert metadata["auto_compacted"] is True
    assert metadata["estimator_calibration"] == pytest.approx(1.0)
    assert metadata["estimator_calibration_samples"] == 2


def test_calibration_is_clamped_against_absurd_provider_accounting():
    # A provider reporting ~0 input tokens must not be able to disable compaction.
    # Sized so the request overflows even at the 0.7 floor: without the clamp the
    # factor would be ~0.0001 and this history would sail straight past the gate.
    history = _history_near(16_000, transient_tokens=20)
    runtime = FakeRuntime(
        [ModelResponse(text="summary", finish_reason="stop")],
        events=_calibration_events(4, estimated=10_000, reported=1),
    )
    session = Session(history)

    _messages, metadata = prepare_context(runtime, session, Step(), Token())

    assert metadata["estimator_calibration"] == pytest.approx(0.7)
    assert metadata["auto_compacted"] is True


def test_metadata_reports_raw_estimates_so_calibration_cannot_compound():
    history = _history_near(11_000, transient_tokens=20)
    runtime = FakeRuntime(
        [],
        events=_calibration_events(4, estimated=10_000, reported=7_500),
    )
    session = Session(history)

    _messages, metadata = prepare_context(runtime, session, Step(), Token())

    raw = metadata["estimated_input_tokens_before"]
    assert metadata["calibrated_input_tokens_before"] < raw
    assert metadata["calibrated_input_tokens_before"] == pytest.approx(raw * 0.75, abs=1)


def test_missing_model_context_window_is_reported_as_a_fallback():
    runtime = FakeRuntime(
        [ModelResponse(text="summary", finish_reason="stop")],
        profile_cls=_FallbackProfile,
    )
    session = Session([AIMessage(role=MessageRole.USER, content="hello")])

    _messages, metadata = prepare_context(runtime, session, Step(), Token())

    assert metadata["context_window_fallback"] is True
    assert metadata["context_limits"]["source"] == "runtime_fallback"


def test_declared_model_context_window_is_not_reported_as_a_fallback():
    runtime = FakeRuntime([ModelResponse(text="summary", finish_reason="stop")])
    session = Session([AIMessage(role=MessageRole.USER, content="hello")])

    _messages, metadata = prepare_context(runtime, session, Step(), Token())

    assert metadata["context_window_fallback"] is False


def _forced_compaction_runtime(responses):
    return FakeRuntime(responses, profile_cls=_profile(auto_compact=1))


def test_committed_summary_drops_inline_reasoning_blocks():
    runtime = _forced_compaction_runtime(
        [
            ModelResponse(
                text="<think>Let me reconsider what I have read so far.</think>\n\nPROGRESS: branch created.",
                finish_reason="stop",
            )
        ]
    )
    session = Session([AIMessage(role=MessageRole.USER, content="build the feature")])

    prepare_context(runtime, session, Step(), Token())

    summary = runtime.commits[-1]["summary"]
    assert summary == "PROGRESS: branch created."
    assert "<think>" not in summary


def test_summary_of_serialized_tool_markup_is_retried_not_committed():
    # MiniMax-style text-encoded tool calls: not native tool_calls, so the old
    # guard let them through and the "summary" became tool-call markup.
    markup = (
        "]<]minimax[>[<tool_call>\n"
        "]<]minimax[>[<invoke name=\"read_workspace_text\">]<]minimax[>[<path>a.go]<]minimax[>[</path>"
    )
    runtime = _forced_compaction_runtime(
        [
            ModelResponse(text=markup, finish_reason="stop"),
            ModelResponse(text="PROGRESS: audited the OAuth handlers.", finish_reason="stop"),
        ]
    )
    session = Session([AIMessage(role=MessageRole.USER, content="build the feature")])

    prepare_context(runtime, session, Step(), Token())

    assert len(runtime.model_executor.requests) == 2
    assert runtime.commits[-1]["summary"] == "PROGRESS: audited the OAuth handlers."


def test_persistent_serialized_tool_markup_fails_instead_of_committing_garbage():
    markup = "<invoke name=\"exec\"><argv><item>cmd</item></argv></invoke>"
    runtime = _forced_compaction_runtime(
        [ModelResponse(text=markup, finish_reason="stop") for _ in range(3)]
    )
    session = Session([AIMessage(role=MessageRole.USER, content="build the feature")])

    with pytest.raises(RuntimeError, match="tool-call markup instead of a summary"):
        prepare_context(runtime, session, Step(), Token())

    assert runtime.commits == []


def test_reasoning_only_summary_is_retried_rather_than_committed_empty():
    runtime = _forced_compaction_runtime(
        [
            ModelResponse(text="<think>still thinking</think>", finish_reason="stop"),
            ModelResponse(text="PROGRESS: done reading.", finish_reason="stop"),
        ]
    )
    session = Session([AIMessage(role=MessageRole.USER, content="build the feature")])

    prepare_context(runtime, session, Step(), Token())

    assert len(runtime.model_executor.requests) == 2
    assert runtime.commits[-1]["summary"] == "PROGRESS: done reading."


def test_observed_incident_shape_does_not_compact_under_its_real_window():
    """Replay of session aba1af18: provider peaked at 21,225 of a real 128k model.

    Loom compacted four times because the profile declared no window (32,768
    fallback) and the estimator ran ~1.23x hot. With the window declared, the
    same history is nowhere near any threshold.
    """
    limits = Limits(context_window_tokens=131_072, output_reserve_tokens=4_096)
    history = _history_near(26_000, transient_tokens=20)
    runtime = FakeRuntime(
        [],  # compacting at 21k of a 128k window would be the bug
        events=_calibration_events(
            8, estimated=26_357, reported=21_118, provider_total=21_225
        ),
        profile_cls=_profile(auto_compact=117_964, window=131_072),
        limits=limits,
    )
    session = Session(history)

    _messages, metadata = prepare_context(runtime, session, Step(), Token())

    assert runtime.commits == []
    assert metadata.get("auto_compacted") is None
    assert metadata["context_window_fallback"] is False
    assert metadata["estimator_calibration"] == pytest.approx(0.8013, abs=1e-3)
