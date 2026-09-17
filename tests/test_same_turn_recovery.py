from __future__ import annotations

import pytest

from app.agent_runtime import (
    AgentEventKind,
    AgentStatus,
    DurableAgentRuntime,
    FileAgentSessionStore,
)
from app.ai import AIMessage, MessageRole, ModelResponse


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
    runtime = DurableAgentRuntime(
        platform=ScriptedPlatform(responses),
        store=store,
    )
    return runtime, store


def _safely_suspended_session(runtime, store, project):
    session = runtime.create_session("agent.fast", workspace_dir=project)
    session.current_turn_id = "same-logical-turn"
    session.status = AgentStatus.RUNNING
    session.model_steps = 2
    session.messages = [
        AIMessage(role=MessageRole.USER, content="finish the merge without redoing completed checks"),
        AIMessage(role=MessageRole.ASSISTANT, content="four files are already resolved; two remain"),
    ]
    store.save(session)
    return session


def test_safe_handoff_recovers_same_turn_without_new_user_input(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    runtime, store = _runtime(tmp_path, [ModelResponse(text="finished remaining two files")])
    session = _safely_suspended_session(runtime, store, project)
    user_events_before = [
        event for event in store.events(session.session_id) if event.kind is AgentEventKind.USER_MESSAGE
    ]

    result = runtime.recover_turn_if_idle(session.session_id, "same-logical-turn")
    restored = store.load(session.session_id)

    assert result.status is AgentStatus.COMPLETED
    assert result.turn_id == "same-logical-turn"
    assert restored.current_turn_id == "same-logical-turn"
    assert restored.final_text == "finished remaining two files"
    assert sum(message.role is MessageRole.USER for message in restored.messages) == 1
    assert all("Continue pursuing this durable Loom goal" not in str(message.content) for message in restored.messages)
    user_events_after = [
        event for event in store.events(session.session_id) if event.kind is AgentEventKind.USER_MESSAGE
    ]
    assert len(user_events_after) == len(user_events_before)
    assert runtime.platform.requests
    request_messages = runtime.platform.requests[0][1].messages
    assert any(
        message.role is MessageRole.ASSISTANT
        and "four files are already resolved" in str(message.content)
        for message in request_messages
    )


def test_same_turn_recovery_rejects_pending_execution_state(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    runtime, store = _runtime(tmp_path)
    session = _safely_suspended_session(runtime, store, project)
    session.pending_step_id = "step-with-unknown-action-outcome"
    store.save(session)

    with pytest.raises(RuntimeError, match="unknown outcome"):
        runtime.recover_turn_if_idle(session.session_id, "same-logical-turn")

    restored = store.load(session.session_id)
    assert restored.status is AgentStatus.RUNNING
    assert restored.current_turn_id == "same-logical-turn"
    assert restored.pending_step_id == "step-with-unknown-action-outcome"


def test_same_turn_recovery_requires_exact_existing_turn_id(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    runtime, store = _runtime(tmp_path)
    session = _safely_suspended_session(runtime, store, project)

    with pytest.raises(ValueError, match="does not match"):
        runtime.recover_turn_if_idle(session.session_id, "different-turn")
