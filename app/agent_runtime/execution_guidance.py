from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence

from app.ai import AIMessage, MessageRole, ToolCall

from .contracts import AgentEvent, AgentEventKind, ToolEffect


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


def recent_tool_repeat_count(
    events: Sequence[AgentEvent],
    *,
    turn_id: str,
    fingerprint: str,
    max_prior_calls: int = 40,
) -> int:
    """Count exact earlier calls without claiming their result is reusable.

    ``exec`` is intentionally classified as sensitive because an arbitrary
    command may mutate external state.  That classification used to make all
    command repetition invisible to convergence guidance, including identical
    SSH status probes.  This bounded count is advisory only: it never skips a
    call or treats an old result as current.
    """

    count = 0
    inspected = 0
    for event in reversed(events):
        if event.turn_id != turn_id or event.kind is not AgentEventKind.TOOL_STARTED:
            continue
        inspected += 1
        if str(event.data.get("call_fingerprint") or "") == fingerprint:
            count += 1
        if inspected >= max(1, int(max_prior_calls)):
            break
    return count


def model_execution_guidance(
    events: Sequence[AgentEvent],
    *,
    turn_id: str,
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
    sensitive_repeats = [
        event
        for event in repeats
        if str(event.data.get("effect") or "") == ToolEffect.SENSITIVE.value
    ]
    read_only_repeats = [
        event
        for event in repeats
        if str(event.data.get("effect") or "") == ToolEffect.READ_ONLY.value
    ]

    # Tool volume is not evidence that a task needs another audit/replan.
    # Only report an observed repetition, once, without interrupting execution.
    if not repeats:
        return None, {}

    parts = ["LOOM_EXECUTION_GUIDANCE v1"]
    metadata: dict[str, object] = {}
    if read_only_repeats:
        tools = sorted({str(event.data.get("tool") or "tool") for event in read_only_repeats})
        parts.append(
            "A read-only operation was repeated with identical normalized arguments and no intervening "
            "recorded mutation. Treat the newest result as confirmation, avoid repeating it again unless "
            "state may have changed, and use read_durable_tool_result for exact prior evidence. "
            f"Repeated tools: {', '.join(tools)}."
        )
        metadata["duplicate_read_only_calls"] = len(read_only_repeats)
    if sensitive_repeats:
        tools = sorted({str(event.data.get("tool") or "tool") for event in sensitive_repeats})
        parts.append(
            "A sensitive operation was requested again with exactly the same normalized arguments. "
            "Do not assume its old result is current or reuse it automatically, but before running it "
            "again decide whether external state could actually have changed. If this is only recovery "
            "after compaction, inspect the durable result first and avoid another full audit. "
            f"Repeated sensitive tools: {', '.join(tools)}."
        )
        metadata["duplicate_sensitive_calls"] = len(sensitive_repeats)
    return AIMessage(
        role=MessageRole.SYSTEM,
        name="loom_execution_guidance",
        content="\n".join(parts),
    ), metadata


__all__ = [
    "model_execution_guidance",
    "recent_read_only_repeat_count",
    "recent_tool_repeat_count",
    "tool_call_fingerprint",
]


def execution_progress_context(events: Sequence[AgentEvent], *, turn_id: str) -> AIMessage | None:
    """Project execution observations, never inferred task or test completion."""
    current = [e for e in events if e.turn_id == turn_id]
    results = [e for e in current if e.kind in {
        AgentEventKind.TOOL_COMPLETED, AgentEventKind.TOOL_FAILED}]
    if not results:
        return None
    narratives = sum(e.kind is AgentEventKind.MODEL_RESPONSE and bool(e.data.get("text"))
                     for e in current)
    payload = {
        "tool_results": len(results),
        "tool_failures": sum(e.kind is AgentEventKind.TOOL_FAILED for e in results),
        "assistant_text_responses": narratives,
        "recent_results": [{"call_id": e.data.get("call_id"), "tool": e.data.get("tool"),
                            "execution_outcome": e.kind.value} for e in results[-4:]],
    }
    return AIMessage(role=MessageRole.SYSTEM, name="loom_execution_progress", content=(
        "Current-turn execution observations (not functional test verdicts): "
        + json.dumps(payload, ensure_ascii=False)
        + "\nContinue useful execution. Routine successful receipts need no prose acknowledgment. "
        "Use update_plan for milestone state; tell the user a new conclusion, blocker, or material "
        "change, rather than replaying rules or promises. Keep exact evidence in deliverables. "
        "Do not infer pass/fail from these counts or stop work because of them."))
