"""Request-boundary project budgets: admission, exactly-once usage, recovery."""
from types import SimpleNamespace
import threading
import uuid

import pytest

from app.ai import AIMessage, MessageRole, ModelResponse, ModelUsage, ToolCall
from app.ai.errors import AITransportError
from app.ai.execution_control import ModelAdmissionRefused
from app.agent_runtime import AgentTool, ToolEffect, ToolResult
from app.agent_runtime.contracts import AgentEventKind as E
from app.project_runner.budget import BudgetedModelExecutor, ProjectBudget
from app.project_runner.store import ProjectStore, encoded
from test_project_runner import PLAN, build, control, create, cycle, reply


def ledger(tmp_path, budget=100, sessions=("s1",)):
    store = ProjectStore(tmp_path / "ledger")
    run = store.create(str(uuid.uuid4()), {"tokenBudget": budget, "maxEpisodes": 9})
    return store, run, ProjectBudget(store, set(sessions))


def episode(store, run, session="s1", run_id=None):
    item = {"id": str(uuid.uuid4()), "turnId": str(uuid.uuid4()), "sessionId": session}
    with store.transaction() as db:
        db.execute("INSERT INTO project_episodes VALUES(?,?,?,?,?,?)",
                   (item["id"], run_id or run["id"], session, item["turnId"], "prepared", encoded(item)))
    return item


def request(session="s1", text="hello"):
    return SimpleNamespace(session_id=session, messages=(AIMessage(role=MessageRole.USER, content=text),),
                           tools=(), max_output_tokens=None)


def usage_event(session, turn, tokens, kind=E.MODEL_RESPONSE):
    field = "summary_usage" if kind is E.CONTEXT_CHECKPOINTED else "usage"
    return SimpleNamespace(event_id=str(uuid.uuid4()), session_id=session, turn_id=turn, kind=kind,
                           data={field: {"total_tokens": tokens}})


class Inner:
    def __init__(self, *outcomes):
        self.outcomes, self.calls = list(outcomes), 0

    def execute(self, platform, profile, request, token, **kwargs):
        self.calls += 1
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return ModelResponse(text="ok", usage=ModelUsage(outcome, 0, outcome))


def charged(budget, run):
    return budget.report(run)["chargedTokens"]


def test_worker_stops_at_the_next_request_boundary_and_keeps_its_task(tmp_path):
    def step(context, arguments):
        return ToolResult(ok=True, content="partial work")
    tool = AgentTool(name="partial_step", description="Do part of the work", input_schema={"type": "object", "properties": {}},
                     effect=ToolEffect.MUTATING, handler=step)
    # planner 30, worker first sample 40: 70 known, and a request as large as the
    # largest one so far (40) cannot fit in 100, so the third request is never sent.
    service, runtime, platform, _, source = build(tmp_path, [reply(PLAN, tokens=30),
        ModelResponse(tool_calls=(ToolCall(call_id="c1", name="partial_step", arguments={}),), usage=ModelUsage(40, 0, 40)),
        reply({"action": "candidate", "summary": "never sampled"})], tools=(tool,))
    try:
        run = control(service, create(service, source, tokenBudget=100), "start")
        for _ in range(3):
            cycle(service)
        final = service.project_store.get(run["id"])
        assert len(platform.requests) == 2
        assert final["control"] == "budget_limited" and final["state"] == "running"
        assert final["tokensUsed"] == 70 and final["plan"][0]["state"] == "pending"
        assert "token budget" in final["reason"] and "stopped at the project token budget" in final["handoff"]
        position = service.project_read(run["id"])["budgetPosition"]
        assert position["knownTokens"] == 70 and position["refusedRequests"] == 1 and position["hardBillingCap"] is False
        worker = runtime.get_session(final["sessions"]["worker-0"])
        assert worker.status.value == "limit_reached"
        # Offering the same runtime events again charges nothing twice.
        for event in service.store.events(worker.session_id):
            service.project_budget.observe(event)
        service.project_runner.tick()
        assert service.project_store.get(run["id"])["tokensUsed"] == 70
    finally:
        runtime.close()


def test_events_are_charged_once_whether_live_settled_or_replayed(tmp_path):
    store, run, budget = ledger(tmp_path)
    item = episode(store, run)
    first, second = usage_event("s1", item["turnId"], 11), usage_event("s1", item["turnId"], 7, E.CONTEXT_CHECKPOINTED)
    budget.observe(first)
    budget.observe(first)
    result = {"usage": [(first.event_id, 11), (second.event_id, 7)]}
    with store.transaction() as db:
        current = store._read(db, run["id"])
        assert budget.settle(db, current, item, result, store.save) == 18
        store.save(db, current, "settled")
    budget.observe(second)
    with store.transaction() as db:
        current = store._read(db, run["id"])
        budget.settle(db, current, item, result, store.save)
        store.save(db, current, "settled_again")
    budget.apply_late()
    assert store.get(run["id"])["tokensUsed"] == 18


def test_late_response_after_settlement_is_charged_once(tmp_path):
    store, run, budget = ledger(tmp_path)
    item = episode(store, run)
    with store.transaction() as db:
        current = store._read(db, run["id"])
        budget.settle(db, current, item, {"usage": []}, store.save)
        store.save(db, current, "settled")
    late = usage_event("s1", item["turnId"], 9)
    budget.observe(late)
    budget.apply_late()
    budget.observe(late)
    budget.apply_late()
    assert store.get(run["id"])["tokensUsed"] == 9


def test_response_without_an_event_is_charged_at_settlement(tmp_path):
    store, run, budget = ledger(tmp_path)
    item = episode(store, run)
    executor = BudgetedModelExecutor(Inner(13), budget)
    executor.execute(None, "p", request(), None)
    assert charged(budget, run) == 13  # in flight until an event or settlement reports it
    with store.transaction() as db:
        current = store._read(db, run["id"])
        assert budget.settle(db, current, item, {"usage": []}, store.save) == 13
        store.save(db, current, "settled")
    assert store.get(run["id"])["tokensUsed"] == 13 and charged(budget, store.get(run["id"])) == 13


def test_returned_response_is_not_double_counted_with_its_event(tmp_path):
    store, run, budget = ledger(tmp_path)
    item = episode(store, run)
    BudgetedModelExecutor(Inner(13), budget).execute(None, "p", request(), None)
    event = usage_event("s1", item["turnId"], 13)
    budget.observe(event)
    assert charged(budget, run) == 13
    with store.transaction() as db:
        current = store._read(db, run["id"])
        budget.settle(db, current, item, {"usage": [(event.event_id, 13)]}, store.save)
        store.save(db, current, "settled")
    assert store.get(run["id"])["tokensUsed"] == 13


def test_restart_turns_open_requests_into_assumed_unknown_usage(tmp_path):
    store, run, budget = ledger(tmp_path, budget=100)
    item = episode(store, run)
    executor = BudgetedModelExecutor(Inner(30), budget)
    executor.execute(None, "p", request(), None)
    budget.observe(usage_event("s1", item["turnId"], 30))
    ticket = budget.admit(request())  # sent, then the process died
    assert ticket
    restarted = ProjectBudget(store, {"s1"})
    restarted.recover()
    position = restarted.report(run)
    assert position["unknownRequests"] == 1 and position["assumedUnknownTokens"] == 30
    assert position["knownTokens"] == 30 and position["chargedTokens"] == 60
    # 60 charged + up to 30 more fits in 100; one more unknown does not.
    restarted.admit(request())
    restarted.recover()
    with pytest.raises(ModelAdmissionRefused):
        restarted.admit(request())


def test_parallel_workers_share_one_budget_including_in_flight_requests(tmp_path):
    store, root, budget = ledger(tmp_path, budget=100, sessions=("a", "b"))
    children = []
    for _ in range(2):
        child = {"id": str(uuid.uuid4()), "revision": 0, "teamOwnerId": root["id"], "tokenBudget": 100, "maxEpisodes": 9,
                 "tokensUsed": 0, "episodesUsed": 0, "state": "running", "activeEpisode": None}
        with store.transaction() as db:
            db.execute("INSERT INTO project_runs VALUES(?,0,?)", (child["id"], encoded(child)))
        children.append(child)
    a, b = episode(store, root, "a", children[0]["id"]), episode(store, root, "b", children[1]["id"])
    BudgetedModelExecutor(Inner(40), budget).execute(None, "p", request("a"), None)
    budget.observe(usage_event("a", a["turnId"], 40))
    # Worker A has a request in flight that reserves 40; B's 40 would make 120.
    assert budget.admit(request("a"))
    with pytest.raises(ModelAdmissionRefused):
        budget.admit(request("b"))
    with store.transaction() as db:
        child = store._read(db, children[0]["id"])
        budget.settle(db, child, a, {"usage": []}, store.save)
        store.save(db, child, "settled")
    # Known usage reaches the shared root; A's unsettled request stays assumed, not known.
    assert store.get(root["id"])["tokensUsed"] == 40
    assert charged(budget, store.get(root["id"])) == 80


def test_rejections_unknowns_pass_through_and_fail_closed(tmp_path):
    store, run, budget = ledger(tmp_path, budget=1000)
    item = episode(store, run)
    executor = BudgetedModelExecutor(Inner(20, AITransportError("402", retryable=False, status_code=402),
                                           AITransportError("reset"), 5), budget)
    executor.execute(None, "p", request(), None)
    budget.observe(usage_event("s1", item["turnId"], 20))
    with pytest.raises(AITransportError):
        executor.execute(None, "p", request(), None)
    with pytest.raises(AITransportError):
        executor.execute(None, "p", request(), None)
    position = budget.report(run)
    assert position["knownTokens"] == 20 and position["unknownRequests"] == 1 and position["assumedUnknownTokens"] == 20
    executor.execute(None, "p", request("not-a-project-session"), None)  # ordinary chats are untouched
    store.close()
    with pytest.raises(ModelAdmissionRefused, match="ledger unavailable"):
        executor.execute(None, "p", request(), None)


def test_requests_outside_an_admitted_episode_or_after_cancel_are_refused(tmp_path):
    store, run, budget = ledger(tmp_path)
    with pytest.raises(ModelAdmissionRefused, match="no admitted episode"):
        budget.admit(request())
    episode(store, run)
    with store.transaction() as db:
        current = store._read(db, run["id"])
        current["state"] = "cancelled"
        store.save(db, current, "cancel")
    with pytest.raises(ModelAdmissionRefused, match="cancelled"):
        budget.admit(request())


def test_concurrent_admissions_cannot_both_take_the_last_room(tmp_path):
    store, run, budget = ledger(tmp_path, budget=100)
    item = episode(store, run)
    budget.observe(usage_event("s1", item["turnId"], 45))
    results = []
    def take():
        try:
            results.append(bool(budget.admit(request())))
        except ModelAdmissionRefused:
            results.append(False)
    threads = [threading.Thread(target=take) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert results.count(True) == 1  # 45 known + 45 reserved; a second reserve would exceed 100


def test_tool_contract_error_with_a_carried_response_is_known_usage_not_unknown(tmp_path):
    from app.ai.errors import AIToolContractError
    store, run, budget = ledger(tmp_path, budget=1000)
    item = episode(store, run)
    carried = AIToolContractError("schema-invalid call", response=ModelResponse(text="", usage=ModelUsage(21, 0, 21)))
    with pytest.raises(AIToolContractError):
        BudgetedModelExecutor(Inner(carried), budget).execute(None, "p", request(), None)
    budget.observe(usage_event("s1", item["turnId"], 21))  # the runtime commits the carried response
    position = budget.report(run)
    # Charged once as known; a real 2026-10-29 run was refused its last request
    # because two such responses were also assumed as unknown usage.
    assert position["knownTokens"] == 21 and position["unknownRequests"] == 0 and position["chargedTokens"] == 21
