"""Claim/submit/review/rework protocol over immutable plan event revisions."""
from copy import deepcopy
import uuid

from .contracts import ToolEffect, AgentEventKind as Event
from .evidence import EVIDENCE_REFS_SCHEMA
from .plan_state import (latest_plan, guard_version, persist, stamp,
                         ready_steps, view, evidence, evidence_changed)
from .tools import AgentTool, ToolResult, validate_tool_arguments


MANAGE_SCHEMA = {"type": "object", "additionalProperties": False, "properties": {
    "action": {"enum": ["inspect", "claim", "submit", "review", "resolve", "block", "unblock", "reopen", "archive"]},
    "plan_id": {"type": "string", "minLength": 1},
    "expected_revision": {"type": "integer", "minimum": 1},
    "operation_id": {"type": "string", "minLength": 1, "maxLength": 128},
    "step_id": {"type": "string", "minLength": 1},
    "claim_id": {"type": "string", "minLength": 1},
    "report": {"type": "string", "minLength": 1, "maxLength": 12000},
    "explanation": {"type": "string", "minLength": 1, "maxLength": 2000},
    "outcome": {"enum": ["passed", "failed", "interrupted", "not_covered", "not_assessed"]},
    "verdict": {"enum": ["passed", "needs_changes"]},
    "evidence_refs": EVIDENCE_REFS_SCHEMA}, "required": ["action"]}


def work_packet(plan):
    current = plan.get("current")
    if not current:
        return None
    step = next(s for s in plan["plan"] if s["id"] == current["step_id"])
    return {"kind": current["kind"], "step": deepcopy(step),
            "upstream": [deepcopy(s) for s in plan["plan"] if s["id"] in step.get("deps", [])],
            "instruction": "Judge acceptance and report evidence; do not implement fixes." if current["kind"] == "review"
            else "Address every feedback item and verify the fixes." if current["kind"] == "rework"
            else "Execute the scoped instructions, verify acceptance, then submit an honest report."}


def response(plan, message="Plan state.", **extra):
    data = view(plan)
    data["packet"] = work_packet(plan)
    data.update(extra)
    return ToolResult(True, message, data)


def manage_plan_tool(store):
    def manage(context, args):
        validate_tool_arguments(MANAGE_SCHEMA, args)
        events = store.events(context.session_id)
        latest = latest_plan(events)
        action = args["action"]
        fingerprint = {k: v for k, v in args.items() if k not in {"expected_revision", "operation_id"}}
        if action == "inspect":
            if not latest:
                return ToolResult(True, "No plan has been created.", {"plan": [], "lifecycle": "none"})
            return response(latest)
        if not latest or latest.get("lifecycle", "active") != "active":
            # An archive retry must also be idempotent.
            if latest and args.get("operation_id") == latest.get("operation_id") and action == "archive":
                guard_version(latest, {"plan_id": args.get("plan_id")})
                if latest.get("operation") != fingerprint:
                    raise ValueError("operation_id was already used with different arguments")
                return response(latest, already_applied=True)
            raise ValueError("no active plan; create one with update_plan")
        if not args.get("operation_id", "").strip():
            raise ValueError("mutations require a unique operation_id; reuse it only to retry the same operation")
        # A committed action can outlive its tool result (crash after emit).
        # Recover by operation id before the revision check, returning CURRENT
        # state so a delayed retry cannot rewind the UI.
        for event in reversed(events):
            if event.kind is Event.PLAN_UPDATED and event.data.get("plan_id") == latest.get("plan_id") and event.data.get("operation_id") == args["operation_id"]:
                if event.data.get("operation") != fingerprint:
                    raise ValueError("operation_id was already used with different arguments")
                return response(latest, already_applied=True)
        guard_version(latest, args, required=True)
        plan = deepcopy(latest)
        if action == "archive":
            if not args.get("explanation", "").strip():
                raise ValueError("archiving requires an explanation; incomplete work remains in history")
            plan["revision"] = latest.get("revision", 0) + 1
            plan["lifecycle"] = "archived"
            plan["current"] = None
        else:
            if plan.get("mode") != "graph":
                raise ValueError("milestones use update_plan; claim/review actions require mode=graph")
            by_id = {s["id"]: s for s in plan["plan"]}
            current = plan.get("current")
            step = by_id.get(args.get("step_id"))
            if args.get("step_id") and step is None:
                raise ValueError("unknown step_id")
            if action == "claim":
                if current:
                    if step and current["step_id"] != step["id"]:
                        raise ValueError("another work item is claimed; finish or block it first")
                    return response(latest, "Current work item is already claimed.", already_claimed=True)
                feedback = next((s for s in plan["plan"] if s.get("feedback", {}).get("status") == "open"
                                 and s["status"] != "blocked"), None)
                ready = ready_steps(plan)
                if feedback:
                    if step and step["id"] != feedback["id"]:
                        raise ValueError("open review feedback must be resolved before other work")
                    step, kind = feedback, "rework"
                else:
                    if step and step["id"] not in ready:
                        raise ValueError("step is not ready; dependencies must have passed")
                    step = step or (by_id[ready[0]] if ready else None)
                    if step is None:
                        return response(latest, "No claimable work; inspect summary and blockers.")
                    kind = step.get("type", "implementation")
                step["status"] = "in_progress"
                plan["current"] = {"kind": kind, "step_id": step["id"], "claim_id": str(uuid.uuid4()),
                                   "claimed_turn_id": context.turn_id}
            elif action in {"submit", "review", "resolve"}:
                if not current or not args.get("claim_id") or current["claim_id"] != args["claim_id"]:
                    raise ValueError("a matching claim_id is required; inspect or claim the current work")
                if not step or step["id"] != current["step_id"]:
                    raise ValueError("step_id must match the claimed work item")
                kind = {"submit": "implementation", "review": "review", "resolve": "rework"}[action]
                if current["kind"] != kind:
                    raise ValueError(f"{action} cannot conclude a {current['kind']} claim")
                if not args.get("report", "").strip():
                    raise ValueError("a concrete report is required")
                facts, rejected = evidence(store, context, args.get("evidence_refs", []))
                if rejected:
                    return rejected
                assessment = {"report": args["report"].strip(), "evidence_refs": deepcopy(args["evidence_refs"]),
                              "evidence_facts": facts, "assessed_turn_id": context.turn_id}
                if action == "submit":
                    if "outcome" not in args:
                        raise ValueError("submit requires an honest outcome")
                    old_assessment = step.get("last_assessment", {})
                    if args["outcome"] == "passed" and old_assessment.get("outcome") and not evidence_changed(old_assessment, facts):
                        raise ValueError("passing reopened work requires new verification evidence")
                    step.update(assessment, status="completed", outcome=args["outcome"], runs=step.get("runs", 0) + 1)
                elif action == "review":
                    if "verdict" not in args:
                        raise ValueError("review requires passed or needs_changes verdict")
                    prior_review = step if step.get("outcome") else step.get("last_assessment", {})
                    if args["verdict"] == "passed" and prior_review.get("outcome") == "failed" and not evidence_changed(prior_review, facts):
                        raise ValueError("passing a previously failed review requires new verification evidence")
                    step["cycles"] = step.get("cycles", 0) + 1
                    step.update(assessment)
                    if args["verdict"] == "passed":
                        step.update(status="completed", outcome="passed")
                    else:
                        exhausted = step["cycles"] >= step.get("max_review_cycles", 3)
                        step.update(status="blocked" if exhausted else "pending", outcome="failed",
                                    feedback={"id": str(uuid.uuid4()), "status": "open", **assessment})
                        if exhausted:
                            step["blocker"] = "Review cycle limit reached; reopen upstream work with an explanation before retrying."
                else:
                    feedback = step.get("feedback", {})
                    if feedback.get("status") != "open":
                        raise ValueError("no open feedback to resolve")
                    if not evidence_changed(feedback, facts):
                        raise ValueError("resolving feedback requires new verification evidence")
                    feedback.update(status="resolved", resolution=assessment)
                    step["status"] = "pending"
                plan["current"] = None
            else:
                if step is None:
                    raise ValueError("known step_id is required")
                if not args.get("explanation", "").strip():
                    raise ValueError(f"{action} requires an explanation")
                if action == "block":
                    if step["status"] == "completed":
                        raise ValueError("completed work must be explicitly reopened before blocking")
                    if current and current["step_id"] != step["id"]:
                        raise ValueError("finish or block the current claim before changing other work")
                    step.update(status="blocked", blocker=args["explanation"])
                    plan["current"] = None
                elif action == "unblock":
                    if step["status"] != "blocked":
                        raise ValueError("step is not blocked")
                    if step.get("cycles", 0) >= step.get("max_review_cycles", 3):
                        raise ValueError("exhausted review requires explicit upstream reopen, not unblock")
                    step["status"] = "pending"
                    step.pop("blocker", None)
                elif action == "reopen":
                    if current:
                        raise ValueError("finish or block the current claim before reopening work")
                    if step.get("type") == "review":
                        raise ValueError("reopen the implementation being changed; dependent reviews are invalidated automatically")
                    if step["status"] not in {"completed", "blocked"}:
                        raise ValueError("only completed or blocked work can be reopened")
                    affected = {step["id"]}
                    while True:
                        expanded = affected | {s["id"] for s in plan["plan"] if affected.intersection(s.get("deps", []))}
                        if expanded == affected:
                            break
                        affected = expanded
                    for step_id in affected:
                        target = by_id[step_id]
                        if target.get("outcome"):
                            target["last_assessment"] = {k: deepcopy(target[k]) for k in
                                                         ("outcome", "evidence_refs", "evidence_facts") if k in target}
                        target["status"] = "pending"
                        for key in ("outcome", "evidence_refs", "evidence_facts", "report", "feedback", "blocker", "cycles"):
                            target.pop(key, None)
                    plan["invalidated_steps"] = sorted(affected)
            plan = stamp(plan, latest, context)
        plan["explanation"] = args.get("explanation", "")
        plan["operation_id"] = args["operation_id"]
        plan["operation"] = fingerprint
        persist(context, plan)
        return response(plan, "Plan action recorded.")

    return AgentTool("manage_plan", (
        "Inspect the durable plan, or execute an opt-in graph. Mutations require plan_id, expected_revision, "
        "and unique operation_id (reuse only for an identical retry). claim returns a claim_id and work packet; "
        "submit/review/resolve require matching step_id, claim_id, report and evidence_refs. submit also requires "
        "outcome; review requires verdict=passed|needs_changes. Feedback has priority; resolve requires NEW "
        "verification evidence, then claim the review again. A review limit blocks downstream work. "
        "block/unblock/reopen/archive require explanation. Reopen implementation invalidates all dependent "
        "acceptance and resets dependent review cycles, preserving history. Archive ends the plan lifecycle "
        "without declaring success. Never use an old plan as authorization for unrelated work."),
        MANAGE_SCHEMA, manage, effect=ToolEffect.READ_ONLY, supports_parallel_tool_calls=False)
