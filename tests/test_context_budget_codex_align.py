from __future__ import annotations

from dataclasses import dataclass, field

from app.ai import AIMessage, MessageRole, ModelResponse, ModelUsage, ToolChoice
from app.agent_runtime.context_budget import (
    _finish_reason_is_incomplete,
    _summary_is_complete,
    prepare_context,
)


class ScriptedExecutor:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def execute(self, _platform, profile_id, request, _token):
        self.requests.append((profile_id, request))
        if not self.responses:
            raise AssertionError("scripted compaction executor ran out of responses")
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response


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
    context_window_tokens: int = 2200
    output_reserve_tokens: int = 400
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
            AIMessage(role=MessageRole.SYSTEM, name="loom_compaction", content=summary),
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


def test_finish_reason_classifier_matches_codex_completion_style() -> None:
    assert _finish_reason_is_incomplete("length") is True
    assert _finish_reason_is_incomplete("max_tokens") is True
    assert _finish_reason_is_incomplete("tool_calls") is True
    assert _finish_reason_is_incomplete("content_filter") is True
    assert _finish_reason_is_incomplete("eos_token") is False
    assert _finish_reason_is_incomplete("finished") is False
    assert _summary_is_complete(ModelResponse(text="summary", finish_reason="eos_token")) is True


def test_compaction_prompt_is_synthesized_user_input_like_codex() -> None:
    runtime = FakeRuntime(
        [
            ModelResponse(
                text="Complete compact summary preserving the earlier task.",
                finish_reason="eos_token",
                usage=ModelUsage(input_tokens=100, output_tokens=40, total_tokens=140),
            ),
        ]
    )
    session = Session(_long_history())

    prepare_context(runtime, session, Step(), Token())

    request = runtime.model_executor.requests[0][1]
    assert request.tools == ()
    assert request.tool_choice is ToolChoice.NONE
    assert request.messages[-1].role is MessageRole.USER
    assert "context" in request.messages[-1].content.casefold()
    assert "summary" in request.messages[-1].content.casefold()


def test_compaction_retries_truncated_summary_and_accepts_provider_eos() -> None:
    runtime = FakeRuntime(
        [
            ModelResponse(
                text="partial summary that hit its output ceiling",
                finish_reason="length",
                usage=ModelUsage(input_tokens=100, output_tokens=400, total_tokens=500),
            ),
            ModelResponse(
                text="Complete compact summary preserving the earlier task and unresolved work.",
                finish_reason="eos_token",
                usage=ModelUsage(input_tokens=100, output_tokens=40, total_tokens=140),
            ),
        ]
    )
    session = Session(_long_history())

    messages, metadata = prepare_context(runtime, session, Step(), Token())

    assert len(runtime.model_executor.requests) == 2
    assert runtime.commits[-1]["summary_source"] == "auto_retry"
    assert runtime.commits[-1]["summary"].startswith("Complete compact summary")
    assert runtime.commits[-1]["summary_usage"].total_tokens == 640
    assert metadata["auto_compacted"] is True
    assert metadata["compaction_attempts"] == 2
    assert metadata["compaction_trimmed_messages"] >= 1
    assert messages[1].name == "loom_compaction"


def test_oversized_compaction_request_trims_oldest_history_before_sampling() -> None:
    history = [
        AIMessage(role=MessageRole.USER, content="old user " + ("x" * 5000)),
        AIMessage(role=MessageRole.ASSISTANT, content="old answer " + ("y" * 5000)),
        AIMessage(role=MessageRole.USER, content="middle user"),
        AIMessage(role=MessageRole.ASSISTANT, content="middle answer"),
        AIMessage(role=MessageRole.USER, content="latest user"),
        AIMessage(role=MessageRole.ASSISTANT, content="latest answer"),
    ]
    runtime = FakeRuntime(
        [ModelResponse(text="Trimmed summary still completes.", finish_reason="stop")],
        limits=Limits(context_window_tokens=1800, output_reserve_tokens=300),
    )
    session = Session(history)

    _messages, metadata = prepare_context(runtime, session, Step(), Token())

    assert len(runtime.model_executor.requests) == 1
    request = runtime.model_executor.requests[0][1]
    assert all("old user" not in str(message.content) for message in request.messages)
    assert runtime.commits[-1]["summary_source"] == "auto_retry"
    assert len(runtime.commits[-1]["archived"]) == 4
    assert metadata["compaction_trimmed_messages"] >= 1
