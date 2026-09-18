from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace

from app.ai import AIMessage, MessageRole, ModelContextLimits, ModelResponse, ModelUsage, ToolCall
from app.ai.errors import AIResponseError
from app.agent_runtime.context_budget import estimate_tokens, prepare_context
from app.agent_runtime.context_compaction import build_compacted_history, summarization_prompt


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
    context_window_tokens: int = 12_000
    output_reserve_tokens: int = 1000
    max_messages: int = 160
    max_tool_result_chars: int = 20_000
    model_retries: int = 2


class Envelope:
    digest = "digest-123"


class Token:
    cancelled = False


class Profile:
    context_limits = ModelContextLimits(
        context_window_tokens=12_000,
        effective_context_percent=100,
        output_reserve_tokens=1000,
        auto_compact_token_limit=1,
    )


class Registry:
    def get(self, _profile_id):
        return Profile()


class Platform:
    registry = Registry()


class Session:
    def __init__(self, messages):
        self.session_id = "session-1"
        self.messages = list(messages)
        self.workspace_dir = "/tmp/project"
        self.profile_id = "agent.fast"
        self.communication_language = "auto"
        self.usage = ModelUsage()


class Store:
    def events(self, _session_id):
        return ()


class FakeRuntime:
    def __init__(self, responses):
        self.limits = Limits()
        self.model_executor = ScriptedExecutor(responses)
        self.platform = Platform()
        self.instruction_loader = InstructionLoader()
        self.store = Store()
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


def _history(pairs=4, chars=240):
    messages = []
    for index in range(pairs):
        messages.append(AIMessage(role=MessageRole.USER, content=f"user-{index}: " + ("u" * chars)))
        messages.append(AIMessage(role=MessageRole.ASSISTANT, content=f"assistant-{index}: " + ("a" * chars)))
    return messages


def _set_roomy_profile(runtime, *, auto_compact_token_limit=10_000, tool_output_token_limit=1000):
    context_limits = ModelContextLimits(
        context_window_tokens=12_000,
        effective_context_percent=100,
        output_reserve_tokens=1000,
        auto_compact_token_limit=auto_compact_token_limit,
        tool_output_token_limit=tool_output_token_limit,
    )
    runtime.platform = SimpleNamespace(
        registry=SimpleNamespace(
            get=lambda _profile_id: SimpleNamespace(context_limits=context_limits)
        )
    )


def test_compaction_request_uses_codex_prompt_as_final_user_message_and_no_tools():
    runtime = FakeRuntime([ModelResponse(text="summary", finish_reason="stop")])
    session = Session(_history())

    prepare_context(runtime, session, Step(), Token())

    request = runtime.model_executor.requests[0][1]
    assert request.tools == ()
    assert request.messages[-1].role is MessageRole.USER
    assert request.messages[-1].content == summarization_prompt("latin")
    assert request.messages[0].role is MessageRole.SYSTEM
    assert runtime.commits[-1]["retained"] == ()


def test_chinese_compaction_request_requires_chinese_summary_language():
    runtime = FakeRuntime([ModelResponse(text="中文摘要：任务仍在继续。")])
    session = Session(_history())
    session.messages.append(AIMessage(role=MessageRole.USER, content="继续检查这个问题"))

    prepare_context(runtime, session, Step(), Token())

    prompt = str(runtime.model_executor.requests[0][1].messages[-1].content)
    assert "Write the entire handoff summary in Chinese" in prompt


def test_wrong_language_compaction_summary_is_retried_before_checkpoint():
    runtime = FakeRuntime([
        ModelResponse(text="This handoff accidentally switched to English."),
        ModelResponse(text="中文摘要：任务仍在继续。"),
    ])
    session = Session(_history())
    session.messages.append(AIMessage(role=MessageRole.USER, content="继续检查这个问题"))

    _messages, metadata = prepare_context(runtime, session, Step(), Token())

    assert len(runtime.model_executor.requests) == 2
    assert runtime.commits[-1]["summary"].startswith("中文摘要")
    assert metadata["compaction_attempts"] == 2


def test_completed_compaction_does_not_invent_semantic_finish_reason_retries():
    runtime = FakeRuntime(
        [
            ModelResponse(
                text="provider-produced summary",
                finish_reason="length",
                usage=ModelUsage(input_tokens=100, output_tokens=30, total_tokens=130),
            )
        ]
    )
    session = Session(_history())

    _messages, metadata = prepare_context(runtime, session, Step(), Token())

    assert len(runtime.model_executor.requests) == 1
    assert runtime.commits[-1]["summary"] == "provider-produced summary"
    assert runtime.commits[-1]["summary_usage"].total_tokens == 130
    assert metadata["compaction_attempts"] == 1


def test_context_window_error_drops_oldest_logical_tool_group_then_retries():
    call = ToolCall(call_id="call-1", name="read_file", arguments={"path": "a.txt"})
    history = [
        AIMessage(role=MessageRole.ASSISTANT, content="", tool_calls=(call,)),
        AIMessage(role=MessageRole.TOOL, content="contents", name="read_file", tool_call_id="call-1"),
        AIMessage(role=MessageRole.USER, content="continue"),
        AIMessage(role=MessageRole.ASSISTANT, content="working"),
    ]
    runtime = FakeRuntime(
        [
            AIResponseError("maximum context window exceeded"),
            ModelResponse(text="summary"),
        ]
    )
    session = Session(history)

    _messages, metadata = prepare_context(runtime, session, Step(), Token())

    assert len(runtime.model_executor.requests) == 2
    first = runtime.model_executor.requests[0][1].messages
    second = runtime.model_executor.requests[1][1].messages
    assert any(message.tool_calls for message in first)
    assert not any(message.tool_calls for message in second)
    assert not any(message.role is MessageRole.TOOL for message in second)
    assert metadata["compaction_trimmed_messages"] == 2

    # The retry trims only the compaction request clone. The durable checkpoint
    # still archives the complete repaired canonical history, including the pair.
    archived = runtime.commits[-1]["archived"]
    assert archived[0].tool_calls[0].call_id == "call-1"
    assert archived[1].tool_call_id == "call-1"


def test_unexpected_compaction_tool_call_retries_without_failing_turn():
    unexpected = ToolCall(call_id="call-compact", name="exec", arguments={"cmd": "git status"})
    runtime = FakeRuntime([
        ModelResponse(
            tool_calls=(unexpected,),
            finish_reason="tool_calls",
            usage=ModelUsage(input_tokens=100, output_tokens=10, total_tokens=110),
        ),
        ModelResponse(
            text="safe summary",
            finish_reason="stop",
            usage=ModelUsage(input_tokens=80, output_tokens=20, total_tokens=100),
        ),
    ])
    session = Session(_history())

    _messages, metadata = prepare_context(runtime, session, Step(), Token())

    assert len(runtime.model_executor.requests) == 2
    assert runtime.commits[-1]["summary"] == "safe summary"
    assert runtime.commits[-1]["summary_usage"] == ModelUsage(
        input_tokens=180,
        output_tokens=30,
        total_tokens=210,
    )
    assert metadata["compaction_attempts"] == 2
    assert metadata["compaction_trimmed_messages"] > 0


def test_repeated_compaction_tool_calls_fall_back_without_failing_turn():
    unexpected = ModelResponse(
        tool_calls=(ToolCall(call_id="call-compact", name="exec", arguments={}),),
        finish_reason="tool_calls",
    )
    runtime = FakeRuntime([unexpected, unexpected, unexpected])
    session = Session(_history())

    _messages, metadata = prepare_context(runtime, session, Step(), Token())

    assert len(runtime.model_executor.requests) == runtime.limits.model_retries + 1
    assert len(runtime.commits) == 1
    assert runtime.commits[-1]["summary_source"] == "auto"
    assert runtime.commits[-1]["summary"].startswith("Deterministic Loom checkpoint")
    assert metadata["compaction_fallback"] == "deterministic"
    assert metadata["compaction_provider_response_invalid"] is True
    assert session.messages[-1].name == "loom_compaction"


def test_replacement_history_contains_real_users_and_summary_not_tool_or_assistant_items():
    call = ToolCall(call_id="call-2", name="echo", arguments={"text": "x"})
    history = [
        AIMessage(role=MessageRole.USER, content="first"),
        AIMessage(role=MessageRole.ASSISTANT, content="", tool_calls=(call,)),
        AIMessage(role=MessageRole.TOOL, content="x", name="echo", tool_call_id="call-2"),
        AIMessage(role=MessageRole.USER, content="second"),
    ]
    runtime = FakeRuntime([ModelResponse(text="summary")])
    session = Session(history)

    prepare_context(runtime, session, Step(), Token())

    assert [message.role for message in session.messages] == [
        MessageRole.USER,
        MessageRole.USER,
        MessageRole.USER,
    ]
    assert [message.content for message in session.messages[:2]] == ["first", "second"]
    assert session.messages[-1].name == "loom_compaction"


def test_oversized_tool_output_is_projected_before_full_compaction():
    call = ToolCall(call_id="call-large", name="read_workspace_text", arguments={"path": "large.go"})
    huge_result = "x" * 40_000
    history = [
        AIMessage(role=MessageRole.USER, content="inspect the auth code"),
        AIMessage(role=MessageRole.ASSISTANT, content="", tool_calls=(call,)),
        AIMessage(
            role=MessageRole.TOOL,
            content=huge_result,
            name="read_workspace_text",
            tool_call_id="call-large",
        ),
        AIMessage(role=MessageRole.USER, content="continue"),
    ]
    runtime = FakeRuntime([])
    _set_roomy_profile(runtime)
    session = Session(history)

    messages, metadata = prepare_context(runtime, session, Step(), Token())

    assert runtime.model_executor.requests == []
    assert runtime.commits == []
    projected_tool = next(message for message in messages if message.role is MessageRole.TOOL)
    assert projected_tool.content != huge_result
    assert "context_reduced" in projected_tool.content
    assert "exact_result_remains_in_durable_transcript" in projected_tool.content
    assert session.messages[2].content == huge_result
    assert metadata["tool_outputs_reduced"] == 1
    assert metadata["estimated_tokens_saved"] > 0
    assert metadata.get("auto_compacted") is not True
