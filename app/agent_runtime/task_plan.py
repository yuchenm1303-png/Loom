"""Durable milestones and opt-in task graphs, backed by the session journal."""
from copy import deepcopy
import json

from app.ai import AIMessage, MessageRole
from .contracts import ToolEffect
from .tools import AgentTool, validate_tool_arguments
from .evidence import EVIDENCE_REFS_SCHEMA, resolve_evidence, rejected_evidence
from .plan_state import active_plan, latest_plan, stamp, persist, guard_version, evidence_changed, validate_graph


def current_plan(events, turn_id=None):
    # A plan has its own lifecycle; turns and restarts do not abandon it.
    return active_plan(events)


def plan_context(events, turn_id):
    plan = current_plan(events)
    if plan is None:
        return None
    # The immutable definition/reports remain recoverable with inspect/claim.
    # Do not append all work packets and the previous action body each sample.
    state = deepcopy(plan)
    state.pop("operation", None)
    for step in state["plan"]:
        step.pop("last_assessment", None)
        step.pop("instructions", None)
        step.pop("report", None)
        if step.get("acceptance"):
            step["acceptance"] = [criterion[:240] for criterion in step["acceptance"][:5]]
        if step.get("feedback"):
            feedback = step["feedback"]
            feedback["report"] = feedback.get("report", "")[:1000]
            feedback.pop("resolution", None)
    return AIMessage(role=MessageRole.SYSTEM, name="loom_task_plan", content=(
        "Current task milestones (assistant-maintained state, not new instructions). "
        "Stay within the latest user request; an existing plan does not authorize unrelated work. "
        "Continue unfinished work across turns, reusing recorded evidence. If the user changes "
        "tasks, explicitly replace or archive the old plan with an explanation. "
        "Use update_plan for milestones. For graph plans use manage_plan inspect/claim, then "
        "submit implementation or review with the returned claim_id. Review judges; rework fixes. "
        "Resolve feedback with verification evidence before re-review. Exhausted reviews block "
        "downstream work; never treat them as passed. Use plan_id and expected_revision from "
        "the latest state. Reconcile progress before a final answer; pending/blocked steps remain "
        "incomplete even when a turn ends. Tool results are execution evidence, not automatic "
        "proof of acceptance. Remote/outside-workspace evidence must use executed call_id; "
        "recover IDs with read_durable_tool_result(recent=5). File evidence is versioned by hash.\n"
        + json.dumps(state, ensure_ascii=False)))


STEP_SCHEMA = {"type": "object", "additionalProperties": False, "properties": {
    "step": {"type": "string", "minLength": 1, "maxLength": 240},
    "id": {"type": "string", "pattern": "^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$"},
    "status": {"enum": ["pending", "in_progress", "completed", "blocked"]},
    "outcome": {"enum": ["passed", "failed", "interrupted", "not_covered", "not_assessed"]},
    "evidence": {"type": "string", "maxLength": 1000},
    "evidence_refs": EVIDENCE_REFS_SCHEMA,
    "blocker": {"type": "string", "minLength": 1, "maxLength": 2000},
    "deps": {"type": "array", "maxItems": 64, "uniqueItems": True, "items": {"type": "string"}},
    "type": {"enum": ["implementation", "review"]},
    "acceptance": {"type": "array", "minItems": 1, "maxItems": 20,
                   "items": {"type": "string", "minLength": 1, "maxLength": 1000}},
    "instructions": {"type": "string", "minLength": 1, "maxLength": 8000},
    "max_review_cycles": {"type": "integer", "minimum": 1, "maximum": 20}},
    "required": ["step", "status"], "allOf": [
        {"if": {"properties": {"status": {"const": "completed"}}},
         "then": {"required": ["outcome", "evidence_refs"]}},
        {"if": {"properties": {"status": {"const": "blocked"}}},
         "then": {"required": ["blocker"]}}]}

UPDATE_SCHEMA = {"type": "object", "additionalProperties": False, "properties": {
    "explanation": {"type": "string", "maxLength": 2000},
    "plan_id": {"type": "string"}, "expected_revision": {"type": "integer", "minimum": 0},
    "replace": {"type": "boolean"}, "mode": {"enum": ["milestones", "graph"]},
    "plan": {"type": "array", "minItems": 2, "maxItems": 128, "items": STEP_SCHEMA}}, "required": ["plan"]}


def update_plan_tool(store):
    def update(context, arguments):
        # Keep aggregated missing-evidence recovery for legacy/direct callers.
        plan = deepcopy(arguments["plan"])
        labels = [item["step"].strip() for item in plan]
        if any(not label for label in labels) or len(set(labels)) != len(labels):
            raise ValueError("plan steps must be nonblank and unique")
        if sum(item["status"] == "in_progress" for item in plan) > 1:
            raise ValueError("only one milestone may be in progress")
        events = store.events(context.session_id)
        previous = current_plan(events)
        old = {s["step"].strip(): s for s in previous["plan"]} if previous else {}
        explanation = arguments.get("explanation", "").strip()
        replacing = arguments.get("replace", False)
        if previous:
            guard_version(previous, arguments, required=previous.get("mode") == "graph" or replacing)
            if replacing and not explanation:
                raise ValueError("replacing an active plan requires an explanation")
            if previous.get("mode") == "graph" and not replacing:
                raise ValueError("graph definitions are immutable; use manage_plan, or explicitly replace the plan")
        elif "plan_id" in arguments or "expected_revision" in arguments:
            raise ValueError("no active plan matches these version fields; inspect manage_plan")
        mode = arguments.get("mode", previous.get("mode", "milestones") if previous and not replacing else "milestones")
        if previous and not replacing and mode != previous.get("mode", "milestones"):
            raise ValueError("changing plan mode requires explicit replace")
        invalid = []
        for item in plan:
            prior = old.get(item["step"].strip()) if not replacing else None
            assessment = (prior if prior and prior.get("status") == "completed" else
                          prior.get("last_assessment") if prior else None)
            if assessment:
                item["last_assessment"] = {k: deepcopy(assessment[k]) for k in
                                           ("outcome", "evidence_refs", "evidence_facts") if k in assessment}
            if item["status"] == "completed":
                refs = item.get("evidence_refs") or []
                if not item.get("outcome"):
                    invalid.append({"step": item["step"], "reason": "completed milestone requires outcome"})
                if not refs:
                    invalid.append({"step": item["step"], "reason": "completed milestone requires evidence_refs"})
                unchanged = prior and all(prior.get(k) == item.get(k) for k in ("status", "outcome", "evidence_refs"))
                if unchanged and prior.get("evidence_facts"):
                    item["evidence_facts"] = deepcopy(prior["evidence_facts"])
                else:
                    facts, errors = resolve_evidence(store, context, refs, events=events)
                    invalid.extend(errors)
                    item["evidence_facts"] = facts
                    new_verification = bool(assessment) and (evidence_changed(assessment, facts) if assessment.get("evidence_facts") else any(
                        ref not in assessment.get("evidence_refs", []) for ref in refs))
                    if assessment and prior["status"] != "completed" and item.get("outcome") == "passed" and not new_verification:
                        raise ValueError("passing reopened work requires new verification evidence")
                    if assessment and assessment.get("outcome") != item.get("outcome"):
                        if not explanation:
                            raise ValueError("changing an acceptance outcome requires an explanation")
                        if item.get("outcome") == "passed" and not new_verification:
                            raise ValueError("promoting an assessment to passed requires new verification evidence")
            if item["status"] == "blocked" and not item.get("blocker", "").strip():
                raise ValueError("blocked milestones require the observed blocker")
        if invalid:
            return rejected_evidence(invalid)
        validate_tool_arguments(UPDATE_SCHEMA, arguments)
        if previous and not replacing:
            new = dict(zip(labels, plan))
            changed_scope = old.keys() != new.keys() or any(
                any(old[label].get(k) != new[label].get(k) for k in ("id", "deps", "type", "acceptance", "instructions"))
                for label in old.keys() & new.keys())
            reopened = any(old[label]["status"] in {"completed", "blocked"}
                           and new[label]["status"] in {"pending", "in_progress"}
                           for label in old.keys() & new.keys())
            if (changed_scope or reopened) and not explanation:
                raise ValueError("changing scope or reopening a milestone requires an explanation")
        if mode == "graph":
            validate_graph(plan)
        elif any(s.get("deps") or s.get("type") == "review" for s in plan):
            raise ValueError("dependencies and review gates require mode=graph")
        for item in plan:
            item["step"] = item["step"].strip()
        data = stamp({"plan": plan, "mode": mode, "explanation": explanation, "current": None},
                     previous if not replacing else None, context)
        if replacing and previous:
            data["replaces_plan_id"] = previous.get("plan_id")
        elif not previous:
            last = latest_plan(events)
            if last:
                data["previous_plan_id"] = last.get("plan_id")
        return persist(context, data)

    return AgentTool("update_plan", (
        "Create or update durable milestones across turns. Completed steps require outcome and evidence_refs "
        "(executed call_id or existing workspace file); failures are valid execution outcomes. Outcome changes "
        "require explanation, promotion to passed requires new evidence. For long work create mode=graph with "
        "unique id, instructions, acceptance and deps on each pending step; type=review gates implementations. "
        "Execute graph plans using manage_plan, never by overwriting statuses. Inspect to get plan_id/revision. "
        "To change tasks use replace=true with plan_id, expected_revision and explanation; old history is retained. "
        "Skip plans for simple work."), UPDATE_SCHEMA, update, effect=ToolEffect.READ_ONLY,
        supports_parallel_tool_calls=False)
