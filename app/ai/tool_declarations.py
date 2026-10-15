"""Vendor envelopes for one ToolDefinition.

Every protocol receives the same wire name, description and JSON Schema; only the
envelope differs. Keeping the envelopes in one module lets the cross-vendor
contract be tested in one place.
"""
from __future__ import annotations

from typing import Any

from .contracts import ToolDefinition


def chat_completions_tool(tool: ToolDefinition) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description,
            "parameters": tool.input_schema,
        },
    }


def responses_tool(tool: ToolDefinition) -> dict[str, Any]:
    return {
        "type": "function",
        "name": tool.name,
        "description": tool.description,
        "parameters": tool.input_schema,
    }


def messages_tool(tool: ToolDefinition) -> dict[str, Any]:
    return {
        "name": tool.name,
        "description": tool.description,
        "input_schema": tool.input_schema,
    }


__all__ = ["chat_completions_tool", "messages_tool", "responses_tool"]
