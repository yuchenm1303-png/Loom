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


def done(step):
    return {"step": step, "status": "completed", "outcome": "passed", "evidence_refs": [{"path": "proof.txt"}]}


SIX = ["读代码", "设计温度头", "冒烟", "训练 nr_temphead.pt", "高 SNR 配对评估", "更新报告"]


def test_unfinished_steps_left_out_stay_as_dropped_with_the_reason(harness):
    # Real run: six planned steps became one "explored (negative result): completed" after a 100-step smoke test.
    h = harness
    h.update.handler(h.context, {"plan": [done(SIX[0]), {"step": SIX[1], "status": "in_progress"}]
                                  + [{"step": s, "status": "pending"} for s in SIX[2:]]})
    result = h.update.handler(h.context, {"plan": [done(SIX[0]), done("温度头探索（负向结论）")],
                                          "explanation": "冒烟结果为负"})
    assert result.ok and "kept as dropped" in result.content and "训练 nr_temphead.pt" in result.content
    plan = current_plan(h.store.events("abcdef"))
    assert [d["step"] for d in plan["dropped"]] == SIX[1:]
    assert {d["reason"] for d in plan["dropped"]} == {"冒烟结果为负"}
    assert {d["status"] for d in plan["dropped"]} == {"pending", "in_progress"}
    assert summary(plan)["dropped"] == 5 and summary(plan)["status"] == "passed", "dropping is visible, not a block"


def test_dropped_steps_are_carried_until_listed_again(harness):
    h = harness
    h.update.handler(h.context, {"plan": [{"step": "A", "status": "in_progress"}, {"step": "B", "status": "pending"},
                                          {"step": "C", "status": "pending"}]})
    h.update.handler(h.context, {"plan": [{"step": "A", "status": "in_progress"}, {"step": "D", "status": "pending"}],
                                 "explanation": "换方向"})
    h.update.handler(h.context, {"plan": [done("A"), {"step": "D", "status": "in_progress"}]})
    assert [d["step"] for d in current_plan(h.store.events("abcdef"))["dropped"]] == ["B", "C"], "a later update keeps them"
    h.update.handler(h.context, {"plan": [done("A"), {"step": "D", "status": "in_progress"}, {"step": "B", "status": "pending"}],
                                 "explanation": "B 还是要做"})
    assert [d["step"] for d in current_plan(h.store.events("abcdef"))["dropped"]] == ["C"]


def test_finished_steps_left_out_are_not_dropped(harness):
    h = harness
    h.update.handler(h.context, {"plan": [done("A"), {"step": "B", "status": "pending"}]})
    result = h.update.handler(h.context, {"plan": [{"step": "B", "status": "in_progress"}, {"step": "C", "status": "pending"}],
                                          "explanation": "A 已做完，不再列"})
    assert "dropped" not in current_plan(h.store.events("abcdef")) and "kept as dropped" not in result.content


def test_replacing_the_plan_keeps_what_it_left_unfinished(harness):
    h = harness
    h.update.handler(h.context, {"plan": [{"step": "A", "status": "in_progress"}, {"step": "B", "status": "pending"}]})
    first = current_plan(h.store.events("abcdef"))
    h.update.handler(h.context, {"plan": [{"step": "X", "status": "in_progress"}, {"step": "Y", "status": "pending"}],
                                 "replace": True, "plan_id": first["plan_id"], "expected_revision": first["revision"],
                                 "explanation": "用户换了任务"})
    assert [d["step"] for d in current_plan(h.store.events("abcdef"))["dropped"]] == ["A", "B"]


# Whose list is it: a list belongs to the person's request it was written for (2026-11-08). A real person asked
# "sum up where we are"; the model handed in the earlier request's unfinished list unchanged and went on editing.

def turn(h, turn_id, text="", source="user", retrying=False):
    """A new turn as the runtime starts it, and a context for tools called in it."""
    def emit(kind, data, turn_id=turn_id):
        h.store.append_event(AgentEvent(str(uuid.uuid4()), "abcdef", turn_id, kind, "2026-10-16T00:00:00Z", deepcopy(data)))
    emit(E.TURN_STARTED, {"source": source, "retrying_failed_input": retrying})
    if not retrying:
        emit(E.USER_MESSAGE, {"text": text, "source": source})
    return ToolContext("abcdef", turn_id, h.workspace, emit_event=emit)


def write(h, context, plan, **extra):
    return h.update.handler(context, {"plan": plan, **extra})


UNDONE = [{"step": "修正 README", "status": "in_progress"}, {"step": "冒烟终检", "status": "pending"}]


def test_a_new_request_after_a_finished_list_gets_a_new_list(harness):
    h = harness
    first = turn(h, "t1", "做实验")
    write(h, first, [done("实验"), done("报告")])
    old = current_plan(h.store.events("abcdef"))
    second = turn(h, "t2", "总结一下成果")
    write(h, second, [{"step": "读结果", "status": "in_progress"}, {"step": "写总结", "status": "pending"}],
          plan_id=old["plan_id"], expected_revision=old["revision"])
    new = current_plan(h.store.events("abcdef"))
    assert new["plan_id"] != old["plan_id"] and new["revision"] == 1 and new["previous_plan_id"] == old["plan_id"]
    assert new["request"] == "总结一下成果" and new["request_turn_id"] == "t2"


def test_an_unfinished_list_does_not_carry_into_a_new_message_unasked(harness):
    h = harness
    write(h, turn(h, "t1", "修文档"), UNDONE)
    old = current_plan(h.store.events("abcdef"))
    second = turn(h, "t2", "总结一下我们现在的工作")
    with pytest.raises(ValueError, match="earlier request") as refused:
        write(h, second, UNDONE)
    assert "修文档" in str(refused.value) and old["plan_id"] in str(refused.value)
    assert current_plan(h.store.events("abcdef"))["revision"] == old["revision"], "nothing written"


def test_carrying_it_on_takes_a_reason_and_then_serves_the_new_message(harness):
    h = harness
    write(h, turn(h, "t1", "修文档"), UNDONE)
    second = turn(h, "t2", "继续")
    write(h, second, UNDONE, explanation="这条消息让我继续修文档")
    plan = current_plan(h.store.events("abcdef"))
    assert plan["request"] == "修文档" and plan["request_turn_id"] == "t2"
    write(h, second, [done("修正 README"), {"step": "冒烟终检", "status": "in_progress"}])  # same turn: no reason needed


def test_a_new_list_for_a_new_request_keeps_the_old_unfinished_steps_visible(harness):
    h = harness
    write(h, turn(h, "t1", "修文档"), UNDONE)
    old = current_plan(h.store.events("abcdef"))
    second = turn(h, "t2", "总结成果")
    write(h, second, [{"step": "读结果", "status": "in_progress"}, {"step": "写总结", "status": "pending"}],
          replace=True, plan_id=old["plan_id"], expected_revision=old["revision"], explanation="用户问的是总结")
    plan = current_plan(h.store.events("abcdef"))
    assert plan["request"] == "总结成果" and plan["request_turn_id"] == "t2"
    assert [d["step"] for d in plan["dropped"]] == ["修正 README", "冒烟终检"]


@pytest.mark.parametrize("source,retrying", [("goal", False), ("user", True)])
def test_a_goal_turn_or_a_retry_continues_the_list_as_it_is(harness, source, retrying):
    h = harness
    write(h, turn(h, "t1", "整晚研究"), UNDONE)
    later = turn(h, "t2", "Continue pursuing this durable Loom goal", source=source, retrying=retrying)
    write(h, later, [done("修正 README"), {"step": "冒烟终检", "status": "in_progress"}])
    plan = current_plan(h.store.events("abcdef"))
    assert plan["request_turn_id"] == "t1" and plan["request"] == "整晚研究"


def test_a_queued_message_is_the_persons_new_request(harness):
    h = harness
    write(h, turn(h, "t1", "修文档"), UNDONE)
    with pytest.raises(ValueError, match="earlier request"):
        write(h, turn(h, "t2", "顺便看看日志", source="queue"), UNDONE)


def test_a_list_from_before_requests_were_recorded_is_still_known_as_earlier(harness):
    h = harness
    write(h, h.context, UNDONE)  # the harness's own turn has no turn_started: like a list written by an older Loom
    plan = current_plan(h.store.events("abcdef"))
    plan.pop("request", None)
    with pytest.raises(ValueError, match="earlier request"):
        write(h, turn(h, "t2", "新问题"), UNDONE)


def test_the_list_the_model_reads_says_whose_it_is(harness):
    h = harness
    write(h, turn(h, "t1", "修文档"), UNDONE)
    events = h.store.events("abcdef")
    plans = [e for e in events if e.kind is E.PLAN_UPDATED]
    new_started = [AgentEvent("x", "abcdef", "t2", E.TURN_STARTED, "2026-10-16T00:00:00Z", {"source": "user"})]
    said = plan_context(plans, "t2", started=new_started).content
    assert "Task list from an earlier request: \"修文档\"" in said and "修正 README" in said and "Never take up" in said
    goal_started = [AgentEvent("y", "abcdef", "t3", E.TURN_STARTED, "2026-10-16T00:00:00Z", {"source": "goal"})]
    assert plan_context(plans, "t3", started=goal_started).content.startswith("Current task milestones")
    assert plan_context(plans, "t1", started=[]).content.startswith("Current task milestones")


def test_a_list_written_by_an_older_loom_is_read_by_the_turn_that_last_wrote_it():
    from app.agent_runtime.plan_state import serving, from_earlier_request
    legacy = {"plan": UNDONE, "origin_turn_id": "t0", "updated_turn_id": "t1"}
    assert serving(legacy) == "t1"
    started = [AgentEvent("x", "abcdef", "t2", E.TURN_STARTED, "2026-10-16T00:00:00Z", {"source": "user"})]
    assert from_earlier_request(legacy, started, "t2") is True
    assert from_earlier_request(legacy, started, "t1") is False


def test_replacing_a_finished_list_from_an_earlier_request_asks_for_nothing(harness):
    # Real run: the model replaced a finished list and was sent to look up its version first.
    h = harness
    write(h, turn(h, "t1", "加乘法"), [done("加乘法"), done("验证")])
    write(h, turn(h, "t2", "写说明"), [{"step": "写说明", "status": "in_progress"}, {"step": "核对", "status": "pending"}],
          replace=True, explanation="新任务")
    plan = current_plan(h.store.events("abcdef"))
    assert plan["revision"] == 1 and plan["request"] == "写说明" and "replaces_plan_id" not in plan


def test_a_wrong_value_is_answered_with_the_values_allowed(harness):
    from app.agent_runtime.tools import validate_tool_arguments, ToolRequestError
    from app.agent_runtime.task_plan import UPDATE_SCHEMA
    bad = {"plan": [{"step": "a", "status": "completed", "outcome": "success", "evidence_refs": [{"path": "proof.txt"}]},
                    {"step": "b", "status": "pending"}]}
    with pytest.raises(ToolRequestError) as refused:
        validate_tool_arguments(UPDATE_SCHEMA, bad)
    assert '"passed"' in str(refused.value) and '"not_assessed"' in str(refused.value)
    assert "success" not in str(refused.value), "the rejected value is not echoed"
    missing = {"plan": [{"step": "a", "status": "completed", "outcome": "passed"}, {"step": "b", "status": "pending"}]}
    with pytest.raises(ToolRequestError, match="missing: evidence_refs"):
        validate_tool_arguments(UPDATE_SCHEMA, missing)


def test_a_list_first_written_in_a_goal_turn_serves_the_persons_latest_message(harness):
    h = harness
    turn(h, "t1", "整晚研究随机图")
    write(h, turn(h, "g1", "Continue pursuing this durable Loom goal", source="goal"), UNDONE)
    plan = current_plan(h.store.events("abcdef"))
    assert plan["request_turn_id"] == "t1" and plan["request"] == "整晚研究随机图"
