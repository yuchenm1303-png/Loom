from __future__ import annotations

from app.agent_runtime import AgentEventKind, AgentRuntime, AgentStatus, FileAgentSessionStore, SandboxManager, SandboxPolicy
from app.agent_runtime.workspace_tools import loom_default_tools
from app.ai import AGENT_FAST_ROLE, AIMessage, MessageRole, ModelResponse, ModelUsage, ToolChoice


class ScriptedPlatform:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def execute_chat(self, profile_id, request):
        self.requests.append((profile_id, request))
        if not self.responses:
            raise AssertionError("scripted platform ran out of responses")
        return self.responses.pop(0)


def test_model_compaction_is_separate_no_tool_task_and_counts_usage(tmp_path):
    workspace = tmp_path / "project"
    workspace.mkdir()
    platform = ScriptedPlatform(
        [
            ModelResponse(
                text="The earlier discussion established A and B; C remains unresolved.",
                usage=ModelUsage(input_tokens=120, output_tokens=20, total_tokens=140),
            )
        ]
    )
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

    checkpoint = runtime.compact_context_with_model(session.session_id, keep_recent=4)

    assert checkpoint.archived_message_count == 4
    assert checkpoint.retained_message_count == 4
    assert checkpoint.summary.startswith("The earlier discussion")
    assert len(platform.requests) == 1
    request = platform.requests[0][1]
    assert request.tool_choice is ToolChoice.NONE
    assert request.tools == ()
    assert request.messages[0].role is MessageRole.SYSTEM
    assert "Do not invent facts" in request.messages[0].content
    assert [message.content for message in request.messages[1:]] == [
        "question one",
        "answer one",
        "question two",
        "answer two",
    ]

    loaded = store.load(session.session_id)
    assert loaded.usage.total_tokens == 140
    assert loaded.messages[0].name == "loom_compaction"
    events = [
        event for event in store.events(session.session_id)
        if event.kind is AgentEventKind.CONTEXT_CHECKPOINTED
    ]
    assert events[-1].data["summary_source"] == "model"
    assert events[-1].data["summary_usage"]["total_tokens"] == 140
    runtime.close()
