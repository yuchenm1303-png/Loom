from __future__ import annotations

from app.agent_runtime.contracts import AgentEvent, AgentEventKind
from app.agent_runtime.turn_timing import turn_timing_metadata


def _event(kind: AgentEventKind, created_at: str) -> AgentEvent:
    return AgentEvent(
        event_id=f"{kind.value}-{created_at}",
        session_id="session-1",
        turn_id="turn-1",
        kind=kind,
        created_at=created_at,
        data={},
    )


def test_first_model_request_reports_startup_latency():
    events = (_event(AgentEventKind.TURN_STARTED, "2026-09-22T00:00:00.000+00:00"),)

    metadata = turn_timing_metadata(
        events,
        turn_id="turn-1",
        kind=AgentEventKind.MODEL_REQUESTED,
        now="2026-09-22T00:00:00.125+00:00",
    )

    assert metadata == {
        "turn_elapsed_ms": 125,
        "first_model_request_latency_ms": 125,
    }


def test_first_action_reports_model_delay_and_rejections():
    events = (
        _event(AgentEventKind.TURN_STARTED, "2026-09-22T00:00:00.000+00:00"),
        _event(AgentEventKind.MODEL_REQUESTED, "2026-09-22T00:00:00.100+00:00"),
        _event(AgentEventKind.MODEL_RESPONSE_REJECTED, "2026-09-22T00:00:03.000+00:00"),
        _event(AgentEventKind.MODEL_REQUESTED, "2026-09-22T00:00:03.010+00:00"),
    )

    metadata = turn_timing_metadata(
        events,
        turn_id="turn-1",
        kind=AgentEventKind.TOOL_REQUESTED,
        now="2026-09-22T00:00:07.500+00:00",
    )

    assert metadata == {
        "turn_elapsed_ms": 7500,
        "first_action_latency_ms": 7500,
        "model_requests_before_first_action": 2,
        "model_rejections_before_first_action": 1,
    }


def test_terminal_event_repeats_first_action_diagnostics():
    first_action = _event(AgentEventKind.TOOL_REQUESTED, "2026-09-22T00:00:02.250+00:00")
    events = (
        _event(AgentEventKind.TURN_STARTED, "2026-09-22T00:00:00.000+00:00"),
        _event(AgentEventKind.MODEL_REQUESTED, "2026-09-22T00:00:00.050+00:00"),
        first_action,
    )

    metadata = turn_timing_metadata(
        events,
        turn_id="turn-1",
        kind=AgentEventKind.TURN_COMPLETED,
        now="2026-09-22T00:00:05.000+00:00",
    )

    assert metadata == {
        "turn_elapsed_ms": 5000,
        "first_model_request_latency_ms": 50,
        "first_action_latency_ms": 2250,
        "model_requests_before_first_action": 1,
        "model_rejections_before_first_action": 0,
    }
