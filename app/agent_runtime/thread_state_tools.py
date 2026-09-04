from __future__ import annotations

from typing import Any

from .contracts import AgentEventKind, ToolEffect
from .durable_state import DurableThreadStateStore, GoalStatus
from .tools import AgentTool, ToolContext, ToolResult


def durable_thread_tools(store: DurableThreadStateStore) -> tuple[AgentTool, ...]:
    def get_state(context: ToolContext, _arguments: dict[str, Any]) -> ToolResult:
        goal = store.get_goal(context.session_id)
        queue = store.list_queue(context.session_id)
        goal_data = None
        if goal is not None:
            goal_data = {
                "objective": goal.objective,
                "status": goal.status.value,
                "token_budget": goal.token_budget,
                "tokens_used": goal.tokens_used,
            }
        queued = [
            {
                "queue_id": item.queue_id,
                "state": item.state.value,
                "text": item.text[:500],
            }
            for item in queue[:20]
        ]
        return ToolResult(
            ok=True,
            content=(
                f"Durable goal: {goal.status.value if goal else 'none'}; "
                f"queued turns: {len(queue)}."
            ),
            data={
                "goal": goal_data,
                "queue": queued,
                "queue_truncated": len(queue) > len(queued),
            },
        )

    def mark_goal(context: ToolContext, arguments: dict[str, Any]) -> ToolResult:
        status = GoalStatus(str(arguments["status"]))
        if status not in {GoalStatus.COMPLETE, GoalStatus.BLOCKED}:
            raise ValueError("model may only mark a durable goal complete or blocked")
        goal = store.set_goal_status(context.session_id, status)
        context.emit(
            AgentEventKind.GOAL_UPDATED,
            {
                "objective": goal.objective,
                "status": goal.status.value,
                "token_budget": goal.token_budget,
                "tokens_used": goal.tokens_used,
                "source": "model",
            },
        )
        return ToolResult(
            ok=True,
            content=f"Durable goal marked {goal.status.value}.",
            data={
                "objective": goal.objective,
                "status": goal.status.value,
                "token_budget": goal.token_budget,
                "tokens_used": goal.tokens_used,
            },
        )

    return (
        AgentTool(
            name="get_thread_state",
            description=(
                "Inspect Loom's durable thread state: the active long-lived goal and future queued turns. "
                "Use this when the current task depends on prior or future thread intent."
            ),
            input_schema={"type": "object", "properties": {}, "additionalProperties": False},
            handler=get_state,
            effect=ToolEffect.READ_ONLY,
        ),
        AgentTool(
            name="mark_thread_goal",
            description=(
                "Mark the current durable Loom goal complete or blocked. Use complete only when the goal is "
                "actually finished; use blocked only when further progress requires an external dependency."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "status": {"type": "string", "enum": ["complete", "blocked"]},
                },
                "required": ["status"],
                "additionalProperties": False,
            },
            handler=mark_goal,
            effect=ToolEffect.READ_ONLY,
        ),
    )


__all__ = ["durable_thread_tools"]
