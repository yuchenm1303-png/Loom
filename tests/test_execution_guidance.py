from __future__ import annotations

from app.ai import ToolCall
from app.agent_runtime.contracts import AgentEvent, AgentEventKind
from app.agent_runtime.execution_guidance import (
    model_execution_guidance,
    recent_read_only_repeat_count,
    recent_tool_repeat_count,
    tool_call_fingerprint,
)


def _event(kind: AgentEventKind, **data) -> AgentEvent:
    return AgentEvent(
        event_id=f"event-{len(data)}-{kind.value}",
        session_id="11111111-1111-1111-1111-111111111111",
        turn_id="turn-1",
        kind=kind,
        created_at="2026-01-01T00:00:00Z",
        data=data,
    )


def test_fingerprint_is_stable_across_argument_key_order():
    left = ToolCall(call_id="left", name="read", arguments={"path": "a", "limit": 3})
    right = ToolCall(call_id="right", name="read", arguments={"limit": 3, "path": "a"})

    assert tool_call_fingerprint(left) == tool_call_fingerprint(right)


def test_repeat_count_stops_at_intervening_mutation():
    fingerprint = "same"
    events = (
        _event(
            AgentEventKind.TOOL_STARTED,
            tool="read",
            effect="read_only",
            call_fingerprint=fingerprint,
        ),
        _event(
            AgentEventKind.TOOL_STARTED,
            tool="write",
            effect="mutating",
            call_fingerprint="write",
        ),
        _event(
            AgentEventKind.TOOL_STARTED,
            tool="read",
            effect="read_only",
            call_fingerprint=fingerprint,
        ),
    )

    assert recent_read_only_repeat_count(
        events,
        turn_id="turn-1",
        fingerprint=fingerprint,
    ) == 1


def test_guidance_reports_new_repeat_once_and_advances_thresholds():
    events = (
        _event(
            AgentEventKind.MODEL_REQUESTED,
            convergence_checkpoint=16,
        ),
        _event(
            AgentEventKind.TOOL_STARTED,
            tool="read_workspace_text",
            effect="read_only",
            call_fingerprint="same",
            repeat_count=1,
        ),
    )

    message, metadata = model_execution_guidance(events, turn_id="turn-1", tool_calls=32)

    assert message is not None
    assert "read_workspace_text" in message.content
    assert "32 tool calls" in message.content
    assert "discriminating verification" in message.content
    assert metadata == {
        "duplicate_read_only_calls": 1,
        "convergence_checkpoint": 32,
    }

    delivered = (*events, _event(AgentEventKind.MODEL_REQUESTED, **metadata))
    message, metadata = model_execution_guidance(delivered, turn_id="turn-1", tool_calls=32)
    assert message is None
    assert metadata == {}


def test_sensitive_repeat_is_advisory_even_across_other_sensitive_calls():
    events = (
        _event(
            AgentEventKind.TOOL_STARTED,
            tool="exec",
            effect="sensitive",
            call_fingerprint="same-ssh-probe",
        ),
        _event(
            AgentEventKind.TOOL_STARTED,
            tool="exec",
            effect="sensitive",
            call_fingerprint="different-command",
        ),
    )

    assert recent_tool_repeat_count(
        events,
        turn_id="turn-1",
        fingerprint="same-ssh-probe",
    ) == 1

    repeated = (*events, _event(
        AgentEventKind.TOOL_STARTED,
        tool="exec",
        effect="sensitive",
        call_fingerprint="same-ssh-probe",
        repeat_count=1,
    ))
    message, metadata = model_execution_guidance(
        repeated,
        turn_id="turn-1",
        tool_calls=3,
    )

    assert message is not None
    assert "sensitive operation" in message.content
    assert "durable result" in message.content
    assert metadata == {"duplicate_sensitive_calls": 1}

def test_guidance_intervenes_before_small_task_becomes_broad_audit():
    events = (
        _event(AgentEventKind.MODEL_REQUESTED),
        _event(
            AgentEventKind.TOOL_STARTED,
            tool="read_workspace_text",
            effect="read_only",
            call_fingerprint="one",
            repeat_count=0,
        ),
        _event(
            AgentEventKind.TOOL_STARTED,
            tool="read_workspace_text",
            effect="read_only",
            call_fingerprint="two",
            repeat_count=0,
        ),
        _event(
            AgentEventKind.TOOL_STARTED,
            tool="exec",
            effect="sensitive",
            call_fingerprint="three",
            repeat_count=0,
        ),
        _event(
            AgentEventKind.TOOL_STARTED,
            tool="read_workspace_text",
            effect="read_only",
            call_fingerprint="four",
            repeat_count=0,
        ),
    )

    message, metadata = model_execution_guidance(events, turn_id="turn-1", tool_calls=4)

    assert message is not None
    assert "4 tool calls" in message.content
    assert "stop broadening the audit" in message.content
    assert metadata == {"convergence_checkpoint": 4}

