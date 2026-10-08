"""Atomic tool-call validation shared by streamed and ordinary responses."""
from __future__ import annotations

import json
from .contracts import ToolCall
from .errors import AIResponseError, AITruncatedToolCallError


def parse_tool_call(call_id: str, name: str, raw_arguments: str, *, finish_reason: str = "") -> ToolCall:
    if finish_reason in {"length", "max_tokens"}:
        raise AITruncatedToolCallError(finish_reason=finish_reason, tool_name=name,
                                      argument_chars=len(raw_arguments))
    if not str(call_id or "").strip() or not str(name or "").strip():
        raise AIResponseError("tool call is missing id or function name")
    try:
        arguments = json.loads(raw_arguments) if raw_arguments.strip() else {}
    except json.JSONDecodeError as exc:
        raise AIResponseError(f"tool call {name!r} returned invalid JSON arguments",
                              finish_reason=finish_reason) from exc
    if not isinstance(arguments, dict):
        raise AIResponseError(f"tool call {name!r} arguments must be a JSON object")
    return ToolCall(call_id=call_id, name=name, arguments=arguments)


def validate_unique_calls(calls: list[ToolCall]) -> tuple[ToolCall, ...]:
    if len({call.call_id for call in calls}) != len(calls):
        raise AIResponseError("response reused a tool call ID")
    return tuple(calls)
