from __future__ import annotations

from typing import Any

from .contracts import ToolEffect
from .tools import AgentTool, ToolContext, ToolRegistry, ToolResult


def workspace_write_tool() -> AgentTool:
    """Validated mutating workspace writer used by Loom's first-party harness."""

    def write_note(context: ToolContext, arguments: dict[str, Any]) -> ToolResult:
        context.raise_if_cancelled()
        relative = str(arguments["path"] or "").strip()
        text = str(arguments["text"] or "")
        if not relative:
            raise ValueError("path must not be empty")
        if len(text) > 64_000:
            raise ValueError("workspace note exceeds 64,000 characters")
        target = context.resolve_workspace_path(relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        context.raise_if_cancelled()
        target.write_text(text, encoding="utf-8")
        return ToolResult(
            ok=True,
            content=f"Wrote workspace note: {relative}",
            data={"path": relative, "chars": len(text)},
        )

    return AgentTool(
        name="write_workspace_note",
        description=(
            "Write a UTF-8 note inside this Agent Session workspace. "
            "This mutating tool always requires explicit user approval."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "text": {"type": "string"},
            },
            "required": ["path", "text"],
            "additionalProperties": False,
        },
        handler=write_note,
        effect=ToolEffect.MUTATING,
    )


def loom_default_tools() -> ToolRegistry:
    from .builtin_tools import builtin_read_only_tools

    registry = builtin_read_only_tools()
    registry.register(workspace_write_tool())
    return registry


__all__ = ["loom_default_tools", "workspace_write_tool"]
