from __future__ import annotations

from typing import Any

from .contracts import ToolEffect
from .diff_tracker import TurnDiffTracker
from .patch_runtime import ApplyPatchRuntime
from .tools import AgentTool, ToolContext, ToolResult


def _tracker(context: ToolContext) -> TurnDiffTracker:
    tracker = context.service("diff_tracker")
    if not isinstance(tracker, TurnDiffTracker):
        raise RuntimeError("turn diff tracker service is unavailable")
    return tracker


def apply_patch_tool() -> AgentTool:
    runtime = ApplyPatchRuntime()

    def apply_patch(context: ToolContext, arguments: dict[str, Any]) -> ToolResult:
        context.raise_if_cancelled()
        result = runtime.apply(
            context,
            arguments["changes"],
            diff_tracker=_tracker(context),
        )
        return ToolResult(
            ok=True,
            content=(
                f"Applied patch to {len(result.paths)} file path(s).\n"
                f"{result.diff}"
            ),
            data=result.to_dict(),
        )

    change_schema = {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["add", "update", "delete", "move"]},
            "path": {"type": "string"},
            "content": {"type": "string"},
            "old_text": {"type": "string"},
            "new_text": {"type": "string"},
            "expected_text": {"type": "string"},
            "move_to": {"type": "string"},
        },
        "required": ["action", "path"],
        "additionalProperties": False,
    }
    return AgentTool(
        name="apply_patch",
        description=(
            "Atomically validate and apply a structured multi-file text patch inside the workspace. "
            "Actions: add(path, content), update(path, old_text, new_text) or whole-file content, "
            "delete(path, optional expected_text), and move(path, move_to). All operations validate "
            "before filesystem mutation; failures leave files unchanged. Returns the current turn diff."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "changes": {"type": "array", "items": change_schema},
            },
            "required": ["changes"],
            "additionalProperties": False,
        },
        handler=apply_patch,
        effect=ToolEffect.MUTATING,
    )


def get_turn_diff_tool() -> AgentTool:
    def get_diff(context: ToolContext, _arguments: dict[str, Any]) -> ToolResult:
        snapshot = _tracker(context).snapshot()
        return ToolResult(
            ok=True,
            content=snapshot.diff or "No tracked file changes in the current turn.",
            data=snapshot.to_dict(),
        )

    return AgentTool(
        name="get_turn_diff",
        description="Return Loom's tracked net file diff for the current turn without requiring a Git repository.",
        input_schema={"type": "object", "properties": {}, "additionalProperties": False},
        handler=get_diff,
        effect=ToolEffect.READ_ONLY,
    )


__all__ = ["apply_patch_tool", "get_turn_diff_tool"]
