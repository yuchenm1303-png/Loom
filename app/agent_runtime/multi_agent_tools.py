from __future__ import annotations

from typing import Any

from .agent_control import AgentControl
from .contracts import ToolEffect
from .tools import AgentTool, ToolContext, ToolResult


def _control_result(content: str, data: dict[str, Any]) -> ToolResult:
    return ToolResult(ok=True, content=content, data=data)


def multi_agent_tools(control: AgentControl) -> tuple[AgentTool, ...]:
    """Model-facing tools for the durable sub-agent control plane.

    These operations only manipulate Loom's internal agent/session state. Child
    agents inherit the caller's workspace and permission mode, so spawning never
    expands filesystem/network/process authority by itself; every child tool call
    still crosses the normal Loom permission/sandbox pipeline.
    """

    def spawn_agent(context: ToolContext, args: dict[str, Any]) -> ToolResult:
        snapshot = control.spawn(
            context.session_id,
            str(args["task"]),
            role=str(args.get("role") or "worker"),
            history_mode=str(args.get("history_mode") or "recent"),
            recent_messages=int(args.get("recent_messages") or 16),
            profile_id=(str(args.get("profile_id") or "").strip() or None),
            background=True,
        )
        return _control_result(
            f"Spawned sub-agent {snapshot.node.session_id} ({snapshot.node.role}).",
            snapshot.to_dict(),
        )

    def send_agent_message(context: ToolContext, args: dict[str, Any]) -> ToolResult:
        snapshot = control.send(
            context.session_id,
            str(args["agent_id"]),
            str(args["message"]),
            wake=bool(args.get("wake", True)),
        )
        return _control_result(
            f"Queued a message for sub-agent {snapshot.node.session_id}.",
            snapshot.to_dict(),
        )

    def wait_agent(context: ToolContext, args: dict[str, Any]) -> ToolResult:
        snapshot = control.wait(
            context.session_id,
            str(args["agent_id"]),
            timeout_seconds=float(args.get("timeout_seconds") or 0.0),
        )
        return _control_result(
            f"Sub-agent {snapshot.node.session_id} status: {snapshot.session_status.value}.",
            snapshot.to_dict(),
        )

    def list_agents(context: ToolContext, args: dict[str, Any]) -> ToolResult:
        snapshots = control.list_tree(
            context.session_id,
            include_closed=bool(args.get("include_closed", True)),
        )
        data = {
            "agents": [snapshot.to_dict() for snapshot in snapshots],
            "count": len(snapshots),
        }
        return _control_result(f"Agent tree contains {len(snapshots)} sub-agent(s).", data)

    def close_agent(context: ToolContext, args: dict[str, Any]) -> ToolResult:
        snapshots = control.close_agent(context.session_id, str(args["agent_id"]))
        data = {
            "closed": [snapshot.to_dict() for snapshot in snapshots],
            "count": len(snapshots),
        }
        return _control_result(f"Closed {len(snapshots)} sub-agent(s).", data)

    return (
        AgentTool(
            name="spawn_agent",
            description=(
                "Spawn an independent Loom sub-agent thread for delegated work. The child shares the "
                "caller workspace and permission mode but has independent history, turns, queue, goal, "
                "failures, and model execution. Returns the child agent id immediately."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "task": {"type": "string", "minLength": 1},
                    "role": {"type": "string"},
                    "history_mode": {"type": "string", "enum": ["none", "recent", "all"]},
                    "recent_messages": {"type": "integer", "minimum": 2, "maximum": 80},
                    "profile_id": {"type": "string"},
                },
                "required": ["task"],
                "additionalProperties": False,
            },
            handler=spawn_agent,
            effect=ToolEffect.READ_ONLY,
        ),
        AgentTool(
            name="send_agent_message",
            description=(
                "Send durable follow-up work to a sub-agent in the same agent tree. The message enters "
                "the child's durable queue and can wake an idle child for another turn."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "agent_id": {"type": "string", "minLength": 1},
                    "message": {"type": "string", "minLength": 1},
                    "wake": {"type": "boolean"},
                },
                "required": ["agent_id", "message"],
                "additionalProperties": False,
            },
            handler=send_agent_message,
            effect=ToolEffect.READ_ONLY,
        ),
        AgentTool(
            name="wait_agent",
            description=(
                "Wait briefly for a sub-agent execution and return its durable status, final text, error, "
                "queue depth, and whether an in-process execution is still running."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "agent_id": {"type": "string", "minLength": 1},
                    "timeout_seconds": {"type": "number", "minimum": 0, "maximum": 120},
                },
                "required": ["agent_id"],
                "additionalProperties": False,
            },
            handler=wait_agent,
            effect=ToolEffect.READ_ONLY,
        ),
        AgentTool(
            name="list_agents",
            description="List durable sub-agents in the caller's agent tree and their current execution state.",
            input_schema={
                "type": "object",
                "properties": {"include_closed": {"type": "boolean"}},
                "additionalProperties": False,
            },
            handler=list_agents,
            effect=ToolEffect.READ_ONLY,
        ),
        AgentTool(
            name="close_agent",
            description=(
                "Close a sub-agent and all of its descendants. Active turns are cancelled and managed "
                "processes are terminated before the durable graph nodes are marked closed."
            ),
            input_schema={
                "type": "object",
                "properties": {"agent_id": {"type": "string", "minLength": 1}},
                "required": ["agent_id"],
                "additionalProperties": False,
            },
            handler=close_agent,
            effect=ToolEffect.READ_ONLY,
        ),
    )


__all__ = ["multi_agent_tools"]
