from __future__ import annotations

from typing import Any

from .contracts import AgentEventKind, ToolEffect
from .durable_state import DurableThreadStateStore, GoalStatus
from .tools import AgentTool, ToolContext, ToolResult


PAUSE_REFUSED = (
    "Only the person can pause a goal: no message of theirs reached this turn, so this goal turn keeps working. "
    "Do not tell the person the goal is paused."
)


def _person_spoke(plan_store, context: ToolContext) -> bool:
    """Whether the person's own words reached this turn: the message that started it, or one sent while it ran.
    A goal turn the Host started itself has neither, and must not be able to stop its own goal."""
    turn_events = getattr(plan_store, "turn_events", None)
    if plan_store is None or not callable(turn_events):
        return True  # nothing to tell turns apart by; the person's own runtime, not a goal driver
    for event in turn_events(context.session_id, context.turn_id):
        source = str(event.data.get("source") or "")
        if event.kind is AgentEventKind.TURN_STARTED and source != "goal":
            return True
        if event.kind is AgentEventKind.USER_MESSAGE and source == "steering":
            return True
    return False


def durable_thread_tools(store: DurableThreadStateStore, *, plan_store=None, claim_note=None,
                         pause=None, claim_check=None, claim_owner=None) -> tuple[AgentTool, ...]:
    """claim_note(session_id) -> text or None: what marking complete means here, when someone else decides it.
    A real lead was told "Durable goal marked complete." and reported "independently verified" before any check ran.

    pause(session_id) -> text or None: stops the goal the way the person's own stop does, when a driver owns it.
    A real lead was asked "请先暂停一下", answered "已暂停" with no way to pause, and the driver started the next
    goal turn a few seconds later."""
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
        if status not in {GoalStatus.COMPLETE, GoalStatus.BLOCKED, GoalStatus.PAUSED}:
            raise ValueError("model may only mark a durable goal complete, blocked or paused")
        if status is GoalStatus.PAUSED:
            if not _person_spoke(plan_store, context):
                raise ValueError(PAUSE_REFUSED)
            current = store.get_goal(context.session_id)
            if current is None:
                raise ValueError("this conversation has no durable goal to pause")
            note = pause(context.session_id) if pause is not None else None
            if note is None:
                current = store.set_goal_status(context.session_id, status)
                context.emit(AgentEventKind.GOAL_UPDATED, {
                    "objective": current.objective, "status": current.status.value,
                    "token_budget": current.token_budget, "tokens_used": current.tokens_used, "source": "model"})
            return ToolResult(ok=True, content=note or "Durable goal paused; nothing runs until the person resumes it.",
                              data={"objective": current.objective, "status": GoalStatus.PAUSED.value})
        # A goal judged by an independent check is not held by its own task list: the list blocked real claims for
        # unpassed step evidence while the check, which reads the open steps, decides what done means.
        owned = status is GoalStatus.COMPLETE and claim_owner is not None and claim_owner(context.session_id)
        if status is GoalStatus.COMPLETE and plan_store is not None and not owned:
            from .plan_state import active_plan, summary
            plan = active_plan(plan_store.events(context.session_id))
            if plan and summary(plan)["status"] != "passed":
                raise ValueError("active plan has incomplete or unpassed acceptance; reconcile it before completing the goal")
        if status is GoalStatus.COMPLETE and claim_check is not None:
            refusal = claim_check(context.session_id)
            if refusal:
                raise ValueError(refusal)
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
        note = claim_note(context.session_id) if claim_note is not None and status is GoalStatus.COMPLETE else None
        return ToolResult(
            ok=True,
            content=note or f"Durable goal marked {goal.status.value}.",
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
                "Mark the current durable Loom goal complete, blocked or paused. Use complete only when the goal is "
                "actually finished; use blocked only when further progress requires an external dependency; use "
                "paused when the person asks to pause or stop the goal, in the turn where they ask. A goal keeps "
                "running after your turn ends until this succeeds, so never tell the person it is paused, stopped "
                "or finished unless this call returned ok."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "status": {"type": "string", "enum": ["complete", "blocked", "paused"]},
                },
                "required": ["status"],
                "additionalProperties": False,
            },
            handler=mark_goal,
            effect=ToolEffect.READ_ONLY,
        ),
    )


__all__ = ["durable_thread_tools"]
