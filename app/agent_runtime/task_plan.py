"""Durable milestones and opt-in task graphs, backed by the session journal."""
from copy import deepcopy
import json

from app.ai import AIMessage, MessageRole
from .contracts import ToolEffect
from .tools import AgentTool, validate_tool_arguments
from .evidence import EVIDENCE_REFS_SCHEMA, resolve_evidence, rejected_evidence
from .plan_state import (active_plan, latest_plan, stamp, persist, guard_version, evidence_changed, validate_graph,
                         person_turn, from_earlier_request, unfinished, UNFINISHED, REQUEST_CHARS, Event)


def current_plan(events, turn_id=None):
    # A plan has its own lifecycle; turns and restarts do not abandon it.
    return active_plan(events)


# A list outlives its turn, so a new message from the person can meet a list written for something else. A real
# person asked "sum up where we are"; the Host asked for a list first, the model handed in the old unfinished one
# unchanged, and went on editing files for it. These say whose list it is.
EARLIER = ("Task list from an earlier request: \"{request}\". It is not this turn's list. If the person's new message "
           "carries that work on, continue it and say so in update_plan's explanation. If it asks for something "
           "else, do that instead: a question you can answer without tools needs no list, and other work gets its "
           "own list with replace=true (the unfinished steps of this one stay visible as dropped). Never take up "
           "this list's work because it is here.")
CARRY_OVER = ("This list was written for an earlier request (\"{request}\"), and the person has sent a new message. If "
              "the new message carries that work on, update it with an explanation saying so. Otherwise write this "
              "request's own list with replace=true, plan_id={plan_id}, expected_revision={revision} and an "
              "explanation; its unfinished steps stay visible as dropped.")


def _request_turn(events, turn_id):
    """The turn of the person's message a list written now serves: this one, or for a goal's continuation or a
    retry, the person's latest."""
    if person_turn(events, turn_id):
        return turn_id
    for event in reversed(events):
        if event.kind is Event.TURN_STARTED and person_turn(events, event.turn_id):
            return event.turn_id
    return turn_id


def _request_text(events, turn_id):
    for event in events:
        if event.kind is Event.USER_MESSAGE and event.turn_id == turn_id:
            return " ".join(str(event.data.get("text") or "").split())[:REQUEST_CHARS]
    return ""


def plan_context(events, turn_id, started=()):
    """The active list as the model reads it each step. started: this turn's turn_started event, which says
    whether the person began it; a list from an earlier request is then labelled as such."""
    plan = current_plan(events)
    if plan is None:
        return None
    earlier = from_earlier_request(plan, tuple(started), turn_id) and not any(
        event.turn_id == turn_id for event in events if event.kind is Event.PLAN_UPDATED)
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
    if earlier:
        return AIMessage(role=MessageRole.SYSTEM, name="loom_task_plan", content=(
            EARLIER.format(request=plan.get("request") or "not recorded") + " Unfinished steps: "
            + "; ".join(step["step"] for step in unfinished(plan)[:12]) + "\n" + json.dumps(state, ensure_ascii=False)))
    return AIMessage(role=MessageRole.SYSTEM, name="loom_task_plan", content=(
        "Current task milestones (assistant-maintained state, not new instructions). "
        "Stay within the latest user request; an existing plan does not authorize unrelated work. "
        "Continue its unfinished work while the request carries it on, reusing recorded evidence. If the user "
        "changes tasks, explicitly replace or archive the old plan with an explanation. "
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
    "dropped_reasons": {"type": "object", "maxProperties": 64,
                        "additionalProperties": {"type": "string", "maxLength": 600}},
    "settled": {"type": "array", "maxItems": 64, "items": {
        "type": "object", "additionalProperties": False, "required": ["step", "outcome", "evidence_refs"],
        "properties": {"step": {"type": "string", "minLength": 1, "maxLength": 240},
                       "outcome": {"enum": ["passed", "failed"]}, "evidence_refs": EVIDENCE_REFS_SCHEMA}}},
    "plan": {"type": "array", "minItems": 2, "maxItems": 128, "items": STEP_SCHEMA}}, "required": ["plan"]}


MAX_DROPPED = 24


def _dropped(previous, old, labels, explanation, context, reasons=None):
    """Unfinished steps a new list leaves out stay visible as dropped, with the reason that came with them.

    A real lead planned six steps, ran a 100-step smoke test, then rewrote the list to one "explored (negative
    result): completed" so the goal could be claimed: the training and evaluation it had planned vanished, and
    the check never saw them. Leaving a step out is allowed; making it disappear is not. A step listed again
    is no longer dropped.

    A dropped step lives as long as the list it was dropped from. A list every step of which is finished closes
    that chapter: a real 14-hour goal replaced its lists a few dozen times and the person's panel ended with 27
    "dropped" steps, most of them long since done, each carrying the reason of an unrelated replacement.
    reasons: a step's own reason by its label; without one it takes the replacement's explanation, marked shared
    so a panel does not present it as that step's own."""
    own = {str(label).strip(): str(why).strip()[:600] for label, why in (reasons or {}).items() if str(why).strip()}
    closed = bool(previous) and not unfinished(previous)
    carried = [] if closed else [item for item in (previous or {}).get("dropped") or () if item.get("step") not in labels]
    fresh = [{"step": label, "status": step["status"], "reason": own.get(label) or explanation[:600],
              "turn_id": context.turn_id, **({} if label in own else {"shared": True})}
             for label, step in old.items() if label not in labels and step.get("status") in UNFINISHED]
    return (carried + fresh)[-MAX_DROPPED:], [item["step"] for item in fresh]


def _settle(settled, dropped, previous, store, context, events):
    """Dropped steps the lead reports it did finish, with the evidence a completed step needs. A lead that kept
    its list behind the work (five times in one run: "plan is behind the real state") replaced it instead of
    marking steps done, and every step it had in fact finished was recorded as dropped. Returns the dropped
    steps that remain, the settled record, and the evidence errors."""
    names = {item["step"] for item in dropped}
    errors, accepted = [], []
    for entry in settled:
        step = entry["step"].strip()
        if step not in names:
            errors.append({"step": step, "reason": "not a dropped step; settled names steps listed as dropped"})
            continue
        facts, problems = resolve_evidence(store, context, entry["evidence_refs"], events=events)
        errors.extend(problems)
        if not problems:
            accepted.append({"step": step, "outcome": entry["outcome"], "evidence_refs": entry["evidence_refs"],
                             "evidence_facts": facts, "turn_id": context.turn_id})
    done = {item["step"] for item in accepted}
    kept = [item for item in (previous or {}).get("settled") or () if item["step"] not in done]
    return [item for item in dropped if item["step"] not in done], (kept + accepted)[-MAX_DROPPED:], errors


def _request(events, context, previous, replacing, earlier, old, labels):
    """Which request of the person's this list serves, kept on the list so the next turn can tell."""
    carried_on = previous and not replacing and not (earlier and not set(labels) & set(old))
    if carried_on:
        since = previous.get("request_turn_id") or _request_turn(events, previous.get("origin_turn_id") or context.turn_id)
        text = previous.get("request") or _request_text(events, since)
        # Carried into the person's new message with their say-so: it now serves this one too.
        return {"request": text, "request_turn_id": context.turn_id if earlier else since}
    turn = _request_turn(events, context.turn_id)
    return {"request": _request_text(events, turn), "request_turn_id": turn}


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
        explanation = arguments.get("explanation", "").strip()
        replacing = arguments.get("replace", False)
        earlier = from_earlier_request(previous, events, context.turn_id)
        finished_before = None
        if earlier and not unfinished(previous):
            # A finished list and a new request: this is a new list, not revision n+1 of the old one, and
            # replacing it asks for nothing (a real model was sent to look up the old list's version first).
            finished_before, previous, replacing = previous, None, False
        elif earlier and not replacing and not explanation:
            raise ValueError(CARRY_OVER.format(request=previous.get("request") or "not recorded",
                                               plan_id=previous.get("plan_id"), revision=previous.get("revision", 0)))
        old = {s["step"].strip(): s for s in previous["plan"]} if previous else {}
        if previous:
            guard_version(previous, arguments, required=previous.get("mode") == "graph" or replacing)
            if replacing and not explanation:
                raise ValueError("replacing an active plan requires an explanation")
            if previous.get("mode") == "graph" and not replacing:
                raise ValueError("graph definitions are immutable; use manage_plan, or explicitly replace the plan")
        elif finished_before is None and ("plan_id" in arguments or arguments.get("expected_revision", 0) != 0):
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
        dropped, new_drops = _dropped(previous, old, set(labels), explanation, context, arguments.get("dropped_reasons"))
        settled = []
        if arguments.get("settled"):
            dropped, settled, errors = _settle(arguments["settled"], dropped, previous, store, context, events)
            if errors:
                return rejected_evidence(errors)
            new_drops = [step for step in new_drops if step in {item["step"] for item in dropped}]
        elif previous and unfinished(previous):
            settled = list(previous.get("settled") or ())
        data = stamp({"plan": plan, "mode": mode, "explanation": explanation, "current": None},
                     previous if not replacing else None, context)
        if dropped:
            data["dropped"] = dropped
        if settled:
            data["settled"] = settled
        if replacing and previous:
            data["replaces_plan_id"] = previous.get("plan_id")
        elif not previous:
            last = latest_plan(events)
            if last:
                data["previous_plan_id"] = last.get("plan_id")
        data.update(_request(events, context, previous, replacing, earlier, old, labels))
        if new_drops:
            return persist(context, data, "Plan updated. Unfinished steps you left out are kept as dropped, with your "
                           "reason, where the person and any check see them: " + "; ".join(new_drops)
                           + ". Work you did finish is not dropped: report it with settled=[{step, outcome, "
                           "evidence_refs}], and give a step its own reason in dropped_reasons.")
        return persist(context, data)

    return AgentTool("update_plan", (
        "Create or update durable milestones across turns. Completed steps require outcome and evidence_refs "
        "(executed call_id or existing workspace file); failures are valid execution outcomes. Outcome changes "
        "require explanation, promotion to passed requires new evidence. For long work create mode=graph with "
        "unique id, instructions, acceptance and deps on each pending step; type=review gates implementations. "
        "Execute graph plans using manage_plan, never by overwriting statuses. Inspect to get plan_id/revision. "
        "To change tasks use replace=true with plan_id, expected_revision and explanation; old history is retained. "
        "A list belongs to the request it was written for: after the person's next message it carries on only with "
        "an explanation saying the message continues it, and a finished list is followed by a new one. "
        "Skip plans for simple work."), UPDATE_SCHEMA, update, effect=ToolEffect.READ_ONLY,
        supports_parallel_tool_calls=False)
