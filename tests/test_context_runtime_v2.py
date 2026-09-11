from __future__ import annotations

from dataclasses import dataclass

import pytest

from app.ai import (
    AIMessage,
    MessageRole,
    ModelContextLimits,
    ModelResponse,
    ModelUsage,
    ToolCall,
    ToolDefinition,
)
from app.agent_runtime.context_budget import ContextBudgetExceeded, prepare_context
from app.agent_runtime.context_limits import resolve_context_limits


class ScriptedExecutor:
    def __init__(self, responses=()):
        self.responses = list(responses)
        self.requests = []

    def execute(self, _platform, profile_id, request, _token):
        self.requests.append((profile_id, request))
        if not self.responses:
            raise AssertionError("unexpected model call")
        return self.responses.pop(0)


class InstructionLoader:
    def load(self, _workspace):
        return ""


class Envelope:
    digest = "v2-digest"


class Token:
    cancelled = False


@dataclass
class Limits:
    context_window_tokens: int = 3000
    output_reserve_tokens: int = 500
    max_messages: int = 160
    max_tool_result_chars: int = 20_000


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
        self.messages = list(messages)
        self.workspace_dir = "/tmp/project"
        self.profile_id = "agent.fast"
        self.communication_language = "auto"
        self.usage = ModelUsage()


class FakeRuntime:
    def __init__(self, *, limits=None, context_limits=None, responses=()):
        self.limits = limits or Limits()
        self.platform = Platform(context_limits)
        self.model_executor = ScriptedExecutor(responses)
        self.instruction_loader = InstructionLoader()
        self.commits = []

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
    ):
        self.commits.append(
            {
                "summary": summary,
                "repaired": repaired,
                "archived": tuple(archived),
                "retained": tuple(retained),
                "summary_source": summary_source,
                "summary_usage": summary_usage,
            }
        )
        session.messages = [
            AIMessage(role=MessageRole.SYSTEM, name="loom_compaction", content=summary),
            *retained,
        ]


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


def test_recent_large_tool_output_is_reduced_without_mutating_canonical_history(monkeypatch):
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
    runtime = FakeRuntime(limits=Limits(context_window_tokens=3000, output_reserve_tokens=500))

    messages, metadata = prepare_context(runtime, session, Step(), Token())

    visible_tool = next(message for message in messages if message.role is MessageRole.TOOL)
    assert len(visible_tool.content) < len(canonical_tool_output)
    assert "context_reduced" in visible_tool.content
    assert visible_tool.tool_call_id == "call-1"
    assert session.messages[-1].content == canonical_tool_output
    assert runtime.model_executor.requests == []
    assert metadata["tool_outputs_reduced"] >= 1
    assert metadata["estimated_input_tokens_after"] < metadata["estimated_input_tokens_before"]


def test_proactive_compaction_uses_model_threshold_before_hard_limit(monkeypatch):
    monkeypatch.delenv("LOOM_CONTEXT_WINDOW_TOKENS", raising=False)
    monkeypatch.delenv("LOOM_OUTPUT_RESERVE_TOKENS", raising=False)
    context_limits = ModelContextLimits(
        context_window_tokens=9000,
        effective_context_percent=100,
        output_reserve_tokens=1000,
        auto_compact_token_limit=1400,
        tool_output_token_limit=1200,
    )
    history = []
    for index in range(7):
        history.extend(
            [
                AIMessage(role=MessageRole.USER, content=f"user-{index} " + ("u" * 260)),
                AIMessage(role=MessageRole.ASSISTANT, content=f"answer-{index} " + ("a" * 260)),
            ]
        )
    session = Session(history)
    runtime = FakeRuntime(
        limits=Limits(context_window_tokens=3000, output_reserve_tokens=500),
        context_limits=context_limits,
        responses=[ModelResponse(text="compact summary", finish_reason="stop")],
    )

    messages, metadata = prepare_context(runtime, session, Step(), Token())

    assert metadata["auto_compacted"] is True
    assert metadata["context_limits"]["source"] == "model_profile"
    assert runtime.model_executor.requests
    assert runtime.commits[-1]["summary"] == "compact summary"
    assert any(message.content == "compact summary" for message in messages)


def test_latest_giant_user_message_is_only_truncated_in_model_visible_copy(monkeypatch):
    monkeypatch.delenv("LOOM_CONTEXT_WINDOW_TOKENS", raising=False)
    monkeypatch.delenv("LOOM_OUTPUT_RESERVE_TOKENS", raising=False)
    giant = "G" * 18_000
    history = [
        AIMessage(role=MessageRole.USER, content="old request " + ("o" * 700)),
        AIMessage(role=MessageRole.ASSISTANT, content="old answer " + ("a" * 700)),
        AIMessage(role=MessageRole.USER, content="middle request " + ("m" * 700)),
        AIMessage(role=MessageRole.ASSISTANT, content="middle answer " + ("n" * 700)),
        AIMessage(role=MessageRole.USER, content=giant),
        AIMessage(role=MessageRole.ASSISTANT, content="working"),
    ]
    session = Session(history)
    runtime = FakeRuntime(
        limits=Limits(context_window_tokens=5000, output_reserve_tokens=700),
        responses=[ModelResponse(text="summary", finish_reason="stop")],
    )

    messages, metadata = prepare_context(runtime, session, Step(), Token())

    visible_users = [message for message in messages if message.role is MessageRole.USER]
    assert any("omitted for context budget" in str(message.content) for message in visible_users)
    assert session.messages[-2].content == giant
    assert metadata["user_messages_truncated"] >= 1


def test_schema_only_overflow_fails_with_diagnostics_instead_of_generic_error(monkeypatch):
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
