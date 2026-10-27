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

    expected = {
        "turn_elapsed_ms": 5000,
        "first_model_request_latency_ms": 50,
        "first_action_latency_ms": 2250,
        "model_requests_before_first_action": 1,
        "model_rejections_before_first_action": 0,
    }

    assert all(metadata[key] == value for key, value in expected.items())
    assert metadata["model_round_trips"] == 1


def test_parallel_tools_use_wall_time_union_and_compaction_is_separate():
    from dataclasses import replace
    def event(kind, second, **data):
        return replace(_event(kind, f"2026-09-22T00:00:{second:02d}.000+00:00"), data=data)
    events = [event(AgentEventKind.TURN_STARTED, 0),
              event(AgentEventKind.MODEL_REQUESTED, 1, request_preparation_ms=400, context_compaction_ms=300),
              event(AgentEventKind.MODEL_RESPONSE, 3),
              event(AgentEventKind.TOOL_STARTED, 3, call_id="a"),
              event(AgentEventKind.TOOL_STARTED, 4, call_id="b"),
              event(AgentEventKind.TOOL_COMPLETED, 7, call_id="a"),
              event(AgentEventKind.TOOL_FAILED, 8, call_id="b")]
    data = turn_timing_metadata(events, turn_id="turn-1", kind=AgentEventKind.TURN_COMPLETED,
                                now="2026-09-22T00:00:10.000+00:00")
    assert data["tool_wall_ms"] == 5000
    assert data["model_wait_ms"] == 2000
    assert data["context_compaction_ms"] == 300 and data["context_assembly_ms"] == 100
