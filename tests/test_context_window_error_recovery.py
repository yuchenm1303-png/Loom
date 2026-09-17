"""A provider's over-length rejection must compact, not replay the same request.

Loom's configured window is a guess for any model whose profile carries no
context metadata. When that guess is too large the provider is the only
authoritative source, and treating its rejection as a malformed response
retries the identical oversized request with an extra recovery instruction
appended — strictly worse each time, then a failed turn.
"""
from __future__ import annotations

import pytest

from app.agent_runtime import (
    AgentRuntime,
    AgentStatus,
    FileAgentSessionStore,
    SandboxManager,
    SandboxPolicy,
    ToolRegistry,
)
from app.ai import ModelResponse
from app.ai.errors import AIResponseError
from app.agent_runtime.context_budget import (
    is_context_window_error,
    request_forced_compaction,
)


def make_runtime(path, platform, **kwargs):
    return AgentRuntime(
        platform=platform,
        store=FileAgentSessionStore(path),
        tools=ToolRegistry(()),
        sandbox_manager=SandboxManager(policy=SandboxPolicy.OFF),
        **kwargs,
    )


class OverLengthThenComplete:
    """Rejects the first real request, then serves compaction and the retry."""

    def __init__(self, message):
        self.message = message
        self.requests = []

    def execute_chat(self, profile, request):
        self.requests.append(request)
        if len(self.requests) == 1:
            raise AIResponseError(self.message)
        if not request.tools:
            return ModelResponse(text="PROGRESS: earlier context summarized.", finish_reason="stop")
        return ModelResponse(text="recovered", finish_reason="stop")


@pytest.mark.parametrize(
    "message",
    [
        "This model's maximum context length is 8192 tokens",
        "Range of input length should be [1, 65536] - too many tokens",
        "request exceeds the maximum context window for this model",
    ],
)
def test_over_length_rejection_compacts_instead_of_replaying(tmp_path, message):
    platform = OverLengthThenComplete(message)
    runtime = make_runtime(tmp_path, platform)
    session = runtime.create_session("agent.fast")

    result = runtime.start_turn(session.session_id, "continue the work")

    assert result.status is AgentStatus.COMPLETED
    assert result.final_text == "recovered"

    events = runtime.store.events(session.session_id)
    rejected = [e for e in events if e.kind.value == "model_response_rejected"]
    assert rejected[-1].data["reason"] == "context_window_exceeded"

    # The retry must not carry the malformed-response recovery instruction: that
    # only appends text to a request the provider already called too long.
    assert platform.requests[-1].messages[-1].name != "loom_terminal_recovery"

    assert [e for e in events if e.kind.value == "context_checkpointed"]
    runtime.close()


def test_ordinary_malformed_response_still_uses_recovery_instruction(tmp_path):
    platform = OverLengthThenComplete("tool call 'exec' returned invalid JSON arguments")
    runtime = make_runtime(tmp_path, platform)
    session = runtime.create_session("agent.fast")

    result = runtime.start_turn(session.session_id, "continue the work")

    assert result.status is AgentStatus.COMPLETED
    rejected = [
        e
        for e in runtime.store.events(session.session_id)
        if e.kind.value == "model_response_rejected"
    ]
    assert rejected[-1].data["reason"] == "invalid_provider_response"
    assert platform.requests[-1].messages[-1].name == "loom_terminal_recovery"
    runtime.close()


class AlwaysOverLength:
    def __init__(self):
        self.requests = []

    def execute_chat(self, profile, request):
        self.requests.append(request)
        if not request.tools:
            return ModelResponse(text="PROGRESS: summarized.", finish_reason="stop")
        raise AIResponseError("maximum context length exceeded")


def test_a_window_compaction_cannot_fix_fails_with_a_window_specific_error(tmp_path):
    runtime = make_runtime(tmp_path, AlwaysOverLength())
    session = runtime.create_session("agent.fast")

    result = runtime.start_turn(session.session_id, "continue the work")

    assert result.status is AgentStatus.FAILED
    assert "context window is smaller than the configured limits" in result.error
    runtime.close()


@pytest.mark.parametrize(
    "message,expected",
    [
        ("This model's maximum context length is 8192 tokens", True),
        ("too many tokens in request", True),
        ("Input token limit reached", True),
        ("tool call 'exec' returned invalid JSON arguments", False),
        ("streamed tool call is missing id or function name", False),
        ("", False),
    ],
)
def test_context_window_error_detection(message, expected):
    assert is_context_window_error(AIResponseError(message)) is expected


def test_forced_compaction_is_consumed_exactly_once():
    class Runtime:
        pass

    class Session:
        session_id = "s1"

    from app.agent_runtime.context_budget import _consume_forced_compaction

    runtime, session = Runtime(), Session()
    assert _consume_forced_compaction(runtime, session) is False

    request_forced_compaction(runtime, session)
    assert _consume_forced_compaction(runtime, session) is True
    assert _consume_forced_compaction(runtime, session) is False


def test_forced_compaction_does_not_leak_across_sessions():
    class Runtime:
        pass

    class Session:
        def __init__(self, session_id):
            self.session_id = session_id

    from app.agent_runtime.context_budget import _consume_forced_compaction

    runtime = Runtime()
    request_forced_compaction(runtime, Session("a"))

    assert _consume_forced_compaction(runtime, Session("b")) is False
    assert _consume_forced_compaction(runtime, Session("a")) is True
