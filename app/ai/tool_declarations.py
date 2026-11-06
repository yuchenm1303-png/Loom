"""Vendor envelopes for one ToolDefinition.

Every protocol receives the same wire name, description and JSON Schema; only the
envelope differs. Keeping the envelopes in one module lets the cross-vendor
contract be tested in one place.

Providers accept less JSON Schema at the top level of a tool than the validator Loom runs
on arguments does: OpenAI's strict backends answer 400 "schema must have type 'object'
and not have 'oneOf'/'anyOf'/'allOf'/'enum'/'const'/'not' at the top level", and that one
tool then fails every request of the conversation, whatever the person sent. So a rule
at the root stays on Loom's side: the wire schema omits it, and orchestrator validation
of the full ``input_schema`` still answers a call that breaks it with a recoverable
tool error. Nested combinators are valid on the wire and are left alone.
"""
from __future__ import annotations

from typing import Any

from .contracts import ToolDefinition


_LOCAL_ONLY_AT_ROOT = ("oneOf", "anyOf", "allOf", "not", "enum", "const", "if", "then", "else")


def wire_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """``schema`` as providers receive it; the same object when nothing has to go (MCP schemas too)."""
    if not any(key in schema for key in _LOCAL_ONLY_AT_ROOT):
        return schema
    return {key: value for key, value in schema.items() if key not in _LOCAL_ONLY_AT_ROOT}


def chat_completions_tool(tool: ToolDefinition) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description,
            "parameters": wire_schema(tool.input_schema),
        },
    }


def responses_tool(tool: ToolDefinition) -> dict[str, Any]:
    return {
        "type": "function",
        "name": tool.name,
        "description": tool.description,
        "parameters": wire_schema(tool.input_schema),
    }


def messages_tool(tool: ToolDefinition) -> dict[str, Any]:
    return {
        "name": tool.name,
        "description": tool.description,
        "input_schema": wire_schema(tool.input_schema),
    }


__all__ = ["chat_completions_tool", "messages_tool", "responses_tool", "wire_schema"]
