from __future__ import annotations

import copy
from datetime import datetime, timezone
from typing import Any, Iterable

from app.agent_runtime import AgentEvent, AgentEventKind


APPROVAL_DECISIONS = ("accept", "decline")


def _timestamp_ms(value: str) -> int:
    text = str(value or "").strip()
    if not text:
        return 0
    try:
        normalized = text[:-1] + "+00:00" if text.endswith("Z") else text
        parsed = datetime.fromisoformat(normalized)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return int(parsed.timestamp() * 1000)
    except ValueError:
        return 0


def _structured_value(data: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in data and data[key] is not None:
            return copy.deepcopy(data[key])
    return None


def _approval_stage(data: dict[str, Any]) -> str:
    explicit = str(data.get("approval_stage") or data.get("approvalStage") or "").strip()
    if explicit:
        return explicit
    if _structured_value(data, "network_approval_context", "networkApprovalContext") is not None:
        return "network"
    if str(data.get("retry_reason") or data.get("retryReason") or "").strip():
        return "retry"
    return "initial"


def approval_request_from_event(
    event: AgentEvent,
    *,
    pending: Any | None = None,
) -> dict[str, Any]:
    if event.kind is not AgentEventKind.TOOL_APPROVAL_REQUIRED:
        raise ValueError("approval request requires TOOL_APPROVAL_REQUIRED event")
    data = event.data
    call_id = str(data.get("call_id") or getattr(pending, "call_id", "") or "").strip()
    if not call_id:
        raise ValueError("approval event has no call_id")
    turn_id = str(event.turn_id or "").strip()
    if not turn_id:
        raise ValueError("approval event has no turn_id")

    arguments = data.get("arguments")
    if arguments is None and pending is not None:
        arguments = getattr(pending, "arguments", {})
    tool_name = str(data.get("tool") or getattr(pending, "tool_name", "") or "")
    effect = str(data.get("effect") or "")
    if not effect and pending is not None:
        effect_value = getattr(pending, "effect", "")
        effect = str(getattr(effect_value, "value", effect_value) or "")
    reason = str(data.get("reason") or getattr(pending, "reason", "") or "")
    retry_reason = str(data.get("retry_reason") or data.get("retryReason") or "").strip() or None

    return {
        "requestId": event.event_id,
        "threadId": event.session_id,
        "turnId": turn_id,
        "itemId": f"tool:{call_id}",
        "approvalItemId": f"approval:{call_id}",
        "callId": call_id,
        "requestType": "toolExecution",
        "approvalStage": _approval_stage(data),
        "retryReason": retry_reason,
        "startedAtMs": _timestamp_ms(event.created_at),
        "toolName": tool_name,
        "arguments": copy.deepcopy(arguments or {}),
        "effect": effect,
        "reason": reason,
        "permissionMode": str(data.get("permission_mode") or data.get("permissionMode") or "") or None,
        "networkApprovalContext": _structured_value(
            data,
            "network_approval_context",
            "networkApprovalContext",
        ),
        "availableDecisions": list(APPROVAL_DECISIONS),
    }


def pending_approval_record(
    session: Any,
    events: Iterable[AgentEvent],
) -> dict[str, Any] | None:
    pending = getattr(session, "pending_approval", None)
    if pending is None:
        return None
    call_id = str(getattr(pending, "call_id", "") or "").strip()
    turn_id = str(getattr(session, "current_turn_id", "") or "").strip()
    for event in reversed(tuple(events)):
        if event.kind is not AgentEventKind.TOOL_APPROVAL_REQUIRED:
            continue
        if turn_id and event.turn_id != turn_id:
            continue
        if str(event.data.get("call_id") or "") != call_id:
            continue
        return approval_request_from_event(event, pending=pending)

    effect_value = getattr(pending, "effect", "")
    return {
        "requestId": None,
        "threadId": str(getattr(session, "session_id", "") or ""),
        "turnId": turn_id or None,
        "itemId": f"tool:{call_id}",
        "approvalItemId": f"approval:{call_id}",
        "callId": call_id,
        "requestType": "toolExecution",
        "approvalStage": "initial",
        "retryReason": None,
        "startedAtMs": 0,
        "toolName": str(getattr(pending, "tool_name", "") or ""),
        "arguments": copy.deepcopy(getattr(pending, "arguments", {}) or {}),
        "effect": str(getattr(effect_value, "value", effect_value) or ""),
        "reason": str(getattr(pending, "reason", "") or ""),
        "permissionMode": None,
        "networkApprovalContext": None,
        "availableDecisions": list(APPROVAL_DECISIONS),
    }


__all__ = [
    "APPROVAL_DECISIONS",
    "approval_request_from_event",
    "pending_approval_record",
]
