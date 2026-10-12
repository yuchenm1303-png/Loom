"""Atomic tool-call validation shared by streamed and ordinary responses."""
from __future__ import annotations

import json
from types import SimpleNamespace
from .contracts import ToolCall
from .errors import AIResponseError, AITruncatedToolCallError


def chat_tool_fragments(message, *, legacy_id: str):
    """Normalize modern and legacy Chat Completions fields by wire shape."""
    calls = tuple(getattr(message, "tool_calls", None) or ())
    legacy = getattr(message, "function_call", None)
    if calls and legacy is not None:
        raise AIResponseError("response mixes modern and legacy tool fields")
    if legacy is not None:
        return (SimpleNamespace(index=0, id=legacy_id, function=legacy),)
    return calls


def parse_tool_call(call_id: str, name: str, raw_arguments: str, *, finish_reason: str = "") -> ToolCall:
    if finish_reason in {"length", "max_tokens"}:
        raise AITruncatedToolCallError(finish_reason=finish_reason, tool_name=name,
                                      argument_chars=len(raw_arguments))
    if not str(call_id or "").strip() or not str(name or "").strip():
        raise AIResponseError("tool call is missing id or function name")
    try:
        arguments = json.loads(raw_arguments) if raw_arguments.strip() else {}
    except json.JSONDecodeError as exc:
        error = AIResponseError(f"tool call {name!r} returned invalid JSON arguments",
                                finish_reason=finish_reason)
        error.argument_chars = len(raw_arguments)
        error.tool_argument_diagnostics = {
            "schema_version": 1,
            "argument_chars": len(raw_arguments),
            "json_error_position": exc.pos,
            "json_error_line": exc.lineno,
            "json_error_column": exc.colno,
            "json_error_at_end": exc.pos == len(raw_arguments),
            # JSONDecodeError.msg is parser-authored; never include .doc or str(exc).
            "json_error_reason": exc.msg,
        }
        raise error from exc
    if not isinstance(arguments, dict):
        raise AIResponseError(f"tool call {name!r} arguments must be a JSON object")
    return ToolCall(call_id=call_id, name=name, arguments=arguments)


def validate_unique_calls(calls: list[ToolCall]) -> tuple[ToolCall, ...]:
    if len({call.call_id for call in calls}) != len(calls):
        raise AIResponseError("response reused a tool call ID")
    return tuple(calls)
