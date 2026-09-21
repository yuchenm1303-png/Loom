from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence

from app.ai import AIMessage, MessageRole, ToolCall

from .contracts import AgentEvent, AgentEventKind, ToolEffect


CONVERGENCE_THRESHOLDS = (20, 40, 80, 120, 160)


def tool_call_fingerprint(call: ToolCall) -> str:
    payload = json.dumps(
        {"tool": call.name, "arguments": call.arguments},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def recent_read_only_repeat_count(
    events: Sequence[AgentEvent],
    *,
    turn_id: str,
    fingerprint: str,
) -> int:
    count = 0
    for event in reversed(events):
        if event.turn_id != turn_id:
            continue
        if event.kind is not AgentEventKind.TOOL_STARTED:
            continue
        effect = str(event.data.get("effect") or "")
        if effect != ToolEffect.READ_ONLY.value:
            break
        if str(event.data.get("call_fingerprint") or "") == fingerprint:
            count += 1
    return count


def model_execution_guidance(
    events: Sequence[AgentEvent],
    *,
    turn_id: str,
    tool_calls: int,
) -> tuple[AIMessage | None, dict[str, object]]:
    turn_events = [event for event in events if event.turn_id == turn_id]
    last_request_index = max(
        (
            index
            for index, event in enumerate(turn_events)
            if event.kind is AgentEventKind.MODEL_REQUESTED
        ),
        default=-1,
    )
    repeats = [
        event
        for event in turn_events[last_request_index + 1 :]
        if event.kind is AgentEventKind.TOOL_STARTED
        and int(event.data.get("repeat_count") or 0) > 0
    ]

    delivered_thresholds = {
        int(event.data.get("convergence_checkpoint") or 0)
        for event in turn_events
        if event.kind is AgentEventKind.MODEL_REQUESTED
    }
    threshold = max(
        (
            value
            for value in CONVERGENCE_THRESHOLDS
            if tool_calls >= value and value not in delivered_thresholds
        ),
        default=0,
    )
    if not repeats and not threshold:
        return None, {}

    parts = ["LOOM_EXECUTION_GUIDANCE v1"]
    metadata: dict[str, object] = {}
    if repeats:
        tools = sorted({str(event.data.get("tool") or "tool") for event in repeats})
        parts.append(
            "A read-only operation was repeated with identical normalized arguments and no intervening "
            "recorded mutation. Treat the newest result as confirmation, avoid repeating it again unless "
            "state may have changed, and use read_durable_tool_result for exact prior evidence. "
            f"Repeated tools: {', '.join(tools)}."
        )
        metadata["duplicate_read_only_calls"] = len(repeats)
    if threshold:
        parts.append(
            f"This turn has reached {threshold} tool calls. Before more tools, explicitly check what is "
            "already established, what remains, and whether the next call will add new evidence. If the "
            "task is solved, answer now; if progress is stalled, change approach or request the missing input."
        )
        metadata["convergence_checkpoint"] = threshold
    return AIMessage(
        role=MessageRole.SYSTEM,
        name="loom_execution_guidance",
        content="\n".join(parts),
    ), metadata


__all__ = [
    "CONVERGENCE_THRESHOLDS",
    "model_execution_guidance",
    "recent_read_only_repeat_count",
    "tool_call_fingerprint",
]
