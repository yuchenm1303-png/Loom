"""Lightweight latency diagnostics for one agent turn.

The values are derived from durable event timestamps rather than process-local
clocks, so they remain inspectable after restart and require no new session
state. They are diagnostics only and never affect scheduling or tool execution.
"""
from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from .contracts import AgentEvent, AgentEventKind


_TERMINAL_KINDS = {
    AgentEventKind.TURN_COMPLETED,
    AgentEventKind.TURN_FAILED,
    AgentEventKind.TURN_CANCELLED,
    AgentEventKind.TURN_INTERRUPTED,
    AgentEventKind.LIMIT_REACHED,
}


def _timestamp(value: str) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def _elapsed_ms(start: str, end: str) -> int | None:
    left = _timestamp(start)
    right = _timestamp(end)
    if left is None or right is None:
        return None
    try:
        elapsed = (right - left).total_seconds() * 1000
    except TypeError:
        return None
    return max(0, int(round(elapsed)))


def turn_timing_metadata(
    events: Sequence[AgentEvent],
    *,
    turn_id: str,
    kind: AgentEventKind,
    now: str,
) -> dict[str, int]:
    """Return observability metadata for a request/action/terminal event."""

    turn_events = [event for event in events if event.turn_id == turn_id]
    started = next(
        (event for event in turn_events if event.kind is AgentEventKind.TURN_STARTED),
        None,
    )
    if started is None:
        return {}

    elapsed = _elapsed_ms(started.created_at, now)
    metadata: dict[str, int] = {}
    if elapsed is not None:
        metadata["turn_elapsed_ms"] = elapsed

    model_requests = [
        event for event in turn_events if event.kind is AgentEventKind.MODEL_REQUESTED
    ]
    tool_requests = [
        event for event in turn_events if event.kind is AgentEventKind.TOOL_REQUESTED
    ]

    if kind is AgentEventKind.MODEL_REQUESTED and not model_requests:
        if elapsed is not None:
            metadata["first_model_request_latency_ms"] = elapsed
        return metadata

    if kind is AgentEventKind.TOOL_REQUESTED and not tool_requests:
        if elapsed is not None:
            metadata["first_action_latency_ms"] = elapsed
        metadata["model_requests_before_first_action"] = len(model_requests)
        metadata["model_rejections_before_first_action"] = sum(
            event.kind is AgentEventKind.MODEL_RESPONSE_REJECTED
            for event in turn_events
        )
        return metadata

    if kind not in _TERMINAL_KINDS:
        return metadata

    if model_requests:
        first_model_latency = _elapsed_ms(started.created_at, model_requests[0].created_at)
        if first_model_latency is not None:
            metadata["first_model_request_latency_ms"] = first_model_latency

    if tool_requests:
        first_action = tool_requests[0]
        first_action_latency = _elapsed_ms(started.created_at, first_action.created_at)
        if first_action_latency is not None:
            metadata["first_action_latency_ms"] = first_action_latency
        before_action = []
        for event in turn_events:
            if event is first_action:
                break
            before_action.append(event)
        metadata["model_requests_before_first_action"] = sum(
            event.kind is AgentEventKind.MODEL_REQUESTED for event in before_action
        )
        metadata["model_rejections_before_first_action"] = sum(
            event.kind is AgentEventKind.MODEL_RESPONSE_REJECTED for event in before_action
        )

    return metadata


__all__ = ["turn_timing_metadata"]
