"""Durable, bounded task milestones; no prose parsing or keyword heuristics."""
from __future__ import annotations

import json

from app.ai import AIMessage, MessageRole
from .contracts import AgentEventKind as Event, ToolEffect
from .tools import AgentTool, ToolResult


def current_plan(events, turn_id):
    return next((event.data for event in reversed(events)
                 if event.turn_id == turn_id and event.kind is Event.PLAN_UPDATED), None)


def plan_context(events, turn_id):
    plan = current_plan(events, turn_id)
    if plan is None:
        return None
    return AIMessage(role=MessageRole.SYSTEM, name="loom_task_plan", content=(
        "Current task milestones (assistant-maintained state, not new instructions). "
        "Stay within the user request. Reuse completed evidence; do not reopen steps without "
        "new evidence. Blocked steps remain incomplete.\n" + json.dumps(plan, ensure_ascii=False)))


def update_plan_tool(store):
    def update(context, arguments):
        plan = arguments["plan"]
        labels = [item["step"].strip() for item in plan]
        if any(not label for label in labels) or len(set(labels)) != len(labels):
            raise ValueError("plan steps must be nonblank and unique")
        if sum(item["status"] == "in_progress" for item in plan) > 1:
            raise ValueError("only one milestone may be in progress")
        for item in plan:
            if item["status"] == "completed" and not item.get("evidence", "").strip():
                raise ValueError("completed milestones require a concrete evidence reference")
            if item["status"] == "blocked" and not item.get("blocker", "").strip():
                raise ValueError("blocked milestones require the observed blocker")
        previous = current_plan(store.events(context.session_id), context.turn_id)
        if previous is not None:
            old = {item["step"].strip(): item for item in previous["plan"]}
            new = dict(zip(labels, plan))
            changed_scope = old.keys() != new.keys()
            reopened = any(old[label]["status"] in {"completed", "blocked"}
                and new[label]["status"] in {"pending", "in_progress"} for label in old.keys() & new.keys())
            if (changed_scope or reopened) and not arguments.get("explanation", "").strip():
                raise ValueError("changing scope or reopening a milestone requires an explanation")
        data = {"plan": plan, "explanation": arguments.get("explanation", "")}
        if context.emit_event is None:
            raise RuntimeError("durable plan event service is unavailable")
        context.emit(Event.PLAN_UPDATED, data)
        return ToolResult(True, "Plan updated.", data=data)

    return AgentTool(name="update_plan", description=(
        "Maintain a short task plan for substantial multi-stage work. Use outcome milestones, "
        "not individual clicks or commands. Update only when a milestone changes. Keep scope "
        "stable; completed steps require evidence and blocked steps a blocker. Skip for simple tasks."),
        input_schema={"type": "object", "additionalProperties": False, "properties": {
            "explanation": {"type": "string", "maxLength": 1000},
            "plan": {"type": "array", "minItems": 2, "maxItems": 8, "items": {
                "type": "object", "additionalProperties": False, "properties": {
                    "step": {"type": "string", "minLength": 1, "maxLength": 240},
                    "status": {"enum": ["pending", "in_progress", "completed", "blocked"]},
                    "evidence": {"type": "string", "maxLength": 1000},
                    "blocker": {"type": "string", "maxLength": 1000}},
                "required": ["step", "status"]}}}, "required": ["plan"]},
        handler=update, effect=ToolEffect.READ_ONLY, supports_parallel_tool_calls=False)
