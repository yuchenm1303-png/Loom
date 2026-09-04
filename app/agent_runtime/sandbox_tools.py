from __future__ import annotations

from .contracts import ToolEffect
from .process_runtime import ProcessStore
from .tools import AgentTool, ToolContext, ToolResult


def sandbox_status_tool() -> AgentTool:
    def status(context: ToolContext, _arguments: dict[str, object]) -> ToolResult:
        store = context.service("process_store")
        if not isinstance(store, ProcessStore):
            raise RuntimeError("process runtime service is unavailable")
        snapshot = store.sandbox_snapshot(
            permission_mode=context.permission_mode,
            workspace=context.workspace,
        )
        if snapshot.enforced:
            content = (
                f"OS sandbox enforced with {snapshot.backend.value} "
                f"in {snapshot.mode.value} mode."
            )
        elif snapshot.mode.value == "disabled":
            content = f"OS sandbox disabled. {snapshot.reason}"
        else:
            content = f"OS sandbox is not enforced. {snapshot.reason}"
        return ToolResult(ok=True, content=content, data=snapshot.to_dict())

    return AgentTool(
        name="get_sandbox_status",
        description=(
            "Report whether Loom can enforce an OS-level sandbox for commands in the current session, "
            "including backend, policy, effective mode, and fallback reason."
        ),
        input_schema={"type": "object", "properties": {}, "additionalProperties": False},
        handler=status,
        effect=ToolEffect.READ_ONLY,
    )


__all__ = ["sandbox_status_tool"]
