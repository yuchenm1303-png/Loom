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
        offset = int(arguments.get("offset") or 0)
        max_chars = int(arguments.get("max_chars") or 2000)
        if not call_id and recent <= 0:
            raise ValueError("provide call_id or a positive recent count")
        if recent < 0 or recent > 50:
            raise ValueError("recent must be within 1..50")
        if offset < 0:
            raise ValueError("offset must be non-negative")
        if max_chars < 256 or max_chars > 20_000:
            raise ValueError("max_chars must be within 256..20000")

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
            full_content = str(event.data.get("content") or "")
            start = min(offset, len(full_content))
            end = min(len(full_content), start + max_chars)
            return ToolResult(
                ok=True,
                content=full_content[start:end],
                data={
                    "call_id": call_id,
                    "tool": str(event.data.get("tool") or ""),
                    "result_ok": bool(event.data.get("ok")),
                    "result_data": dict(event.data.get("data") or {}),
                    "created_at": event.created_at,
                    "content_offset": start,
                    "content_chars": end - start,
                    "total_chars": len(full_content),
                    "has_more": end < len(full_content),
                    "next_offset": end if end < len(full_content) else None,
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
            "of recent results. Long results are read in stable chunks using offset/max_chars so exact "
            "evidence can be recovered without rerunning the original tool. Use this after context "
            "reduction or compaction instead of rerunning an unchanged command or file read."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "call_id": {"type": "string"},
                "recent": {"type": "integer", "minimum": 1, "maximum": 50},
                "offset": {"type": "integer", "minimum": 0},
                "max_chars": {"type": "integer", "minimum": 256, "maximum": 20000},
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
