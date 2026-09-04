from __future__ import annotations

from app.agent_runtime import (
    AgentEventKind,
    AgentRuntime,
    AgentStatus,
    ContextCheckpointStore,
    FileAgentSessionStore,
    PermissionMode,
    SandboxManager,
    SandboxPolicy,
    compaction_split_index,
)
from app.agent_runtime.workspace_tools import loom_default_tools
from app.ai import AGENT_FAST_ROLE, AIMessage, MessageRole, ModelResponse, ToolCall


class ScriptedPlatform:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def execute_chat(self, profile_id, request):
        self.requests.append((profile_id, request))
        if not self.responses:
            raise AssertionError("scripted platform ran out of responses")
        return self.responses.pop(0)


def _runtime(tmp_path, responses=()):
    store = FileAgentSessionStore(tmp_path / "state")
    runtime = AgentRuntime(
        platform=ScriptedPlatform(responses),
        store=store,
        tools=loom_default_tools(),
        sandbox_manager=SandboxManager(policy=SandboxPolicy.OFF),
    )
    return runtime, store


def test_model_request_contains_authoritative_runtime_state(tmp_path):
    runtime, store = _runtime(tmp_path, [ModelResponse(text="done")])
    workspace = tmp_path / "project"
    workspace.mkdir()
    session = runtime.create_session(
        AGENT_FAST_ROLE.role_id,
        workspace_dir=workspace,
        permission_mode=PermissionMode.WORKSPACE,
    )
    runtime.set_goal(session.session_id, "Finish the refactor", token_budget=5000)

    result = runtime.start_turn(session.session_id, "Inspect the project.")

    assert result.status is AgentStatus.COMPLETED
    request = runtime.platform.requests[0][1]
    assert request.messages[0].role is MessageRole.SYSTEM
    context = request.messages[1]
    assert context.role is MessageRole.SYSTEM
    assert context.name == "loom_runtime_state"
    assert "LOOM_RUNTIME_STATE v1" in context.content
    assert str(workspace.resolve()) in context.content
    assert '"mode": "workspace"' in context.content
    assert "Finish the refactor" in context.content
    assert '"policy": "off"' in context.content

    requested = [
        event for event in store.events(session.session_id)
        if event.kind is AgentEventKind.MODEL_REQUESTED
    ]
    assert len(requested[-1].data["context_digest"]) == 64
    runtime.close()


def test_runtime_state_envelope_is_transient_not_persisted_as_chat_history(tmp_path):
    runtime, store = _runtime(tmp_path, [ModelResponse(text="done")])
    workspace = tmp_path / "project"
    workspace.mkdir()
    session = runtime.create_session(AGENT_FAST_ROLE.role_id, workspace_dir=workspace)

    runtime.start_turn(session.session_id, "hello")
    loaded = store.load(session.session_id)

    assert all(message.name != "loom_runtime_state" for message in loaded.messages)
    assert [message.role for message in loaded.messages] == [
        MessageRole.USER,
        MessageRole.ASSISTANT,
    ]
    runtime.close()


def test_compaction_split_starts_at_user_and_keeps_tool_pair_together():
    messages = (
        AIMessage(role=MessageRole.USER, content="first"),
        AIMessage(
            role=MessageRole.ASSISTANT,
            content="",
            tool_calls=(ToolCall(call_id="call-1", name="echo", arguments={"text": "x"}),),
        ),
        AIMessage(
            role=MessageRole.TOOL,
            content='{"ok":true}',
            name="echo",
            tool_call_id="call-1",
        ),
        AIMessage(role=MessageRole.ASSISTANT, content="tool done"),
        AIMessage(role=MessageRole.USER, content="second"),
        AIMessage(role=MessageRole.ASSISTANT, content="second answer"),
    )

    split = compaction_split_index(messages, keep_recent=3)

    assert split == 4
    assert messages[split].role is MessageRole.USER
    assert messages[1].tool_calls[0].call_id == messages[2].tool_call_id
    assert split > 2


def test_context_checkpoint_archives_old_history_before_replacing_active_transcript(tmp_path):
    runtime, store = _runtime(tmp_path)
    workspace = tmp_path / "project"
    workspace.mkdir()
    session = runtime.create_session(AGENT_FAST_ROLE.role_id, workspace_dir=workspace)
    session.status = AgentStatus.COMPLETED
    session.messages = [
        AIMessage(role=MessageRole.USER, content="user one"),
        AIMessage(role=MessageRole.ASSISTANT, content="answer one"),
        AIMessage(role=MessageRole.USER, content="user two"),
        AIMessage(role=MessageRole.ASSISTANT, content="answer two"),
        AIMessage(role=MessageRole.USER, content="user three"),
        AIMessage(role=MessageRole.ASSISTANT, content="answer three"),
        AIMessage(role=MessageRole.USER, content="user four"),
        AIMessage(role=MessageRole.ASSISTANT, content="answer four"),
    ]
    store.save(session)

    checkpoint = runtime.compact_context(
        session.session_id,
        "Earlier discussion covered the first two exchanges.",
        keep_recent=4,
    )
    loaded = store.load(session.session_id)

    assert checkpoint.archived_message_count == 4
    assert checkpoint.retained_message_count == 4
    assert len(loaded.messages) == 5
    assert loaded.messages[0].role is MessageRole.SYSTEM
    assert loaded.messages[0].name == "loom_compaction"
    assert checkpoint.checkpoint_id in loaded.messages[0].content
    assert loaded.messages[1].content == "user three"

    checkpoint_store = ContextCheckpointStore(store.root)
    restored_archive = checkpoint_store.load(session.session_id, checkpoint.checkpoint_id)
    assert [message.content for message in restored_archive.archived_messages] == [
        "user one",
        "answer one",
        "user two",
        "answer two",
    ]
    events = store.events(session.session_id)
    checkpoint_events = [
        event for event in events if event.kind is AgentEventKind.CONTEXT_CHECKPOINTED
    ]
    assert checkpoint_events[-1].data["archived_messages"] == 4
    runtime.close()


def test_context_compaction_refuses_active_turn(tmp_path):
    runtime, store = _runtime(tmp_path)
    workspace = tmp_path / "project"
    workspace.mkdir()
    session = runtime.create_session(AGENT_FAST_ROLE.role_id, workspace_dir=workspace)
    session.status = AgentStatus.RUNNING
    session.messages = [
        AIMessage(role=MessageRole.USER, content="one"),
        AIMessage(role=MessageRole.ASSISTANT, content="two"),
        AIMessage(role=MessageRole.USER, content="three"),
        AIMessage(role=MessageRole.ASSISTANT, content="four"),
    ]
    store.save(session)

    try:
        runtime.compact_context(session.session_id, "summary", keep_recent=2)
    except RuntimeError as exc:
        assert "active" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("active turn compaction should fail")
    runtime.close()
