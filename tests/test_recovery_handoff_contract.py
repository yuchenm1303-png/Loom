from __future__ import annotations

from app.agent_runtime import AgentEventKind, AgentRuntime, AgentStatus, FileAgentSessionStore
from app.agent_runtime.workspace_tools import loom_default_tools
from app.ai import AIMessage, MessageRole


class NoModelCalls:
    def execute_chat(self, profile_id, request):
        raise AssertionError("recovery classification must not sample the model")


def _runtime(tmp_path):
    return AgentRuntime(
        platform=NoModelCalls(),
        store=FileAgentSessionStore(tmp_path / "state"),
        tools=loom_default_tools(),
    )


def test_restart_load_does_not_terminalize_potential_safe_handoff(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    runtime = _runtime(tmp_path)
    session = runtime.create_session("agent.fast", workspace_dir=project)
    session.status = AgentStatus.RUNNING
    session.current_turn_id = "handoff-turn"
    session.messages = [AIMessage(role=MessageRole.USER, content="continue this exact turn")]
    runtime.store.save(session)

    restarted = _runtime(tmp_path)
    restored = restarted.get_session(session.session_id)

    # A persisted active snapshot is not, by itself, proof of an unclean crash.
    # Window05 must first distinguish live rejoin / trusted handoff / unclean loss.
    assert restored.status is AgentStatus.RUNNING
    assert restored.current_turn_id == "handoff-turn"
    assert restored.messages == session.messages
    assert not [
        event
        for event in restarted.store.events(session.session_id)
        if event.kind is AgentEventKind.TURN_INTERRUPTED
    ]


def test_unclean_finalization_is_explicit_and_preserves_logical_turn_id(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    runtime = _runtime(tmp_path)
    session = runtime.create_session("agent.fast", workspace_dir=project)
    session.status = AgentStatus.RUNNING
    session.current_turn_id = "unclean-turn"
    runtime.store.save(session)

    restarted = _runtime(tmp_path)
    result = restarted.recover_interrupted(session.session_id)
    restored = restarted.store.load(session.session_id)

    assert result.status is AgentStatus.INTERRUPTED
    assert restored.current_turn_id == "unclean-turn"
    assert len([
        event
        for event in restarted.store.events(session.session_id)
        if event.kind is AgentEventKind.TURN_INTERRUPTED
    ]) == 1
