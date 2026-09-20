"""An undeclared context window must not be replaced by an invented one.

Codex leaves `context_window` as `None` for a model it has no metadata for, and
every budget check short-circuits on that `None` — it never auto-compacts and
never trims, it sends the request and lets the provider be the authority. Loom
previously substituted a hard-coded 32,768 and compacted a 21k conversation four
times in three minutes. This locks the Codex behaviour, plus the one thing Codex
does not need: learning a ceiling from a rejection, but only a credible one.
"""
from __future__ import annotations

from dataclasses import dataclass

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
            raise AssertionError("compaction ran when it should not have")
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
    # None = the host declared nothing, which is what "unknown window" means.
    context_window_tokens: int | None = None
    output_reserve_tokens: int | None = None
    max_messages: int = 160
    max_tool_result_chars: int = 20_000
    model_retries: int = 2


class Envelope:
    digest = "digest-unknown-window"


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


class UndeclaredProfile:
    """A model whose profile carries no context metadata at all."""

    context_limits = ModelContextLimits()


class DeclaredProfile:
    context_limits = ModelContextLimits(
        context_window_tokens=12_000,
        effective_context_percent=100,
        output_reserve_tokens=1_000,
    )


def _platform(profile_cls):
    class Registry:
        def get(self, _profile_id):
            return profile_cls()

    class Platform:
        registry = Registry()

    return Platform()


class Session:
    def __init__(self, messages):
        self.session_id = "session-unknown-window"
        self.messages = list(messages)
        self.workspace_dir = "/tmp/project"
        self.profile_id = "agent.fast"
        self.communication_language = "auto"
        self.usage = ModelUsage()


class FakeRuntime:
    def __init__(self, responses=(), *, events=(), profile_cls=UndeclaredProfile):
        self.limits = Limits()
        self.model_executor = ScriptedExecutor(responses)
        self.platform = _platform(profile_cls)
        self.instruction_loader = InstructionLoader()
        self.store = Store(events)
        self.commits = []

    def platform_for_session(self, _session_id):
        # Mirrors Runtime.platform_for_session for a runtime with no per-session
        # model override: it falls back to self.platform. Compaction samples the
        # model through this rather than through .platform directly, so a double
        # without it fails every path that compacts.
        return self.platform

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
        self.commits.append({"summary": summary})
        session.messages = list(
            replacement_override
            if replacement_override is not None
            else build_compacted_history(
                tuple((*archived, *retained)),
                summary,
                token_counter=lambda messages: estimate_tokens(messages),
            )
        )


def _history_over(target_tokens):
    """Alternating turns sized past ``target_tokens``, no single item oversized.

    Sized by the estimator itself so each test states the threshold it means to
    cross, instead of encoding a message count that silently stops crossing it.
    """
    messages = []
    index = 0
    while True:
        messages.append(AIMessage(role=MessageRole.USER, content=f"u{index}: " + "u" * 2_000))
        messages.append(AIMessage(role=MessageRole.ASSISTANT, content=f"a{index}: " + "a" * 2_000))
        index += 1
        if estimate_tokens(messages) > target_tokens:
            return messages


def _rejection(size):
    return Event(
        "model_response_rejected",
        {"reason": "context_window_exceeded", "rejected_input_tokens": size},
    )


def _accepted(input_tokens):
    return Event(
        "model_response",
        {"usage": {"input_tokens": input_tokens, "output_tokens": 50, "total_tokens": input_tokens + 50}},
    )


def test_undeclared_window_never_auto_compacts():
    # History well past the fallback-derived 11k budget. Codex would send this.
    history = _history_over(13_000)
    runtime = FakeRuntime()  # no scripted responses: compacting is the failure
    session = Session(history)

    messages, metadata = prepare_context(runtime, session, Step(), Token())

    assert runtime.commits == []
    assert metadata.get("auto_compacted") is None
    assert metadata["context_budget_source"] == "unbounded"
    assert metadata["effective_input_budget_tokens"] is None
    assert metadata["context_limits"]["window_known"] is False
    assert len(messages) == len(history) + 2


def test_declared_window_still_compacts():
    runtime = FakeRuntime(
        [ModelResponse(text="summary", finish_reason="stop")],
        profile_cls=DeclaredProfile,
    )
    session = Session(_history_over(13_000))

    _messages, metadata = prepare_context(runtime, session, Step(), Token())

    assert metadata["auto_compacted"] is True
    assert metadata["context_budget_source"] == "model_metadata"
    assert metadata["context_limits"]["window_known"] is True


def test_a_credible_rejection_becomes_the_budget():
    # Provider served 9k, then refused 14k: a real ceiling sits between them.
    runtime = FakeRuntime(
        [ModelResponse(text="summary", finish_reason="stop")],
        events=(_accepted(9_000), _rejection(14_000)),
    )
    session = Session(_history_over(13_000))

    _messages, metadata = prepare_context(runtime, session, Step(), Token())

    assert metadata["context_budget_source"] == "observed_provider_limit"
    assert metadata["effective_input_budget_tokens"] == 14_000 * 9 // 10
    assert metadata["auto_compacted"] is True


def test_a_rejection_smaller_than_base_context_is_not_believed():
    # A gateway fault or misrouted model can report a context error for a request
    # that is mostly system prompt. Believing it would wedge every later request.
    runtime = FakeRuntime(events=(_rejection(200),))
    session = Session(_history_over(13_000))

    _messages, metadata = prepare_context(runtime, session, Step(), Token())

    assert runtime.commits == []
    assert metadata["context_budget_source"] == "unbounded"


def test_a_rejection_contradicted_by_a_larger_accepted_request_is_not_believed():
    # The same provider already served 20k, so a 12k "limit" is not a limit.
    runtime = FakeRuntime(events=(_accepted(20_000), _rejection(12_000)))
    session = Session(_history_over(13_000))

    _messages, metadata = prepare_context(runtime, session, Step(), Token())

    assert runtime.commits == []
    assert metadata["context_budget_source"] == "unbounded"


def test_the_smallest_rejection_wins():
    runtime = FakeRuntime(
        [ModelResponse(text="summary", finish_reason="stop")],
        events=(_accepted(9_000), _rejection(30_000), _rejection(13_000)),
    )
    session = Session(_history_over(13_000))

    _messages, metadata = prepare_context(runtime, session, Step(), Token())

    assert metadata["effective_input_budget_tokens"] == 13_000 * 9 // 10


def test_an_oversized_single_user_item_is_still_clipped_without_a_window():
    # Structural, not a compaction decision: no summary can shrink one item, so
    # the sanity ceiling still applies even with no declared window.
    # Big enough to exceed the 272k sanity ceiling, which is what the undeclared
    # path now uses for structural checks.
    giant = AIMessage(role=MessageRole.USER, content="G" * 900_000)
    runtime = FakeRuntime()
    session = Session([giant])

    messages, metadata = prepare_context(runtime, session, Step(), Token())

    visible = next(m for m in messages if m.role is MessageRole.USER and not m.name)
    assert len(str(visible.content)) < 900_000
    assert metadata["user_messages_truncated"] == 1
    assert runtime.commits == []


def test_message_cap_still_forces_compaction_without_a_window():
    # The message-count safety net is independent of tokens and must survive.
    runtime = FakeRuntime([ModelResponse(text="summary", finish_reason="stop")])
    runtime.limits = Limits(max_messages=6)
    session = Session(_history_over(13_000))

    _messages, metadata = prepare_context(runtime, session, Step(), Token())

    assert metadata["auto_compacted"] is True


def test_observed_incident_history_does_not_compact_without_a_declared_window():
    """Session aba1af18: provider peaked at 21,225 and Loom compacted four times.

    With no declared window Loom now behaves like Codex and compacts zero times,
    regardless of what the estimator thinks the history costs.
    """
    runtime = FakeRuntime(
        events=(
            _accepted(19_982),
            _accepted(20_700),
            _accepted(21_118),
        ),
    )
    session = Session(_history_over(13_000))

    _messages, metadata = prepare_context(runtime, session, Step(), Token())

    assert runtime.commits == []
    assert metadata.get("auto_compacted") is None
    assert metadata["context_budget_source"] == "unbounded"
