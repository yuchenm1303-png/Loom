from __future__ import annotations

import pytest

from app.agent_runtime import (
    AgentEventKind,
    AgentLimits,
    AgentStatus,
    DurableAgentRuntime,
    FileAgentSessionStore,
    PendingToolApproval,
    ToolEffect,
)
from app.agent_runtime.handoff_recovery import install as install_handoff_recovery
from app.agent_runtime.workspace_tools import loom_default_tools
from app.ai import AIMessage, MessageRole, ModelResponse, ToolCall


class ScriptedPlatform:
    def __init__(self, responses=()):
        self.responses = list(responses)
        self.requests = []

    def execute_chat(self, profile_id, request):
        self.requests.append((profile_id, request))
        if not self.responses:
            raise AssertionError("recovery must not sample the model at this boundary")
        return self.responses.pop(0)


def _runtime(tmp_path, responses=()):
    install_handoff_recovery()
    platform = ScriptedPlatform(responses)
    runtime = DurableAgentRuntime(
        platform=platform,
        store=FileAgentSessionStore(tmp_path / "state"),
        tools=loom_default_tools(),
    )
    return runtime, platform


def _running_session(runtime, tmp_path, *, turn_id="recover-turn"):
    project = tmp_path / "project"
    project.mkdir(exist_ok=True)
    session = runtime.create_session("agent.fast", workspace_dir=project)
    session.status = AgentStatus.RUNNING
    session.current_turn_id = turn_id
    session.messages = [AIMessage(role=MessageRole.USER, content="finish the same turn")]
    runtime.store.save(session)
    return session


def _failed_transport(runtime, session, *, retryable: bool) -> None:
    session.status = AgentStatus.FAILED
    session.error = "AITransportError: connection lost"
    runtime.store.save(session)
    runtime._record(
        session,
        AgentEventKind.TURN_FAILED,
        data={
            "error": session.error,
            "retryable_transport": retryable,
        },
    )


def test_retry_budgets_keep_semantic_repairs_small_and_transport_recovery_longer():
    limits = AgentLimits()

    assert limits.model_retries == 2
    assert limits.transport_retries == 5


def test_safe_handoff_resumes_same_turn_without_duplicating_user_message(tmp_path):
    original, _ = _runtime(tmp_path)
    session = _running_session(original, tmp_path)

    restarted, platform = _runtime(tmp_path, [ModelResponse(text="resumed safely")])
    result = restarted.recover_turn_if_idle(session.session_id, session.current_turn_id)
    restored = restarted.store.load(session.session_id)

    assert result.status is AgentStatus.COMPLETED
    assert result.turn_id == "recover-turn"
    assert restored.current_turn_id == "recover-turn"
    assert restored.final_text == "resumed safely"
    assert len(platform.requests) == 1
    assert [message.role for message in restored.messages].count(MessageRole.USER) == 1
    assert len([
        event
        for event in restarted.store.events(session.session_id)
        if event.kind is AgentEventKind.TURN_COMPLETED
    ]) == 1


def test_transport_failed_turn_resumes_same_turn_after_network_returns(tmp_path):
    original, _ = _runtime(tmp_path)
    session = _running_session(original, tmp_path, turn_id="network-recovery")
    _failed_transport(original, session, retryable=True)

    restarted, platform = _runtime(tmp_path, [ModelResponse(text="back online")])
    result = restarted.recover_turn_if_idle(session.session_id, "network-recovery")
    restored = restarted.store.load(session.session_id)

    assert result.status is AgentStatus.COMPLETED
    assert result.turn_id == "network-recovery"
    assert restored.current_turn_id == "network-recovery"
    assert restored.final_text == "back online"
    assert restored.error == ""
    assert len(platform.requests) == 1
    assert [message.role for message in restored.messages].count(MessageRole.USER) == 1
    recovery_starts = [
        event
        for event in restarted.store.events(session.session_id)
        if event.kind is AgentEventKind.TURN_STARTED
        and event.data.get("source") == "network_recovery"
    ]
    assert len(recovery_starts) == 1


def test_non_retryable_transport_failure_is_not_auto_resumed(tmp_path):
    runtime, platform = _runtime(tmp_path)
    session = _running_session(runtime, tmp_path, turn_id="provider-rejection")
    _failed_transport(runtime, session, retryable=False)

    result = runtime.recover_turn_if_idle(session.session_id, "provider-rejection")

    assert result.status is AgentStatus.FAILED
    assert result.error == "AITransportError: connection lost"
    assert platform.requests == []


def test_transport_failure_without_durable_retryability_is_not_auto_resumed(tmp_path):
    runtime, platform = _runtime(tmp_path)
    session = _running_session(runtime, tmp_path, turn_id="legacy-transport")
    session.status = AgentStatus.FAILED
    session.error = "AITransportError: connection lost"
    runtime.store.save(session)

    result = runtime.recover_turn_if_idle(session.session_id, "legacy-transport")

    assert result.status is AgentStatus.FAILED
    assert platform.requests == []


def test_non_transport_failed_turn_is_not_auto_resumed(tmp_path):
    runtime, platform = _runtime(tmp_path)
    session = _running_session(runtime, tmp_path, turn_id="ordinary-failure")
    session.status = AgentStatus.FAILED
    session.error = "RuntimeError: invalid provider response"
    runtime.store.save(session)

    result = runtime.recover_turn_if_idle(session.session_id, "ordinary-failure")

    assert result.status is AgentStatus.FAILED
    assert result.error == "RuntimeError: invalid provider response"
    assert platform.requests == []


def test_recovery_commits_already_durable_terminal_response_without_resampling(tmp_path):
    runtime, platform = _runtime(tmp_path)
    session = _running_session(runtime, tmp_path, turn_id="terminal-window")
    session.messages.append(AIMessage(role=MessageRole.ASSISTANT, content="already durable"))
    runtime._record(
        session,
        AgentEventKind.MODEL_RESPONSE,
        data={
            "step_id": "terminal-step",
            "text": "already durable",
            "finish_reason": "stop",
            "response_id": "response-1",
            "tool_calls": [],
            "usage": {"input_tokens": 1, "output_tokens": 2, "total_tokens": 3},
        },
    )

    result = runtime.recover_turn_if_idle(session.session_id, "terminal-window")
    restored = runtime.store.load(session.session_id)

    assert result.status is AgentStatus.COMPLETED
    assert restored.final_text == "already durable"
    assert platform.requests == []
    completed = [
        event
        for event in runtime.store.events(session.session_id)
        if event.kind is AgentEventKind.TURN_COMPLETED
    ]
    assert completed[-1].data["recovered"] is True


def test_unresolved_tool_is_interrupted_instead_of_replayed(tmp_path):
    runtime, platform = _runtime(tmp_path)
    session = _running_session(runtime, tmp_path, turn_id="unsafe-tool")
    call = ToolCall(call_id="unknown-outcome", name="echo", arguments={"text": "x"})
    session.pending_tool_calls = [call]
    runtime._record(
        session,
        AgentEventKind.TOOL_REQUESTED,
        data={"call_id": call.call_id, "tool": call.name, "arguments": call.arguments},
    )

    result = runtime.recover_turn_if_idle(session.session_id, "unsafe-tool")

    assert result.status is AgentStatus.INTERRUPTED
    assert platform.requests == []
    interrupted = [
        event
        for event in runtime.store.events(session.session_id)
        if event.kind is AgentEventKind.TURN_INTERRUPTED
    ]
    assert len(interrupted) == 1


def test_lost_approval_context_fails_closed_after_restart(tmp_path):
    original, _ = _runtime(tmp_path)
    session = _running_session(original, tmp_path, turn_id="approval-restart")
    call = ToolCall(call_id="approve-1", name="echo", arguments={"text": "x"})
    session.status = AgentStatus.WAITING_APPROVAL
    session.pending_tool_calls = [call]
    session.pending_step_id = "lost-step"
    session.pending_approval = PendingToolApproval(
        call_id=call.call_id,
        tool_name=call.name,
        arguments=call.arguments,
        effect=ToolEffect.MUTATING,
        reason="approval required",
    )
    original.store.save(session)

    restarted, platform = _runtime(tmp_path)
    result = restarted.recover_turn_if_idle(session.session_id, "approval-restart")

    assert result.status is AgentStatus.INTERRUPTED
    assert platform.requests == []


def test_same_process_approval_rejoin_keeps_valid_waiter(tmp_path):
    runtime, platform = _runtime(tmp_path)
    session = _running_session(runtime, tmp_path, turn_id="approval-live")
    step = runtime._capture_step_context(session, next_model_step=True)
    session.status = AgentStatus.WAITING_APPROVAL
    session.pending_step_id = step.step_id
    session.pending_approval = PendingToolApproval(
        call_id="approve-live",
        tool_name="echo",
        arguments={"text": "x"},
        effect=ToolEffect.MUTATING,
        reason="approval required",
    )
    runtime.store.save(session)

    result = runtime.recover_turn_if_idle(session.session_id, "approval-live")

    assert result.status is AgentStatus.WAITING_APPROVAL
    assert platform.requests == []
    assert runtime.store.load(session.session_id).pending_approval is not None


def test_user_cancelled_turn_is_never_auto_resumed(tmp_path):
    runtime, platform = _runtime(tmp_path)
    session = _running_session(runtime, tmp_path, turn_id="cancelled-turn")
    session.status = AgentStatus.CANCELLED
    session.error = "cancelled by user"
    runtime.store.save(session)

    result = runtime.recover_turn_if_idle(session.session_id, "cancelled-turn")

    assert result.status is AgentStatus.CANCELLED
    assert platform.requests == []


def test_recovery_rejects_different_turn_identity(tmp_path):
    runtime, _ = _runtime(tmp_path)
    session = _running_session(runtime, tmp_path)

    with pytest.raises(ValueError, match="turn_id does not match"):
        runtime.recover_turn_if_idle(session.session_id, "different-turn")
