from __future__ import annotations

from dataclasses import dataclass

from app.ai import AIMessage, MessageRole, ModelResponse, ModelUsage
from app.agent_runtime.context_budget import prepare_context


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
    context_window_tokens: int = 2200
    output_reserve_tokens: int = 400
    max_messages: int = 160
    max_tool_result_chars: int = 20_000


class Envelope:
    digest = "digest-123"


class Token:
    cancelled = False


class Session:
    def __init__(self, messages):
        self.messages = list(messages)
        self.workspace_dir = "/tmp/project"
        self.profile_id = "agent.fast"
        self.usage = ModelUsage()


class FakeRuntime:
    def __init__(self, responses, *, limits=None):
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


def _long_history(*, pairs=8, chars=420):
    messages = []
    for index in range(pairs):
        messages.append(
            AIMessage(role=MessageRole.USER, content=f"user-{index}: " + ("u" * chars))
        )
        messages.append(
            AIMessage(role=MessageRole.ASSISTANT, content=f"assistant-{index}: " + ("a" * chars))
        )
    return messages


def test_provider_specific_success_finish_reason_is_accepted():
    runtime = FakeRuntime(
        [ModelResponse(text="Complete compact summary.", finish_reason="eos_token")]
    )
    session = Session(_long_history())

    _messages, metadata = prepare_context(runtime, session, Step(), Token())

    assert runtime.commits[-1]["summary"] == "Complete compact summary."
    assert metadata["auto_compacted"] is True
    assert metadata["compaction_attempts"] == 1


def test_length_completion_retries_instead_of_killing_the_turn():
    runtime = FakeRuntime(
        [
            ModelResponse(
                text="partial summary",
                finish_reason="length",
                usage=ModelUsage(input_tokens=100, output_tokens=400, total_tokens=500),
            ),
            ModelResponse(
                text="Complete summary after retry.",
                finish_reason="eos_token",
                usage=ModelUsage(input_tokens=90, output_tokens=30, total_tokens=120),
            ),
        ]
    )
    session = Session(_long_history())

    _messages, metadata = prepare_context(runtime, session, Step(), Token())

    assert len(runtime.model_executor.requests) == 2
    assert runtime.commits[-1]["summary"] == "Complete summary after retry."
    assert runtime.commits[-1]["summary_source"] == "auto_retry"
    assert runtime.commits[-1]["summary_usage"].total_tokens == 620
    assert metadata["compaction_attempts"] == 2


def test_compaction_uses_full_reserved_output_budget_and_codex_prompt_shape():
    runtime = FakeRuntime(
        [ModelResponse(text="summary", finish_reason="stop")]
    )
    session = Session(_long_history())

    prepare_context(runtime, session, Step(), Token())

    request = runtime.model_executor.requests[0][1]
    assert request.max_output_tokens == runtime.limits.output_reserve_tokens
    assert request.tools == ()
    assert request.messages[-1].role is MessageRole.USER
    assert "compacting earlier canonical conversation history" in request.messages[-1].content


def test_oversized_compaction_request_trims_only_temporary_old_history():
    runtime = FakeRuntime(
        [ModelResponse(text="summary", finish_reason="stop")],
        limits=Limits(context_window_tokens=1800, output_reserve_tokens=300),
    )
    original = _long_history(pairs=9, chars=520)
    session = Session(original)

    _messages, metadata = prepare_context(runtime, session, Step(), Token())

    request = runtime.model_executor.requests[0][1]
    request_text = "\n".join(str(message.content) for message in request.messages)
    assert "user-0:" not in request_text
    assert metadata["compaction_trimmed_messages"] > 0

    # The request clone is trimmed like Codex, but Loom's durable checkpoint still
    # archives the complete canonical prefix selected for compaction.
    archived = runtime.commits[-1]["archived"]
    assert archived
    assert str(archived[0].content).startswith("user-0:")
