from __future__ import annotations

from typing import Any

from mcp.server import MCPServer
from mcp_types import CallToolResult, TextContent, ToolAnnotations

from app.remote_control import (
    RemoteControlClient,
    RemoteControlError,
    approval_fingerprint,
)

from .ui import THREAD_CONTROL_HTML, THREAD_CONTROL_URI


_READ_ONLY = ToolAnnotations(
    read_only_hint=True,
    destructive_hint=False,
    idempotent_hint=True,
    open_world_hint=False,
)
_REMOTE_WRITE = ToolAnnotations(
    read_only_hint=False,
    destructive_hint=True,
    idempotent_hint=False,
    open_world_hint=True,
)
_STOP_WRITE = ToolAnnotations(
    read_only_hint=False,
    destructive_hint=True,
    idempotent_hint=True,
    open_world_hint=False,
)
_APPROVAL_WRITE = ToolAnnotations(
    read_only_hint=False,
    destructive_hint=True,
    idempotent_hint=True,
    open_world_hint=True,
)


def _safe_call(action, *args, **kwargs) -> dict[str, Any]:
    try:
        result = action(*args, **kwargs)
    except RemoteControlError as exc:
        return exc.as_result()
    if not isinstance(result, dict):
        return {
            "ok": False,
            "error": {
                "code": "invalid_response",
                "message": "Loom remote-control action returned a non-object response",
                "data": None,
            },
        }
    return result


def build_mcp_server(remote: RemoteControlClient) -> MCPServer:
    """Build the MCP surface without importing or bypassing AgentRuntime."""

    mcp = MCPServer(
        "Loom",
        instructions=(
            "Use Loom to inspect and control the user's local Loom agent. "
            "Loom remains authoritative for permissions, approvals, sandboxing, "
            "and execution. Never imply a Loom task succeeded until Loom reports it."
        ),
    )

    @mcp.resource(
        THREAD_CONTROL_URI,
        name="loom_thread_control",
        title="Loom task",
        mime_type="text/html;profile=mcp-app",
    )
    def loom_thread_control_ui() -> str:
        """Render Loom thread status and user-controlled approval actions."""

        return THREAD_CONTROL_HTML

    @mcp.tool(
        name="loom_status",
        title="Check Loom status",
        annotations=_READ_ONLY,
    )
    def loom_status() -> dict[str, Any]:
        """Check whether the local Loom runtime is online and report its capabilities."""

        return _safe_call(remote.status)

    @mcp.tool(
        name="loom_projects_list",
        title="List Loom projects",
        annotations=_READ_ONLY,
    )
    def loom_projects_list() -> dict[str, Any]:
        """List projects registered in the local Loom App Server."""

        return _safe_call(remote.projects_list)

    @mcp.tool(
        name="loom_threads_list",
        title="List Loom threads",
        annotations=_READ_ONLY,
    )
    def loom_threads_list(limit: int = 50) -> dict[str, Any]:
        """List recent Loom threads. Use this before continuing existing work."""

        return _safe_call(remote.threads_list, limit=limit)

    @mcp.tool(
        name="loom_thread_read",
        title="Read Loom thread",
        annotations=_READ_ONLY,
        meta={
            "ui": {"resourceUri": THREAD_CONTROL_URI},
            "openai/outputTemplate": THREAD_CONTROL_URI,
        },
        structured_output=False,
    )
    def loom_thread_read(thread_id: str) -> CallToolResult:
        """Read authoritative durable state for one Loom thread.

        Approval fingerprint data is intentionally kept in MCP result metadata so
        the app UI can bind its buttons to the displayed request without adding
        that anti-stale token to the model-visible structured content.
        """

        result = _safe_call(remote.thread_read, thread_id)
        private_meta: dict[str, Any] = {}
        pending = result.get("pendingApproval")
        if result.get("ok") is True and isinstance(pending, dict):
            try:
                private_meta["loom/approvalFingerprint"] = approval_fingerprint(
                    thread_id,
                    pending,
                )
            except ValueError:
                pass

        thread = result.get("thread")
        if result.get("ok") is True and isinstance(thread, dict):
            status = str(thread.get("status") or "unknown").replace("_", " ")
            summary = f"Loom thread {thread_id} is {status}."
            if isinstance(pending, dict):
                summary += " Loom is waiting for the user's approval."
        else:
            error = result.get("error")
            message = (
                str(error.get("message") or "unknown error")
                if isinstance(error, dict)
                else "unknown error"
            )
            summary = f"Could not read Loom thread {thread_id}: {message}"

        return CallToolResult(
            content=(TextContent(type="text", text=summary),),
            structured_content=result,
            _meta=private_meta or None,
        )

    @mcp.tool(
        name="loom_task_start",
        title="Start Loom task",
        annotations=_REMOTE_WRITE,
    )
    def loom_task_start(
        prompt: str,
        project_id: str = "",
        thread_id: str = "",
        idempotency_key: str = "",
    ) -> dict[str, Any]:
        """Start a task in a project or continue an existing Loom thread.

        Pass exactly one of project_id or thread_id. The remote channel chooses
        no permission mode; Loom creates new remote threads under its configured
        remote-control ceiling.
        """

        return _safe_call(
            remote.task_start,
            prompt=prompt,
            project_id=project_id,
            thread_id=thread_id,
            idempotency_key=idempotency_key,
        )

    @mcp.tool(
        name="loom_task_steer",
        title="Steer Loom task",
        annotations=_REMOTE_WRITE,
    )
    def loom_task_steer(
        thread_id: str,
        turn_id: str,
        input_text: str,
        idempotency_key: str = "",
    ) -> dict[str, Any]:
        """Give new instructions to the currently running Loom turn."""

        return _safe_call(
            remote.task_steer,
            thread_id=thread_id,
            turn_id=turn_id,
            input_text=input_text,
            idempotency_key=idempotency_key,
        )

    @mcp.tool(
        name="loom_task_stop",
        title="Stop Loom task",
        annotations=_STOP_WRITE,
    )
    def loom_task_stop(thread_id: str, turn_id: str) -> dict[str, Any]:
        """Interrupt the matching current Loom turn."""

        return _safe_call(remote.task_stop, thread_id=thread_id, turn_id=turn_id)

    @mcp.tool(
        name="loom_approval_respond",
        title="Respond to Loom approval",
        annotations=_APPROVAL_WRITE,
        meta={
            "ui": {"visibility": ["app"]},
            "openai/widgetAccessible": True,
        },
    )
    def loom_approval_respond(
        thread_id: str,
        fingerprint: str,
        decision: str,
    ) -> dict[str, Any]:
        """Respond to the exact approval request displayed by the Loom app UI.

        This tool is app-only: the model must not approve its own requested
        authority. The fingerprint makes stale approval cards fail closed.
        """

        return _safe_call(
            remote.approval_respond,
            thread_id=thread_id,
            fingerprint=fingerprint,
            decision=decision,
        )

    return mcp


__all__ = ["build_mcp_server"]
