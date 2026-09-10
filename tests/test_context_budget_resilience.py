from __future__ import annotations

from dataclasses import dataclass, field

from app.ai import AIMessage, MessageRole, ModelResponse, ModelUsage
from app.ai.errors import AITransportError
from app.agent_runtime.context_budget import _fit_compaction_input, prepare_context


class ScriptedExecutor:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def execute(self, _platform, profile_id, request, _token):
        self.requests.append((profile_id, request))
        if not self.responses:
            raise AssertionError("scripted compaction executor ran out of responses")
        value = self.responses.pop(0)
        if isinstance(value, BaseException):
            raise value
        return value


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
    context_window_tokens: int = 2600
    output_reserve_tokens: int = 700
    max_messages: int = 160
    max_tool_result_chars: int = 20_000


@dataclass
class Envelope:
    digest: str = "digest-123"


@dataclass
class Token:
    cancelled: bool = False


@dataclass
class Session:
    messages: list[AIMessage]
    workspace_dir: str = "/tmp/project"
    profile_id: str = "agent.fast"
    usage: ModelUsage = field(default_factory=ModelUsage)


class FakeRuntime:
    def __init__(self, responses, *, limits: Limits | None = None):
        self.limits = limits or Limits()
        self.model_executor = ScriptedExecutor(responses)
        self.platform = object()
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
            AIMessage(
                role=MessageRole.SYSTEM,
                name="loom_compaction",
                content=summary,
            ),
            *retained,
        ]


def _long_history(*, pairs: int = 8, chars: int = 420) -> list[AIMessage]:
    messages = []
    for index in range(pairs):
        messages.append(
            AIMessage(
                role=MessageRole.USER,
                content=f"user-{index}: " + ("u" * chars),
            )
        )
        messages.append(
            AIMessage(
                role=MessageRole.ASSISTANT,
                content=f"assistant-{index}: " + ("a" * chars),
            )
        )
    return messages


def test_completed_model_response_is_usable_even_when_provider_reports_length() -> None:
    """Regression for RuntimeError: compaction did not produce a complete summary.

    Codex waits for response completion and uses the resulting assistant message;
    it does not apply a provider-specific finish-reason allow-list afterwards.
    """
    runtime = FakeRuntime(
        [
            ModelResponse(
                text="Earlier work established the implementation plan and the current blocker.",
                finish_reason="length",
                usage=ModelUsage(input_tokens=100, output_tokens=80, total_tokens=180),
            )
        ]
    )
    session = Session(_long_history())

    messages, metadata = prepare_context(runtime, session, Step(), Token())

    assert len(runtime.model_executor.requests) == 1
    assert runtime.commits[-1]["summary_source"] == "auto"
    assert runtime.commits[-1]["summary"].startswith("Earlier work established")
    assert runtime.commits[-1]["summary_usage"].total_tokens == 180
    assert metadata["auto_compacted"] is True
    assert metadata["compaction_attempts"] == 1
    assert messages[1].name == "loom_compaction"


def test_provider_specific_success_finish_reason_is_not_rejected() -> None:
    runtime = FakeRuntime(
        [ModelResponse(text="Compact handoff summary.", finish_reason="eos_token")]
    )
    session = Session(_long_history())

    _messages, metadata = prepare_context(runtime, session, Step(), Token())

    assert runtime.commits[-1]["summary"] == "Compact handoff summary."
    assert metadata["compaction_attempts"] == 1


def test_empty_compaction_output_retries_without_failing_main_turn_immediately() -> None:
    runtime = FakeRuntime(
        [
            ModelResponse(
                text="",
                finish_reason="stop",
                usage=ModelUsage(input_tokens=80, output_tokens=0, total_tokens=80),
            ),
            ModelResponse(
                text="Retry produced a usable compact handoff.",
                finish_reason="stop",
                usage=ModelUsage(input_tokens=80, output_tokens=25, total_tokens=105),
            ),
        ]
    )
    session = Session(_long_history())

    _messages, metadata = prepare_context(runtime, session, Step(), Token())

    assert len(runtime.model_executor.requests) == 2
    assert runtime.commits[-1]["summary_source"] == "auto_retry"
    assert runtime.commits[-1]["summary"] == "Retry produced a usable compact handoff."
    assert runtime.commits[-1]["summary_usage"].total_tokens == 185
    assert metadata["compaction_attempts"] == 2


def test_compaction_helper_uses_full_reserved_output_budget() -> None:
    limits = Limits(
        context_window_tokens=10_000,
        output_reserve_tokens=4096,
        max_messages=160,
        max_tool_result_chars=20_000,
    )
    runtime = FakeRuntime(
        [ModelResponse(text="A concise summary.", finish_reason="stop")],
        limits=limits,
    )
    session = Session(_long_history(pairs=12, chars=900))

    prepare_context(runtime, session, Step(), Token())

    request = runtime.model_executor.requests[0][1]
    assert request.max_output_tokens == 4096


def test_oversized_compaction_input_drops_oldest_safe_history_like_codex() -> None:
    messages = (
        AIMessage(role=MessageRole.USER, content="oldest-user " + ("x" * 3000)),
        AIMessage(role=MessageRole.ASSISTANT, content="oldest-answer " + ("y" * 3000)),
        AIMessage(role=MessageRole.USER, content="recent-user " + ("r" * 500)),
        AIMessage(role=MessageRole.ASSISTANT, content="recent-answer " + ("s" * 500)),
    )

    compact_input, request = _fit_compaction_input(
        messages,
        budget=900,
        max_output_tokens=400,
    )
    request_text = "\n".join(str(message.content) for message in request.messages)

    assert len(compact_input) < len(messages)
    assert "oldest-user" not in request_text
    assert "recent-user" in request_text
    assert "recent-answer" in request_text


def test_provider_context_window_error_trims_oldest_and_retries() -> None:
    runtime = FakeRuntime(
        [
            AITransportError("context_length_exceeded: maximum context length reached"),
            ModelResponse(
                text="Summary after dropping the oldest compact input.",
                finish_reason="stop",
            ),
        ],
        limits=Limits(
            context_window_tokens=6000,
            output_reserve_tokens=1200,
            max_messages=160,
            max_tool_result_chars=20_000,
        ),
    )
    session = Session(_long_history(pairs=10, chars=650))

    _messages, metadata = prepare_context(runtime, session, Step(), Token())

    assert len(runtime.model_executor.requests) == 2
    first = runtime.model_executor.requests[0][1]
    second = runtime.model_executor.requests[1][1]
    assert len(second.messages) < len(first.messages)
    assert runtime.commits[-1]["summary"].startswith("Summary after dropping")
    # Codex resets ordinary stream-retry count after context trimming; this is
    # still the first successful response attempt from Loom's perspective.
    assert metadata["compaction_attempts"] == 1
