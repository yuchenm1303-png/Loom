from __future__ import annotations

from app.agent_runtime import AgentEventKind, AgentRuntime, AgentStatus, FileAgentSessionStore, SandboxManager, SandboxPolicy
from app.agent_runtime.context_compaction import SUMMARY_PREFIX, summarization_prompt
from app.agent_runtime.workspace_tools import loom_default_tools
from app.ai import AGENT_FAST_ROLE, AIMessage, MessageRole, ModelResponse, ModelUsage, ToolCall, ToolChoice


class ScriptedPlatform:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def execute_chat(self, profile_id, request):
        self.requests.append((profile_id, request))
        if not self.responses:
            raise AssertionError("scripted platform ran out of responses")
        return self.responses.pop(0)


def _runtime(tmp_path, responses):
    workspace = tmp_path / "project"
    workspace.mkdir()
    platform = ScriptedPlatform(responses)
    store = FileAgentSessionStore(tmp_path / "state")
    runtime = AgentRuntime(
        platform=platform,
        store=store,
        tools=loom_default_tools(),
        sandbox_manager=SandboxManager(policy=SandboxPolicy.OFF),
    )
    session = runtime.create_session(AGENT_FAST_ROLE.role_id, workspace_dir=workspace)
    session.status = AgentStatus.COMPLETED
    session.messages = [
        AIMessage(role=MessageRole.USER, content="question one"),
        AIMessage(role=MessageRole.ASSISTANT, content="answer one"),
        AIMessage(role=MessageRole.USER, content="question two"),
        AIMessage(role=MessageRole.ASSISTANT, content="answer two"),
        AIMessage(role=MessageRole.USER, content="question three"),
        AIMessage(role=MessageRole.ASSISTANT, content="answer three"),
        AIMessage(role=MessageRole.USER, content="question four"),
        AIMessage(role=MessageRole.ASSISTANT, content="answer four"),
    ]
    store.save(session)
    return runtime, platform, store, session


def test_model_compaction_is_separate_no_tool_task_and_counts_usage(tmp_path):
    runtime, platform, store, session = _runtime(
        tmp_path,
        [
            ModelResponse(
                text="The earlier discussion established A and B; C remains unresolved.",
                usage=ModelUsage(input_tokens=120, output_tokens=20, total_tokens=140),
            )
        ],
    )

    checkpoint = runtime.compact_context_with_model(session.session_id, keep_recent=4)

    assert checkpoint.archived_message_count == 8
    assert checkpoint.retained_message_count == 4
    assert checkpoint.summary.startswith("The earlier discussion")
    assert len(platform.requests) == 1
    request = platform.requests[0][1]
    assert request.tool_choice is ToolChoice.NONE
    assert request.tools == ()
    assert request.messages[0].role is MessageRole.SYSTEM
    assert request.messages[-1].role is MessageRole.USER
    assert request.messages[-1].content == summarization_prompt("latin")
    assert request.messages[1].name == "loom_communication_language"
    assert [message.content for message in request.messages[2:-1]] == [
        "question one",
        "answer one",
        "question two",
        "answer two",
        "question three",
        "answer three",
        "question four",
        "answer four",
    ]

    loaded = store.load(session.session_id)
    assert loaded.usage.total_tokens == 140
    assert [message.content for message in loaded.messages[:-1]] == [
        "question one",
        "question two",
        "question three",
        "question four",
    ]
    assert loaded.messages[-1].role is MessageRole.USER
    assert loaded.messages[-1].name == "loom_compaction"
    assert str(loaded.messages[-1].content).startswith(SUMMARY_PREFIX)
    events = [
        event for event in store.events(session.session_id)
        if event.kind is AgentEventKind.CONTEXT_CHECKPOINTED
    ]
    assert events[-1].data["summary_source"] == "model"
    assert events[-1].data["communication_language"] == "latin"
    assert events[-1].data["summary_usage"]["total_tokens"] == 140
    runtime.close()


def test_manual_compaction_retries_invalid_tool_response_then_succeeds(tmp_path):
    unexpected = ModelResponse(
        tool_calls=(ToolCall(call_id="compact-tool", name="exec", arguments={"cmd": "git status"}),),
        finish_reason="tool_calls",
    )
    runtime, platform, store, session = _runtime(
        tmp_path,
        [unexpected, ModelResponse(text="safe manual summary")],
    )

    checkpoint = runtime.compact_context_with_model(session.session_id)

    assert checkpoint.summary == "safe manual summary"
    assert len(platform.requests) == 2
    assert all(request.tool_choice is ToolChoice.NONE for _, request in platform.requests)
    assert all(request.tools == () for _, request in platform.requests)
    loaded = store.load(session.session_id)
    assert loaded.messages[-1].name == "loom_compaction"
    runtime.close()


def test_manual_compaction_falls_back_after_repeated_invalid_tool_responses(tmp_path):
    unexpected = ModelResponse(
        tool_calls=(ToolCall(call_id="compact-tool", name="exec", arguments={}),),
        finish_reason="tool_calls",
    )
    runtime, platform, store, session = _runtime(
        tmp_path,
        [unexpected for _ in range(3)],
    )

    checkpoint = runtime.compact_context_with_model(session.session_id)

    assert len(platform.requests) == runtime.limits.model_retries + 1
    assert checkpoint.summary.startswith("Deterministic Loom checkpoint")
    loaded = store.load(session.session_id)
    assert loaded.messages[-1].name == "loom_compaction"
    events = [
        event for event in store.events(session.session_id)
        if event.kind is AgentEventKind.CONTEXT_CHECKPOINTED
    ]
    assert events[-1].data["summary_source"] == "model_fallback"
    runtime.close()
