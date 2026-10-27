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

    # Pair actual request/receipt times. Parallel tools contribute their union,
    # not a sum that can exceed the entire turn's elapsed wall time.
    pending_model = None
    pending_tools = {}
    tool_intervals = []
    model_ms = preparation_ms = retry_wait_ms = queue_ms = first_content_ms = 0
    generation_ms = first_content_samples = 0
    compaction_ms = 0
    rejected_ms = 0
    for event in turn_events:
        data = event.data
        if event.kind is AgentEventKind.MODEL_REQUESTED:
            pending_model = event.created_at
            preparation_ms += int(data.get("request_preparation_ms") or 0)
            if not data.get("request_preparation_reused"):
                compaction_ms += int(data.get("context_compaction_ms") or 0)
            retry_wait_ms += int(data.get("retry_wait_ms") or 0)
        elif event.kind in {AgentEventKind.MODEL_RESPONSE, AgentEventKind.MODEL_RESPONSE_REJECTED}:
            duration = _elapsed_ms(pending_model, event.created_at) if pending_model else 0
            model_ms += duration or 0
            if event.kind is AgentEventKind.MODEL_RESPONSE_REJECTED:
                rejected_ms += duration or 0
            timing = data.get("stream_timing") or {}
            queue_ms += int(timing.get("queue_wait_ms") or 0)
            first_content_ms += int(timing.get("first_content_ms") or 0)
            if timing.get("first_content_ms") is not None:
                first_content_samples += 1
                generation_ms += max(0, int(timing.get("execution_ms") or 0) - int(timing["first_content_ms"]))
            pending_model = None
        elif event.kind is AgentEventKind.TOOL_STARTED:
            pending_tools[str(data.get("call_id"))] = event.created_at
        elif event.kind in {AgentEventKind.TOOL_COMPLETED, AgentEventKind.TOOL_FAILED}:
            start = pending_tools.pop(str(data.get("call_id")), None)
            left, right = _elapsed_ms(started.created_at, start) if start else None, _elapsed_ms(started.created_at, event.created_at)
            if left is not None and right is not None:
                tool_intervals.append((left, right))
    tool_ms = 0
    if pending_model:
        model_ms += _elapsed_ms(pending_model, now) or 0
    for start in pending_tools.values():
        left, right = _elapsed_ms(started.created_at, start), _elapsed_ms(started.created_at, now)
        if left is not None and right is not None:
            tool_intervals.append((left, right))
    previous_end = 0
    for left, right in sorted(tool_intervals):
        tool_ms += max(0, right - max(left, previous_end))
        previous_end = max(previous_end, right)
    metadata.update(model_round_trips=len(model_requests), model_wait_ms=model_ms,
                    model_rejected_ms=rejected_ms, model_queue_ms=queue_ms,
                    model_time_to_content_ms=first_content_ms, tool_wall_ms=tool_ms,
                    model_generation_ms=generation_ms, model_first_content_samples=first_content_samples,
                    request_preparation_total_ms=preparation_ms, retry_wait_total_ms=retry_wait_ms,
                    context_compaction_ms=compaction_ms,
                    context_assembly_ms=max(0, preparation_ms - compaction_ms),
                    runtime_other_ms=max(0, (elapsed or 0) - model_ms - tool_ms - preparation_ms - retry_wait_ms))

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
