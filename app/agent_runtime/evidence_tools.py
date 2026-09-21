from __future__ import annotations

from typing import Any

from .contracts import AgentEventKind, ToolEffect
from .storage import FileAgentSessionStore
from .tools import AgentTool, ToolContext, ToolResult


_RESULT_KINDS = {AgentEventKind.TOOL_COMPLETED, AgentEventKind.TOOL_FAILED}


def durable_tool_result_tool(store: FileAgentSessionStore) -> AgentTool:
    """Expose already-authorized durable observations without rerunning their tools."""

    def read_result(context: ToolContext, arguments: dict[str, Any]) -> ToolResult:
        call_id = str(arguments.get("call_id") or "").strip()
        recent = int(arguments.get("recent") or 0)
        if not call_id and recent <= 0:
            raise ValueError("provide call_id or a positive recent count")
        if recent < 0 or recent > 50:
            raise ValueError("recent must be within 1..50")

        events = store.events(context.session_id)
        results = [event for event in events if event.kind in _RESULT_KINDS]
        if call_id:
            event = next(
                (
                    item
                    for item in reversed(results)
                    if str(item.data.get("call_id") or "") == call_id
                ),
                None,
            )
            if event is None:
                return ToolResult(ok=False, content=f"No durable tool result found for call_id {call_id!r}.")
            return ToolResult(
                ok=True,
                content=str(event.data.get("content") or ""),
                data={
                    "call_id": call_id,
                    "tool": str(event.data.get("tool") or ""),
                    "result_ok": bool(event.data.get("ok")),
                    "result_data": dict(event.data.get("data") or {}),
                    "created_at": event.created_at,
                },
            )

        selected = results[-recent:]
        return ToolResult(
            ok=True,
            content=f"Found {len(selected)} recent durable tool results.",
            data={
                "results": [
                    {
                        "call_id": str(event.data.get("call_id") or ""),
                        "tool": str(event.data.get("tool") or ""),
                        "ok": bool(event.data.get("ok")),
                        "created_at": event.created_at,
                        "content_preview": str(event.data.get("content") or "")[:500],
                    }
                    for event in selected
                ]
            },
        )

    return AgentTool(
        name="read_durable_tool_result",
        description=(
            "Read a previously recorded tool result from this task by call_id, or list a small number "
            "of recent results. Use this after context reduction or compaction instead of rerunning a "
            "command or rereading an unchanged file merely to recover old evidence."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "call_id": {"type": "string"},
                "recent": {"type": "integer", "minimum": 1, "maximum": 50},
            },
            "additionalProperties": False,
        },
        handler=read_result,
        effect=ToolEffect.READ_ONLY,
    )


def run_scratch_dir_tool(store: FileAgentSessionStore) -> AgentTool:
    def scratch_dir(context: ToolContext, _arguments: dict[str, Any]) -> ToolResult:
        path = store.session_dir(context.session_id) / "run-artifacts" / context.turn_id
        path.mkdir(parents=True, exist_ok=True)
        return ToolResult(
            ok=True,
            content=str(path),
            data={
                "path": str(path),
                "purpose": "temporary diagnostics and intermediate artifacts",
                "outside_workspace": True,
            },
        )

    return AgentTool(
        name="get_run_scratch_dir",
        description=(
            "Return a task-owned directory outside the project workspace for temporary diagnostics, "
            "command output, generated probes, and intermediate files. Prefer it over creating checkpoint, "
            "dump, backup, or scratch files in the user's project."
        ),
        input_schema={"type": "object", "properties": {}, "additionalProperties": False},
        handler=scratch_dir,
        effect=ToolEffect.READ_ONLY,
    )


__all__ = ["durable_tool_result_tool", "run_scratch_dir_tool"]
