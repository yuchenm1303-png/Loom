"""Real reducer/storage/protocol regressions for durable plans."""
from copy import deepcopy
import hashlib
import json
from types import SimpleNamespace
import uuid

import pytest

from app.agent_runtime.contracts import AgentEvent, AgentEventKind as E
from app.agent_runtime.storage import FileAgentSessionStore
from app.agent_runtime.task_plan import update_plan_tool, current_plan, plan_context
from app.agent_runtime.plan_tools import manage_plan_tool
from app.agent_runtime.plan_state import summary
from app.agent_runtime.tools import ToolContext


@pytest.fixture
def harness(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "proof.txt").write_text("version one", encoding="utf-8")
    store = FileAgentSessionStore(tmp_path / "state")
    def emit(kind, data):
        store.append_event(AgentEvent(str(uuid.uuid4()), "abcdef", "turn-one", kind,
                                     "2026-10-16T00:00:00Z", deepcopy(data)))
    context = ToolContext("abcdef", "turn-one", workspace, emit_event=emit)
    return SimpleNamespace(store=store, context=context, workspace=workspace,
                           update=update_plan_tool(store), manage=manage_plan_tool(store))


def graph(limit=3):
    def step(step_id, deps=(), kind="implementation"):
        return {"id": step_id, "step": step_id, "status": "pending", "deps": list(deps),
                "type": kind, "acceptance": [f"{step_id} contract verified"], "instructions": f"Perform {step_id}"}
    review = step("review", ["build"], "review")
    review["max_review_cycles"] = limit
    return {"mode": "graph", "plan": [step("build"), review, step("ship", ["review"])]}


def state(h):
    return h.manage.handler(h.context, {"action": "inspect"}).data


def act(h, action, **kwargs):
    current = state(h)
    args = {"action": action, "plan_id": current["plan_id"], "expected_revision": current["revision"],
            "operation_id": str(uuid.uuid4()), **kwargs}
    return h.manage.handler(h.context, args)


def conclude(h, action, **kwargs):
    current = state(h)["current"]
    return act(h, action, step_id=current["step_id"], claim_id=current["claim_id"],
               report="Observed verification result", evidence_refs=[{"path": "proof.txt"}], **kwargs)


def test_plan_survives_reload_and_new_turn_then_archive_ends_projection(harness):
    h = harness
    h.update.handler(h.context, graph())
    first = state(h)
    reloaded = FileAgentSessionStore(h.store.root.parent.parent)
    assert current_plan(reloaded.events("abcdef"), "different-turn")["plan_id"] == first["plan_id"]
    assert json.loads(plan_context(reloaded.context_events("abcdef"), "different-turn").content.split("\n", 1)[1])["plan_id"] == first["plan_id"]
    act(h, "archive", explanation="User changed the requested task")
    assert plan_context(reloaded.events("abcdef"), "different-turn") is None
    assert h.store.events("abcdef")[0].data["lifecycle"] == "active"
    h.update.handler(h.context, graph())
    assert state(h)["plan_id"] != first["plan_id"]


@pytest.mark.parametrize("mutation", [
    lambda p: p["plan"][0].update(deps=["ship"]),
    lambda p: p["plan"][0].update(deps=["missing"]),
    lambda p: p["plan"][0].update(id="review"),
    lambda p: p["plan"][1].update(deps=[]),
    lambda p: p["plan"][1].update(max_review_cycles=0),
    lambda p: p["plan"][0].update(acceptance=[" "]),
    lambda p: p["plan"][0].update(status="in_progress"),
    lambda p: p["plan"][0].update(outcome="passed"),
])
def test_invalid_graph_is_atomic(harness, mutation):
    plan = graph()
    mutation(plan)
    with pytest.raises(ValueError):
        harness.update.handler(harness.context, plan)
    assert not harness.store.events("abcdef")


def test_claim_enforces_dependencies_and_matching_token(harness):
    h = harness
    h.update.handler(h.context, graph())
    assert state(h)["ready"] == ["build"]
    with pytest.raises(ValueError, match="not ready"):
        act(h, "claim", step_id="ship")
    with pytest.raises(ValueError, match="claim_id"):
        act(h, "submit", step_id="build", claim_id="invented", report="done", outcome="passed")
    claimed = act(h, "claim").data
    assert claimed["packet"]["step"]["id"] == "build"
    assert act(h, "claim").data["current"] == claimed["current"]
    with pytest.raises(ValueError, match="another work"):
        act(h, "claim", step_id="ship")
    with pytest.raises(ValueError, match="cannot conclude"):
        conclude(h, "review", verdict="passed")
    conclude(h, "submit", outcome="passed")
    assert state(h)["ready"] == ["review"]


def test_review_rework_cycle_and_downstream_gate(harness):
    h = harness
    h.update.handler(h.context, graph())
    act(h, "claim")
    conclude(h, "submit", outcome="passed")
    act(h, "claim")
    rejected = conclude(h, "review", verdict="needs_changes").data
    assert rejected["ready"] == []
    assert rejected["plan"][1]["cycles"] == 1
    assert act(h, "claim").data["current"]["kind"] == "rework"
    with pytest.raises(ValueError, match="new verification"):
        conclude(h, "resolve")
    (h.workspace / "proof.txt").write_text("fixed version", encoding="utf-8")
    conclude(h, "resolve")
    assert state(h)["ready"] == ["review"]
    claimed = act(h, "claim").data
    assert claimed["packet"]["step"]["feedback"]["resolution"]["evidence_facts"][0]["sha256"]
    conclude(h, "review", verdict="passed")
    assert state(h)["ready"] == ["ship"]
    assert state(h)["plan"][1]["cycles"] == 2
    act(h, "claim")
    conclude(h, "submit", outcome="passed")
    assert state(h)["summary"]["status"] == "passed"


def test_exhausted_review_never_unlocks_dependents_and_reopen_invalidates(harness):
    h = harness
    h.update.handler(h.context, graph(limit=1))
    act(h, "claim")
    conclude(h, "submit", outcome="passed")
    act(h, "claim")
    conclude(h, "review", verdict="needs_changes")
    assert state(h)["summary"]["status"] == "blocked"
    assert state(h)["plan"][1]["status"] == "blocked"
    assert act(h, "claim").data["current"] is None
    with pytest.raises(ValueError, match="exhausted"):
        act(h, "unblock", step_id="review", explanation="try again")
    reopened = act(h, "reopen", step_id="build", explanation="Change upstream implementation").data
    assert reopened["invalidated_steps"] == ["build", "review", "ship"]
    assert all(s["status"] == "pending" for s in reopened["plan"])
    assert reopened["ready"] == ["build"]
    assert any(e.data.get("plan", [{}, {}])[1].get("status") == "blocked" for e in h.store.events("abcdef"))


def test_stale_version_and_operation_retry_do_not_duplicate_claim(harness):
    h = harness
    h.update.handler(h.context, graph())
    original = state(h)
    args = {"action": "claim", "plan_id": original["plan_id"], "expected_revision": original["revision"], "operation_id": "claim-once"}
    first = h.manage.handler(h.context, args).data
    count = len(h.store.events("abcdef"))
    assert h.manage.handler(h.context, args).data["already_applied"]
    assert len(h.store.events("abcdef")) == count
    with pytest.raises(ValueError, match="stale"):
        h.manage.handler(h.context, {**args, "operation_id": "new-op"})
    with pytest.raises(ValueError, match="different arguments"):
        h.manage.handler(h.context, {**args, "step_id": "review"})
    conclude(h, "submit", outcome="passed")
    assert h.manage.handler(h.context, args).data["revision"] > first["revision"]


def milestones(outcome="failed"):
    return {"plan": [{"step": "Verify", "status": "completed", "outcome": outcome,
                      "evidence_refs": [{"path": "proof.txt"}]}, {"step": "Report", "status": "pending"}]}


def test_outcome_promotion_requires_explanation_and_new_version(harness):
    h = harness
    h.update.handler(h.context, milestones())
    with pytest.raises(ValueError, match="explanation"):
        h.update.handler(h.context, milestones("passed"))
    with pytest.raises(ValueError, match="new verification"):
        h.update.handler(h.context, {**milestones("passed"), "explanation": "fixed"})
    (h.workspace / "proof.txt").write_text("new verification", encoding="utf-8")
    assert h.update.handler(h.context, {**milestones("passed"), "explanation": "Fixed and verified"}).ok


def test_reopening_cannot_launder_failed_evidence_into_pass(harness):
    h = harness
    h.update.handler(h.context, milestones())
    h.update.handler(h.context, {"plan": [{"step": "Verify", "status": "in_progress"},
                                         {"step": "Report", "status": "pending"}], "explanation": "retry"})
    with pytest.raises(ValueError, match="new verification"):
        h.update.handler(h.context, {**milestones("passed"), "explanation": "finished retry"})


def test_evidence_hash_is_immutable_when_another_step_updates(harness):
    h = harness
    h.update.handler(h.context, milestones("passed"))
    old_hash = state(h)["plan"][0]["evidence_facts"][0]["sha256"]
    assert old_hash == hashlib.sha256(b"version one").hexdigest()
    (h.workspace / "proof.txt").write_text("overwritten later", encoding="utf-8")
    args = milestones("passed")
    args["plan"][1]["status"] = "in_progress"
    h.update.handler(h.context, args)
    assert state(h)["plan"][0]["evidence_facts"][0]["sha256"] == old_hash


def test_graph_cannot_be_overwritten_with_fabricated_completion(harness):
    h = harness
    h.update.handler(h.context, graph())
    before = state(h)
    with pytest.raises(ValueError):
        h.update.handler(h.context, {**milestones("passed"), "plan_id": before["plan_id"], "expected_revision": before["revision"]})
    replacement = h.update.handler(h.context, {**graph(), "replace": True, "explanation": "New user scope",
        "plan_id": before["plan_id"], "expected_revision": before["revision"]}).data
    assert replacement["plan_id"] != before["plan_id"]
    assert replacement["replaces_plan_id"] == before["plan_id"]


def test_invalid_evidence_does_not_release_claim(harness):
    h = harness
    h.update.handler(h.context, graph())
    act(h, "claim")
    before = state(h)
    result = act(h, "submit", step_id="build", claim_id=before["current"]["claim_id"],
                 report="done", outcome="passed", evidence_refs=[{"path": "missing"}])
    assert not result.ok
    assert state(h)["revision"] == before["revision"]
    assert state(h)["current"] == before["current"]


def test_goal_completion_rejects_unpassed_active_plan(harness):
    from app.agent_runtime.thread_state_tools import durable_thread_tools
    h = harness
    h.update.handler(h.context, milestones())
    calls = []
    tools = durable_thread_tools(SimpleNamespace(set_goal_status=lambda *args: calls.append(args)), plan_store=h.store)
    with pytest.raises(ValueError, match="incomplete or unpassed"):
        tools[1].handler(h.context, {"status": "complete"})
    assert not calls


def test_plan_event_projects_without_tool_receipt(harness):
    from app.app_server import LoomAppServerService, _event_item_identity, _apply_event_to_item
    h = harness
    h.update.handler(h.context, graph())
    event = h.store.events("abcdef")[-1]
    identity = _event_item_identity(event)
    assert identity[1] == "task_plan"
    item = {"type": identity[1]}
    _apply_event_to_item(item, event)
    assert item["result"]["plan_id"] == state(h)["plan_id"]
    assert item["status"] == "completed"
    notifications = []
    service = SimpleNamespace(_notify=lambda method, params: notifications.append((method, params)))
    LoomAppServerService._on_runtime_event(service, event)
    assert notifications[0][0] == "item/completed"
    assert notifications[0][1]["item"]["result"]["plan_id"] == state(h)["plan_id"]


def test_failed_review_cannot_be_laundered_by_reopening_upstream(harness):
    h = harness
    h.update.handler(h.context, graph(limit=1))
    act(h, "claim")
    conclude(h, "submit", outcome="passed")
    act(h, "claim")
    conclude(h, "review", verdict="needs_changes")
    act(h, "reopen", step_id="build", explanation="Fix rejected implementation")
    act(h, "claim")
    with pytest.raises(ValueError, match="new verification"):
        conclude(h, "submit", outcome="passed")
    (h.workspace / "proof.txt").write_text("fixed after review", encoding="utf-8")
    conclude(h, "submit", outcome="passed")
    act(h, "claim")
    # Merely pointing back to the rejected bytes cannot pass the review.
    (h.workspace / "proof.txt").write_text("version one", encoding="utf-8")
    with pytest.raises(ValueError, match="new verification"):
        conclude(h, "review", verdict="passed")


def test_committed_claim_recovers_after_crash_before_tool_receipt(harness):
    from dataclasses import replace
    h = harness
    h.update.handler(h.context, graph())
    before = state(h)
    args = {"action": "claim", "plan_id": before["plan_id"], "expected_revision": before["revision"], "operation_id": "crash-operation"}
    def crash(kind, data):
        h.context.emit_event(kind, data)
        raise RuntimeError("process died before tool completed")
    with pytest.raises(RuntimeError, match="process died"):
        h.manage.handler(replace(h.context, emit_event=crash), args)
    reloaded = FileAgentSessionStore(h.store.root.parent.parent)
    recovered = manage_plan_tool(reloaded).handler(h.context, args)
    assert recovered.data["already_applied"]
    assert recovered.data["current"]["step_id"] == "build"
    assert len(reloaded.events("abcdef")) == 2


def test_legacy_plan_can_be_inspected_and_archived_without_rewriting_history(harness):
    h = harness
    h.context.emit_event(E.PLAN_UPDATED, {"plan": [{"step": "Old", "status": "pending"}]})
    first = state(h)
    assert first["revision"] == 1
    assert state(h)["plan_id"] == first["plan_id"]
    act(h, "archive", explanation="Finish legacy lifecycle")
    assert current_plan(h.store.events("abcdef")) is None
    assert "plan_id" not in h.store.events("abcdef")[0].data


def test_turn_summary_and_context_follow_durable_plan(tmp_path):
    from app.agent_runtime import AgentRuntime, ToolRegistry
    from app.ai import ModelResponse, ToolCall
    class Platform:
        def __init__(self):
            self.requests = []
            self.responses = [ModelResponse(tool_calls=(ToolCall("plan-call", "update_plan", {
                "plan": [{"step": "Build", "status": "in_progress"}, {"step": "Verify", "status": "pending"}]}),)),
                ModelResponse(text="Need user input"), ModelResponse(text="Continuing")]
        def execute_chat(self, profile_id, request):
            self.requests.append(request)
            return self.responses.pop(0)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    platform = Platform()
    store = FileAgentSessionStore(tmp_path / "state")
    runtime = AgentRuntime(platform=platform, store=store, tools=ToolRegistry())
    session = runtime.create_session("test", workspace_dir=workspace)
    runtime.start_turn(session.session_id, "Build and verify")
    terminal = next(e for e in reversed(store.events(session.session_id)) if e.kind is E.TURN_COMPLETED)
    assert terminal.data["task_completion"] == "incomplete"
    runtime.start_turn(session.session_id, "Continue")
    assert any(m.name == "loom_task_plan" or "Current task milestones" in str(m.content) for m in platform.requests[-1].messages)
    assert summary(current_plan(store.events(session.session_id)))["status"] == "incomplete"


def test_runtime_executes_entire_graph_through_exposed_tools(tmp_path):
    from app.agent_runtime import AgentRuntime, ToolRegistry
    from app.ai import ModelResponse, ToolCall
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "proof.txt").write_text("verified contract", encoding="utf-8")
    store = FileAgentSessionStore(tmp_path / "state")
    class Platform:
        def __init__(self):
            self.index = 0
        def execute_chat(self, profile_id, request):
            index = self.index
            self.index += 1
            if index == 0:
                return ModelResponse(tool_calls=(ToolCall("define", "update_plan", graph()),))
            if index == 7:
                return ModelResponse(text="Implemented, reviewed and delivered")
            plan = current_plan(store.events(request.session_id))
            args = {"plan_id": plan["plan_id"], "expected_revision": plan["revision"], "operation_id": str(index)}
            if index in {1, 3, 5}:
                args["action"] = "claim"
            else:
                args.update(action="review" if index == 4 else "submit", step_id=plan["current"]["step_id"],
                            claim_id=plan["current"]["claim_id"], report="Verified actual contract",
                            evidence_refs=[{"path": "proof.txt"}])
                args["verdict" if index == 4 else "outcome"] = "passed"
            return ModelResponse(tool_calls=(ToolCall(f"operation-{index}", "manage_plan", args),))
    platform = Platform()
    runtime = AgentRuntime(platform=platform, store=store, tools=ToolRegistry())
    session = runtime.create_session("test", workspace_dir=workspace)
    result = runtime.start_turn(session.session_id, "Implement and review the contract")
    assert platform.index == 8
    assert "Implemented" in result.final_text
    events = store.events(session.session_id)
    terminal = next(e for e in reversed(events) if e.kind is E.TURN_COMPLETED)
    assert terminal.data["task_completion"] == "passed"
    assert terminal.data["plan_summary"]["passed"] == 3
    assert not [e for e in events if e.kind is E.TOOL_FAILED]
    assert current_plan(events)["plan"][1]["cycles"] == 1
