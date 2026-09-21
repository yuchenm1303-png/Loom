from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from app.ai import (
    AIMessage,
    ImagePart,
    MessageRole,
    ModelContextLimits,
    ModelResponse,
    ModelUsage,
    ToolCall,
    ToolDefinition,
)
from app.ai.execution_control import ModelCancelled
from app.agent_runtime.context_budget import ContextBudgetExceeded, estimate_tokens, prepare_context
from app.agent_runtime.context_compaction import build_compacted_history
from app.agent_runtime.context_limits import resolve_context_limits
from app.agent_runtime.tools import ToolResult


class ScriptedExecutor:
    def __init__(self, responses=()):
        self.responses = list(responses)
        self.requests = []

    def execute(self, _platform, profile_id, request, _token):
        self.requests.append((profile_id, request))
        if not self.responses:
            raise AssertionError("unexpected model call")
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response


class InstructionLoader:
    def load(self, _workspace):
        return ""


class Envelope:
    digest = "v2-digest"


class Token:
    def __init__(self, cancelled=False):
        self.cancelled = cancelled


@dataclass
class Limits:
    context_window_tokens: int = 3000
    output_reserve_tokens: int = 500
    max_messages: int = 160
    max_tool_result_chars: int = 20_000
    model_retries: int = 2


class Profile:
    def __init__(self, context_limits):
        self.context_limits = context_limits


class Registry:
    def __init__(self, context_limits):
        self.context_limits = context_limits

    def get(self, _profile_id):
        return Profile(self.context_limits)


class Platform:
    def __init__(self, context_limits=None):
        if context_limits is not None:
            self.registry = Registry(context_limits)


class Router:
    def __init__(self, definitions=()):
        self._definitions = tuple(definitions)

    def definitions(self):
        return self._definitions


class Step:
    def __init__(self, definitions=()):
        self.tool_router = Router(definitions)


class Session:
    def __init__(self, messages):
        self.session_id = "session-1"
        self.messages = list(messages)
        self.workspace_dir = "/tmp/project"
        self.profile_id = "agent.fast"
        self.communication_language = "auto"
        self.usage = ModelUsage()


class EventStore:
    def __init__(self, events=()):
        self._events = tuple(events)

    def events(self, _session_id):
        return self._events


class FakeRuntime:
    def __init__(self, *, limits=None, context_limits=None, responses=(), events=()):
        self.limits = limits or Limits()
        self.platform = Platform(context_limits)
        self.model_executor = ScriptedExecutor(responses)
        self.instruction_loader = InstructionLoader()
        self.commits = []
        self.store = EventStore(events)

    def platform_for_session(self, _session_id):
        # Mirrors Runtime.platform_for_session for a runtime with no per-session
        # model override: it falls back to self.platform. Compaction samples the
        # model through this rather than through .platform directly, so a double
        # without it fails every path that compacts.
        return self.platform

    def _context_envelope(self, _session, _step):
        return Envelope()

    def _request_context_messages(self, _session, _step, _envelope):
        return (AIMessage(role=MessageRole.SYSTEM, content="runtime state"),)

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
        self.commits.append(
            {
                "summary": summary,
                "repaired": repaired,
                "archived": tuple(archived),
                "retained": tuple(retained),
                "summary_source": summary_source,
                "summary_usage": summary_usage,
                "replacement_override": replacement_override,
            }
        )
        session.messages = list(
            replacement_override
            if replacement_override is not None
            else build_compacted_history(
                tuple((*archived, *retained)),
                summary,
                token_counter=lambda messages: estimate_tokens(messages),
            )
        )


def _event(kind: str, *, total_tokens: int = 0):
    return SimpleNamespace(
        kind=SimpleNamespace(value=kind),
        data={"usage": {"input_tokens": total_tokens, "output_tokens": 0, "total_tokens": total_tokens}},
    )


def test_inline_image_base64_is_replaced_by_visual_token_cost():
    prefix = "data:image/png;base64,"
    short = AIMessage(role=MessageRole.USER, content=(ImagePart(prefix + "AA"),))
    huge = AIMessage(role=MessageRole.USER, content=(ImagePart(prefix + ("A" * 200_000)),))

    assert estimate_tokens((short,)) == estimate_tokens((huge,))
    assert estimate_tokens((huge,)) < 3000


def test_each_image_is_charged_once_instead_of_each_message_once():
    image = ImagePart("data:image/png;base64," + ("A" * 100_000))
    one = AIMessage(role=MessageRole.USER, content=(image,))
    two = AIMessage(role=MessageRole.USER, content=(image, image))

    delta = estimate_tokens((two,)) - estimate_tokens((one,))

    assert 2000 <= delta <= 2200


def test_model_profile_context_window_beats_conservative_runtime_fallback(monkeypatch):
    monkeypatch.delenv("LOOM_CONTEXT_WINDOW_TOKENS", raising=False)
    monkeypatch.delenv("LOOM_OUTPUT_RESERVE_TOKENS", raising=False)
    runtime = FakeRuntime(
        limits=Limits(context_window_tokens=3000, output_reserve_tokens=500),
        context_limits=ModelContextLimits(
            context_window_tokens=10_000,
            effective_context_percent=90,
            output_reserve_tokens=1000,
            auto_compact_token_limit=6000,
            tool_output_token_limit=1500,
        ),
    )
    session = Session([AIMessage(role=MessageRole.USER, content="hello")])

    resolved = resolve_context_limits(runtime, session)

    assert resolved.source == "model_profile"
    assert resolved.context_window_tokens == 10_000
    assert resolved.effective_context_window_tokens == 9000
    assert resolved.input_budget_tokens == 8000
    assert resolved.output_reserve_tokens == 1000
    assert resolved.auto_compact_token_limit == 6000
    assert resolved.tool_output_token_limit == 1500


def test_default_tool_output_limit_scales_with_model_window(monkeypatch):
    monkeypatch.delenv("LOOM_CONTEXT_WINDOW_TOKENS", raising=False)
    monkeypatch.delenv("LOOM_OUTPUT_RESERVE_TOKENS", raising=False)
    runtime = FakeRuntime(
        context_limits=ModelContextLimits(
            context_window_tokens=54_000,
            effective_context_percent=95,
            output_reserve_tokens=1000,
        )
    )
    session = Session([AIMessage(role=MessageRole.USER, content="hello")])

    resolved = resolve_context_limits(runtime, session)

    # Codex currently budgets about 10k tool-result tokens in a 272k window.
    # A ~51k effective Loom window should therefore keep roughly 1.9k, not 6k.
    assert 1700 <= resolved.tool_output_token_limit <= 2000


def test_tool_result_model_payload_is_token_bounded_and_keeps_tail():
    result = ToolResult(
        ok=False,
        content="BEGIN\n" + ("middle-line\n" * 2000) + "FINAL ERROR: compiler failed\n",
        data={"exit_code": 1, "verbose": "x" * 4000},
    )

    payload = result.model_payload(max_tokens=1200)

    assert "BEGIN" in payload
    assert "FINAL ERROR: compiler failed" in payload
    assert "middle of tool output omitted from model context" in payload
    assert '"truncated":true' in payload
    assert '"truncation":"head_tail"' in payload
    assert '"exact_result_remains_in_durable_transcript":true' in payload
    assert len(payload.encode("utf-8")) <= 1200 * 3 + 96


def test_default_auto_compact_limit_uses_raw_window_with_effective_hard_cap(monkeypatch):
    monkeypatch.delenv("LOOM_CONTEXT_WINDOW_TOKENS", raising=False)
    monkeypatch.delenv("LOOM_OUTPUT_RESERVE_TOKENS", raising=False)
    runtime = FakeRuntime(
        context_limits=ModelContextLimits(
            context_window_tokens=10_000,
            effective_context_percent=80,
            output_reserve_tokens=1000,
        )
    )
    resolved = resolve_context_limits(
        runtime,
        Session([AIMessage(role=MessageRole.USER, content="hello")]),
    )

    assert resolved.effective_context_window_tokens == 8000
    # Codex's default auto threshold is 90% of the raw window (9000), while
    # the effective 80% window is an independent hard cap. The earliest safe
    # trigger is therefore 8000, not 90% of 8000.
    assert resolved.auto_compact_token_limit == 8000


def test_normal_projection_proactively_bounds_legacy_tool_output(monkeypatch):
    monkeypatch.delenv("LOOM_CONTEXT_WINDOW_TOKENS", raising=False)
    monkeypatch.delenv("LOOM_OUTPUT_RESERVE_TOKENS", raising=False)
    call = ToolCall(call_id="call-1", name="read_file", arguments={"path": "large.log"})
    canonical_tool_output = '{"ok":true,"content":"' + ("x" * 18_000) + '"}'
    session = Session(
        [
            AIMessage(role=MessageRole.USER, content="inspect the file"),
            AIMessage(role=MessageRole.ASSISTANT, content="", tool_calls=(call,)),
            AIMessage(
                role=MessageRole.TOOL,
                content=canonical_tool_output,
                name="read_file",
                tool_call_id="call-1",
            ),
        ]
    )
    runtime = FakeRuntime(limits=Limits(context_window_tokens=30_000, output_reserve_tokens=3000))

    messages, metadata = prepare_context(runtime, session, Step(), Token())

    visible_tool = next(message for message in messages if message.role is MessageRole.TOOL)
    assert visible_tool.content != canonical_tool_output
    assert "middle of tool output omitted for context budget" in visible_tool.content
    # The request projection is bounded, but canonical/durable session history is
    # not destructively rewritten by context budgeting.
    assert session.messages[-1].content == canonical_tool_output
    assert runtime.model_executor.requests == []
    assert metadata["tool_outputs_reduced"] == 1
    assert metadata["estimated_tokens_saved"] > 0
    assert metadata["user_messages_truncated"] == 0


def test_proactive_compaction_uses_model_threshold_before_hard_limit(monkeypatch):
    monkeypatch.delenv("LOOM_CONTEXT_WINDOW_TOKENS", raising=False)
    monkeypatch.delenv("LOOM_OUTPUT_RESERVE_TOKENS", raising=False)
    context_limits = ModelContextLimits(
        context_window_tokens=9000,
        effective_context_percent=100,
        output_reserve_tokens=1000,
        auto_compact_token_limit=800,
    )
    history = []
    for index in range(7):
        history.extend(
            [
                AIMessage(role=MessageRole.USER, content=f"user-{index} " + ("u" * 500)),
                AIMessage(role=MessageRole.ASSISTANT, content=f"answer-{index} " + ("a" * 500)),
            ]
        )
    session = Session(history)
    runtime = FakeRuntime(
        context_limits=context_limits,
        responses=[ModelResponse(text="compact summary", finish_reason="stop")],
    )

    messages, metadata = prepare_context(runtime, session, Step(), Token())

    assert metadata["auto_compacted"] is True
    assert runtime.commits[-1]["summary"] == "compact summary"
    assert runtime.commits[-1]["retained"] == ()
    assert all(message.role is not MessageRole.ASSISTANT for message in session.messages)
    assert all(message.role is not MessageRole.TOOL for message in session.messages)
    assert session.messages[-1].role is MessageRole.USER
    assert session.messages[-1].name == "loom_compaction"
    assert any(message.name == "loom_compaction" for message in messages)


def test_provider_usage_is_primary_auto_compact_signal(monkeypatch):
    monkeypatch.delenv("LOOM_CONTEXT_WINDOW_TOKENS", raising=False)
    monkeypatch.delenv("LOOM_OUTPUT_RESERVE_TOKENS", raising=False)
    limits = ModelContextLimits(
        context_window_tokens=10_000,
        effective_context_percent=100,
        output_reserve_tokens=1000,
        auto_compact_token_limit=4000,
    )
    session = Session([AIMessage(role=MessageRole.USER, content="small history")])
    runtime = FakeRuntime(
        context_limits=limits,
        responses=[ModelResponse(text="summary")],
        events=[_event("model_response", total_tokens=4500)],
    )

    _messages, metadata = prepare_context(runtime, session, Step(), Token())

    assert metadata["token_accounting_source"] == "provider_usage"
    assert metadata["active_context_tokens"] == 4500
    assert metadata["auto_compacted"] is True


def test_projected_tool_reduction_prevents_stale_provider_usage_compaction(monkeypatch):
    """A previous full request must not override the smaller request we can send now."""
    monkeypatch.delenv("LOOM_CONTEXT_WINDOW_TOKENS", raising=False)
    monkeypatch.delenv("LOOM_OUTPUT_RESERVE_TOKENS", raising=False)
    limits = ModelContextLimits(
        context_window_tokens=10_000,
        effective_context_percent=100,
        output_reserve_tokens=1000,
        auto_compact_token_limit=4000,
        tool_output_token_limit=500,
    )
    call = ToolCall(call_id="call-large", name="read_file", arguments={"path": "large.log"})
    session = Session(
        [
            AIMessage(role=MessageRole.USER, content="inspect the log"),
            AIMessage(role=MessageRole.ASSISTANT, content="", tool_calls=(call,)),
            AIMessage(
                role=MessageRole.TOOL,
                content="x" * 30_000,
                name="read_file",
                tool_call_id="call-large",
            ),
        ]
    )
    runtime = FakeRuntime(
        context_limits=limits,
        responses=(),
        events=[_event("model_response", total_tokens=4500)],
    )

    messages, metadata = prepare_context(runtime, session, Step(), Token())

    assert metadata["token_accounting_source"] == "provider_usage"
    # The current meter includes tool output appended after the provider's last
    # usage sample, while the compaction decision uses the bounded projection.
    assert metadata["active_context_tokens"] > 4500
    assert metadata["tool_outputs_reduced"] == 1
    assert metadata.get("auto_compacted") is not True
    assert runtime.model_executor.requests == []
    assert runtime.commits == []
    assert next(message for message in messages if message.role is MessageRole.TOOL).content != "x" * 30_000


def test_checkpoint_invalidates_stale_provider_usage(monkeypatch):
    monkeypatch.delenv("LOOM_CONTEXT_WINDOW_TOKENS", raising=False)
    monkeypatch.delenv("LOOM_OUTPUT_RESERVE_TOKENS", raising=False)
    limits = ModelContextLimits(
        context_window_tokens=10_000,
        effective_context_percent=100,
        output_reserve_tokens=1000,
        auto_compact_token_limit=4000,
    )
    session = Session([AIMessage(role=MessageRole.USER, content="small history")])
    runtime = FakeRuntime(
        context_limits=limits,
        events=[
            _event("model_response", total_tokens=9000),
            _event("context_checkpointed"),
        ],
    )

    _messages, metadata = prepare_context(runtime, session, Step(), Token())

    assert metadata["token_accounting_source"] == "fallback_estimate"
    assert "auto_compacted" not in metadata
    assert runtime.model_executor.requests == []


def test_compaction_archives_complete_tool_group_but_does_not_replay_it(monkeypatch):
    monkeypatch.delenv("LOOM_CONTEXT_WINDOW_TOKENS", raising=False)
    monkeypatch.delenv("LOOM_OUTPUT_RESERVE_TOKENS", raising=False)
    call = ToolCall(call_id="call-1", name="read_file", arguments={"path": "a.txt"})
    history = [
        AIMessage(role=MessageRole.USER, content="read it"),
        AIMessage(role=MessageRole.ASSISTANT, content="", tool_calls=(call,)),
        AIMessage(role=MessageRole.TOOL, content="contents", name="read_file", tool_call_id="call-1"),
        AIMessage(role=MessageRole.ASSISTANT, content="done"),
        AIMessage(role=MessageRole.USER, content="continue"),
    ]
    session = Session(history)
    runtime = FakeRuntime(
        context_limits=ModelContextLimits(
            context_window_tokens=8000,
            effective_context_percent=100,
            output_reserve_tokens=1000,
            auto_compact_token_limit=1,
        ),
        responses=[ModelResponse(text="summary")],
    )

    prepare_context(runtime, session, Step(), Token())

    archived = runtime.commits[-1]["archived"]
    assert archived[1].tool_calls[0].call_id == "call-1"
    assert archived[2].tool_call_id == "call-1"
    assert [message.role for message in session.messages] == [
        MessageRole.USER,
        MessageRole.USER,
        MessageRole.USER,
    ]


def test_cancelled_compaction_does_not_mutate_history(monkeypatch):
    monkeypatch.delenv("LOOM_CONTEXT_WINDOW_TOKENS", raising=False)
    monkeypatch.delenv("LOOM_OUTPUT_RESERVE_TOKENS", raising=False)
    original = [AIMessage(role=MessageRole.USER, content="hello")]
    session = Session(original)
    runtime = FakeRuntime(
        context_limits=ModelContextLimits(
            context_window_tokens=8000,
            effective_context_percent=100,
            output_reserve_tokens=1000,
            auto_compact_token_limit=1,
        )
    )

    with pytest.raises(ModelCancelled):
        prepare_context(runtime, session, Step(), Token(cancelled=True))

    assert session.messages == original
    assert runtime.commits == []


def test_context_window_change_updates_default_compaction_limit(monkeypatch):
    monkeypatch.delenv("LOOM_CONTEXT_WINDOW_TOKENS", raising=False)
    monkeypatch.delenv("LOOM_OUTPUT_RESERVE_TOKENS", raising=False)
    context = ModelContextLimits(
        context_window_tokens=10_000,
        effective_context_percent=100,
        output_reserve_tokens=1000,
    )
    runtime = FakeRuntime(context_limits=context)
    session = Session([AIMessage(role=MessageRole.USER, content="hello")])
    first = resolve_context_limits(runtime, session)

    runtime.platform.registry.context_limits = ModelContextLimits(
        context_window_tokens=20_000,
        effective_context_percent=100,
        output_reserve_tokens=1000,
    )
    second = resolve_context_limits(runtime, session)

    assert first.auto_compact_token_limit == 9000
    assert second.auto_compact_token_limit == 18_000


def test_schema_only_overflow_fails_with_diagnostics(monkeypatch):
    monkeypatch.delenv("LOOM_CONTEXT_WINDOW_TOKENS", raising=False)
    monkeypatch.delenv("LOOM_OUTPUT_RESERVE_TOKENS", raising=False)
    huge_tool = ToolDefinition(
        name="huge_tool",
        description="d" * 12_000,
        input_schema={"type": "object", "properties": {"value": {"type": "string"}}},
    )
    session = Session([AIMessage(role=MessageRole.USER, content="hello")])
    runtime = FakeRuntime(limits=Limits(context_window_tokens=2200, output_reserve_tokens=400))

    with pytest.raises(ContextBudgetExceeded) as caught:
        prepare_context(runtime, session, Step((huge_tool,)), Token())

    error = caught.value
    assert error.tool_schema_tokens > 0
    assert error.estimated_tokens > error.input_budget_tokens
    assert "tool_schemas=" in str(error)
