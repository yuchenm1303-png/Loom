"""Atomic tool-call validation shared by streamed and ordinary responses."""
from __future__ import annotations

import json
import math
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


def decode_argument_json(raw: str):
    """Reject ambiguous JSON instead of silently changing a tool's meaning."""
    def object_pairs(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON object property")
            result[key] = value
        return result
    def constant(_value):
        raise ValueError("non-finite JSON number")
    def number(value):
        parsed = float(value)
        if not math.isfinite(parsed):
            raise ValueError("non-finite JSON number")
        return parsed
    return json.loads(raw, object_pairs_hook=object_pairs, parse_constant=constant, parse_float=number)


def parse_tool_call(call_id: str, name: str, raw_arguments: str, *, finish_reason: str = "") -> ToolCall:
    if finish_reason in {"length", "max_tokens"}:
        raise AITruncatedToolCallError(finish_reason=finish_reason, tool_name=name,
                                      argument_chars=len(raw_arguments))
    if not str(call_id or "").strip() or not str(name or "").strip():
        raise AIResponseError("tool call is missing id or function name")
    try:
        arguments = decode_argument_json(raw_arguments) if raw_arguments.strip() else {}
    except json.JSONDecodeError as exc:
        error = AIResponseError(f"tool call {name!r} returned invalid JSON arguments",
                                finish_reason=finish_reason)
        error.tool_name = name
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
    except ValueError as exc:
        error = AIResponseError(f"tool call {name!r} contains ambiguous or non-standard JSON arguments", finish_reason=finish_reason)
        error.tool_name = name
        error.tool_argument_diagnostics = {"schema_version": 1, "json_error_reason": str(exc), "argument_chars": len(raw_arguments)}
        raise error from None
    if not isinstance(arguments, dict):
        error = AIResponseError(f"tool call {name!r} arguments must be a JSON object", finish_reason=finish_reason)
        error.tool_name = name
        error.tool_argument_diagnostics = {"schema_version": 1, "issues": [{"argument_path": "$", "validation_rule": "type", "expected_type": "object"}]}
        raise error
    return ToolCall(call_id=call_id, name=name, arguments=arguments)


def validate_unique_calls(calls: list[ToolCall]) -> tuple[ToolCall, ...]:
    if any(not isinstance(call.call_id, str) or not call.call_id.strip()
           or not isinstance(call.name, str) or not call.name.strip() for call in calls):
        raise AIResponseError("tool call is missing id or function name")
    if len({call.call_id for call in calls}) != len(calls):
        raise AIResponseError("response reused a tool call ID")
    return tuple(calls)
