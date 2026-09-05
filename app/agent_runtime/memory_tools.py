from __future__ import annotations

import json

from .contracts import ToolEffect
from .memory_store import MemoryStore
from .tools import AgentTool, ToolContext, ToolResult


def memory_tools(store: MemoryStore) -> tuple[AgentTool, ...]:
    def search_memory(context: ToolContext, arguments: dict[str, object]) -> ToolResult:
        query = str(arguments.get("query") or "").strip()
        if not query:
            raise ValueError("query must not be empty")
        limit = int(arguments.get("limit") or 8)
        records = store.search(query, workspace=context.workspace, limit=limit)
        data = {"query": query, "memories": [record.to_dict() for record in records]}
        return ToolResult(
            ok=True,
            content=(
                "No relevant long-term memories found."
                if not records
                else json.dumps(data["memories"], ensure_ascii=False, indent=2)
            ),
            data=data,
        )

    def memory_status(context: ToolContext, arguments: dict[str, object]) -> ToolResult:
        _ = arguments
        counts = store.counts(workspace=context.workspace)
        return ToolResult(
            ok=True,
            content=(
                f"Long-term memory: {counts['visible']} visible, "
                f"{counts['total']} total, {counts['pending']} pending consolidation."
            ),
            data=counts,
        )

    return (
        AgentTool(
            name="search_memory",
            description=(
                "Search Loom's consolidated long-term memory for relevant user preferences, facts, "
                "constraints, project decisions, and workspace-specific context. Memory is advisory and may be stale."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "minLength": 1},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 32},
                },
                "required": ["query"],
                "additionalProperties": False,
            },
            handler=search_memory,
            effect=ToolEffect.READ_ONLY,
        ),
        AgentTool(
            name="memory_status",
            description="Report visible, total, and pending long-term memory counts without exposing hidden memory content.",
            input_schema={"type": "object", "properties": {}, "additionalProperties": False},
            handler=memory_status,
            effect=ToolEffect.READ_ONLY,
        ),
    )


__all__ = ["memory_tools"]
