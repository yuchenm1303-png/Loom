"""Event-backed plan state. No task authority is inferred from assistant prose."""
from copy import deepcopy
import json
import uuid

from .contracts import AgentEventKind as Event
from .evidence import resolve_evidence, rejected_evidence
from .tools import ToolResult


def latest_plan(events):
    event = next((e for e in reversed(events) if e.kind is Event.PLAN_UPDATED), None)
    if event is None:
        return None
    plan = deepcopy(event.data)
    # Read old journals without rewriting them or requiring a migration command.
    if not plan.get("plan_id"):
        plan["plan_id"] = str(uuid.uuid5(uuid.NAMESPACE_URL, f"loom-plan:{event.session_id}:{event.turn_id}"))
        plan["revision"] = sum(e.kind is Event.PLAN_UPDATED and e.turn_id == event.turn_id for e in events)
        plan["origin_turn_id"] = event.turn_id
    plan.setdefault("mode", "milestones")
    plan.setdefault("lifecycle", "active")
    plan.setdefault("current", None)
    return plan


def active_plan(events):
    plan = latest_plan(events)
    return plan if plan and plan.get("lifecycle", "active") == "active" else None


def summary(plan):
    if not plan or not plan.get("plan") or plan.get("lifecycle", "active") != "active":
        return {"status": "not_assessed"}
    steps = plan["plan"]
    passed = sum(s["status"] == "completed" and s.get("outcome") == "passed" for s in steps)
    completed = sum(s["status"] == "completed" for s in steps)
    return {"plan_id": plan.get("plan_id"), "revision": plan.get("revision", 0),
            "assessment_source": "assistant_assessment_with_verified_references",
            "total": len(steps), "completed": completed, "passed": passed,
            "status": "passed" if passed == len(steps) else "blocked" if any(
                s["status"] == "blocked" for s in steps) else "incomplete"}


def stamp(plan, previous, context):
    plan["plan_id"] = previous.get("plan_id") if previous and previous.get("plan_id") else str(uuid.uuid4())
    plan["revision"] = previous.get("revision", 0) + 1 if previous else 1
    plan["lifecycle"] = "active"
    plan["origin_turn_id"] = previous.get("origin_turn_id", context.turn_id) if previous else context.turn_id
    plan["updated_turn_id"] = context.turn_id
    return plan


def persist(context, plan, message="Plan updated."):
    if context.emit_event is None:
        raise RuntimeError("durable plan event service is unavailable")
    context.emit(Event.PLAN_UPDATED, plan)
    return ToolResult(True, message, deepcopy(plan))


def guard_version(previous, arguments, *, required=False):
    if required and ("plan_id" not in arguments or "expected_revision" not in arguments):
        raise ValueError("inspect manage_plan first; plan_id and expected_revision are required")
    if "plan_id" in arguments and arguments["plan_id"] != previous.get("plan_id"):
        raise ValueError("plan_id does not match the active plan; inspect manage_plan")
    if "expected_revision" in arguments and arguments["expected_revision"] != previous.get("revision", 0):
        raise ValueError("stale plan revision; inspect manage_plan and reconcile before retrying")


def evidence(store, context, refs):
    if not refs:
        return [], rejected_evidence([{"reason": "evidence_refs are required"}])
    facts, invalid = resolve_evidence(store, context, refs)
    return facts, rejected_evidence(invalid) if invalid else None


def evidence_changed(old, facts):
    # Event IDs and content hashes identify verification versions. A timestamp
    # change by itself must not turn a failed assessment into a pass.
    def identities(values):
        return {(f.get("call_id"), f.get("event_id"), f.get("path"), f.get("sha256")) for f in values}
    return bool(identities(facts) - identities(old.get("evidence_facts", [])))


def validate_graph(steps):
    if len(json.dumps(steps, ensure_ascii=False)) > 128000:
        raise ValueError("graph definition exceeds 128000 characters; split it into independently scoped plans")
    by_id = {s.get("id"): s for s in steps}
    if None in by_id or len(by_id) != len(steps):
        raise ValueError("graph steps require unique stable ids")
    for step in steps:
        if step["status"] != "pending":
            raise ValueError("new graph steps must start pending; use manage_plan to execute")
        if any(key in step for key in ("outcome", "evidence_refs", "evidence", "blocker")):
            raise ValueError("new graph steps cannot contain execution results")
        if not step.get("acceptance") or any(not criterion.strip() for criterion in step["acceptance"]):
            raise ValueError("graph steps require concrete acceptance criteria")
        if not step.get("instructions", "").strip():
            raise ValueError("graph steps require instructions")
        deps = step.get("deps", [])
        if len(set(deps)) != len(deps) or any(dep not in by_id or dep == step["id"] for dep in deps):
            raise ValueError("graph dependencies must be distinct known step ids, excluding self")
        if step.get("type", "implementation") == "review" and (
            not deps or any(by_id[dep].get("type", "implementation") != "implementation" for dep in deps)
        ):
            raise ValueError("review steps must depend on implementation steps they assess")
    visiting, done = set(), set()
    def visit(step_id):
        if step_id in visiting:
            raise ValueError("plan dependency cycle")
        if step_id in done:
            return
        visiting.add(step_id)
        for dep in by_id[step_id].get("deps", []):
            visit(dep)
        visiting.remove(step_id)
        done.add(step_id)
    for step_id in by_id:
        visit(step_id)


def ready_steps(plan):
    by_id = {s["id"]: s for s in plan["plan"]}
    return [s["id"] for s in plan["plan"] if s["status"] == "pending"
            and s.get("feedback", {}).get("status") != "open"
            and all(by_id[d]["status"] == "completed" and by_id[d].get("outcome") == "passed"
                    for d in s.get("deps", []))]


def view(plan):
    result = deepcopy(plan)
    result["summary"] = summary(plan)
    result["ready"] = ready_steps(plan) if plan.get("mode") == "graph" and plan.get("lifecycle") == "active" else []
    return result
