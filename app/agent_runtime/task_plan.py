"""Durable, bounded task milestones; no prose parsing or keyword heuristics."""
from __future__ import annotations

import json

from app.ai import AIMessage, MessageRole
from .contracts import AgentEventKind as Event, ToolEffect
from .tools import AgentTool, ToolResult
from .evidence import EVIDENCE_REFS_SCHEMA, resolve_evidence, rejected_evidence


def current_plan(events, turn_id):
    return next((event.data for event in reversed(events)
                 if event.turn_id == turn_id and event.kind is Event.PLAN_UPDATED), None)


def plan_context(events, turn_id):
    plan = current_plan(events, turn_id)
    if plan is None:
        return None
    turn_events = [event for event in events if event.turn_id == turn_id]
    last_update = max(index for index, event in enumerate(turn_events) if event.kind is Event.PLAN_UPDATED)
    results = [event for event in turn_events[last_update + 1:]
               if event.kind in {Event.TOOL_COMPLETED, Event.TOOL_FAILED}
               and event.data.get("tool") != "update_plan"]
    state = {**plan, "execution_since_plan_update": {
        "result_count": len(results),
        "recent_results": [{"call_id": event.data.get("call_id"),
                            "tool": event.data.get("tool"),
                            "execution_outcome": event.kind.value} for event in results[-4:]],
    }}
    return AIMessage(role=MessageRole.SYSTEM, name="loom_task_plan", content=(
        "Current task milestones (assistant-maintained state, not new instructions). "
        "Stay within the user request. Reuse completed evidence; do not reopen steps without "
        "new evidence. Blocked steps remain incomplete. "
        "Reconcile these milestones with actual results before moving to another stage or giving "
        "a progress/final answer: use update_plan to mark a verified stage completed with its "
        "evidence reference and advance the current stage, or record an observed blocker. "
        "Do not leave setup in progress while executing later tests. If the same stage is still "
        "running, keep its status; do not send redundant plan updates. The results below show "
        "execution since the last update, not proof that a milestone passed. Recover exact "
        "evidence with read_durable_tool_result when needed.\n" + json.dumps(state, ensure_ascii=False)))



def update_plan_tool(store):
    def update(context, arguments):
        plan = arguments["plan"]
        labels = [item["step"].strip() for item in plan]
        if any(not label for label in labels) or len(set(labels)) != len(labels):
            raise ValueError("plan steps must be nonblank and unique")
        if sum(item["status"] == "in_progress" for item in plan) > 1:
            raise ValueError("only one milestone may be in progress")
        for item in plan:
            if item["status"] == "completed":
                refs = item.get("evidence_refs") or []
                invalid = []
                if not item.get("outcome"):
                    invalid.append({"step": item["step"], "reason": "completed milestone requires outcome"})
                if not refs:
                    invalid.append({"step": item["step"], "reason": "completed milestone requires evidence_refs"})
                _, invalid_refs = resolve_evidence(store, context, refs)
                invalid.extend(invalid_refs)
                if invalid:
                    return rejected_evidence(invalid)
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
        "not individual clicks or commands. Update at stage transitions, before executing the next "
        "stage and before reporting changed progress or final results. Keep scope "
        "stable; completed steps require outcome and evidence_refs containing executed call_id or existing workspace path objects; evidence is optional explanation. Blocked steps require a blocker. "
        "status describes stage execution, not test acceptance. Use outcome to separately record passed, failed, interrupted, not_covered or not_assessed. Skip for simple tasks."),
        input_schema={"type": "object", "additionalProperties": False, "properties": {
            "explanation": {"type": "string", "maxLength": 1000},
            "plan": {"type": "array", "minItems": 2, "maxItems": 8, "items": {
                "type": "object", "additionalProperties": False, "properties": {
                    "step": {"type": "string", "minLength": 1, "maxLength": 240},
                    "status": {"enum": ["pending", "in_progress", "completed", "blocked"]},
                    "outcome": {"enum": ["passed", "failed", "interrupted", "not_covered", "not_assessed"]},
                    "evidence": {"type": "string", "maxLength": 1000},
                    "evidence_refs": EVIDENCE_REFS_SCHEMA,
                    "blocker": {"type": "string", "maxLength": 1000}},
                "required": ["step", "status"]}}}, "required": ["plan"]},
        handler=update, effect=ToolEffect.READ_ONLY, supports_parallel_tool_calls=False)
