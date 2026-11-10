"""Execution owned by a goal must obey the same stop as its lead."""
import threading
from types import SimpleNamespace

import pytest

from app.agent_runtime.contracts import AgentStatus
from app.agent_runtime.durable_state import GoalStatus
from app.project_runner import goal, goal_lines
from app.ai.errors import AIQuotaExceeded
from test_project_goal import (
    START, PASSED, answer, conversation, tick, state, usage, runtime_with_sub_agents,
)


@pytest.mark.parametrize("stop", ["pause", "budget"])
@pytest.mark.parametrize("restarted", [False, True])
def test_stopping_during_a_check_stops_spending_and_resumes_the_same_checker(tmp_path, stop, restarted):
    service, runtime, platform, source = conversation(tmp_path, [START, answer("交付待核验")])
    entered, release = threading.Event(), threading.Event()
    original = platform.execute_chat

    def execute(profile, request):
        if any("You are an independent checker" in str(m.content) for m in request.messages):
            platform.requests.append(request)
            entered.set()
            release.wait(10)
            return PASSED
        return original(profile, request)

    platform.execute_chat = execute
    try:
        record = service.weave_goals.get(source)
        goal._spawn_checker(service, source, record, "核验交付")
        assert entered.wait(10)
        checker = record["checkerId"]
        if stop == "pause":
            goal.control(service, {"threadId": source, "action": "pause"})
        else:
            record["tokenBudget"] = 1
            service.weave_goals.put(source, record)
            goal.advance(service)
            assert runtime.get_goal(source).status is GoalStatus.BUDGET_LIMITED
        release.set()
        snapshot = runtime.agent_control.wait(source, checker, timeout_seconds=10)
        assert snapshot.session_status is AgentStatus.CANCELLED
        if restarted:
            session = runtime.store.load(checker)
            session.status = AgentStatus.RUNNING  # lost process, before its cancellation reached disk
            runtime.store.save(session)
        before = len(platform.requests)
        tick(service, source)
        assert len(platform.requests) == before
        goal.control(service, {"threadId": source, "action": "resume", "tokenBudget": 4_000_000})
        tick(service, source)
        assert service.weave_goals.get(source)["checkerId"] == checker
        tick(service, source)
        assert state(service, source)["checked"] is True
        assert service.weave_goals.get(source)["checkFaults"] == 0
    finally:
        release.set()
        runtime.close()


@pytest.mark.parametrize("contents", ["{broken", "[]", "null", '{"thread": []}'])
def test_a_corrupt_goal_store_cannot_be_silently_replaced_by_the_next_write(tmp_path, contents):
    store = goal.Goals(tmp_path)
    store.path.write_text(contents, encoding="utf-8")
    with pytest.raises(ValueError):
        store.put("new-thread", {"phase": "working"})
    assert store.path.read_text(encoding="utf-8") == contents


def test_an_unreadable_goal_store_does_not_stop_the_rest_of_the_project_scheduler():
    def broken():
        raise PermissionError("store unavailable")

    service = SimpleNamespace(weave_goals=SimpleNamespace(all=broken))
    assert goal.advance(service) is False
    assert "store unavailable" in service._goal_errors["store"]
    service.weave_goals.all = lambda: {}
    assert goal.advance(service) is False
    assert "store" not in service._goal_errors


def test_line_report_collection_failure_still_closes_the_agent(tmp_path, monkeypatch):
    closed = []
    control = SimpleNamespace(snapshot=lambda *_: SimpleNamespace(final_text="report"),
                              close_agent=lambda *args: closed.append(args))
    service = SimpleNamespace(runtime=SimpleNamespace(agent_control=control),
                              store=SimpleNamespace(load=lambda _: SimpleNamespace(usage=usage(10))))

    def broken(*_):
        raise OSError("journal unavailable")

    monkeypatch.setattr(goal_lines, "_changed", broken)
    line = {"agentId": "line", "tokens": 0}
    record = {}
    goal_lines._settle(service, "parent", record, line, "done")
    assert closed == [("parent", "line")]
    assert line["state"] == "failed" and "journal unavailable" in line["why"]
    assert record["lineStats"]["failed"] == 1


def waiting_line(used, **overrides):
    sent = []
    control = SimpleNamespace(snapshot=lambda *_: SimpleNamespace(execution_running=False, session_status=AgentStatus.FAILED),
                              send=lambda *args: sent.append(args))
    service = SimpleNamespace(runtime=SimpleNamespace(agent_control=control),
                              store=SimpleNamespace(load=lambda _: SimpleNamespace(usage=usage(used))))
    line = {"agentId": "line", "tokens": used, "cap": 100, "deadline": 10_000, "state": "waiting",
            "waitingState": "reporting", "asked": True, "pressedAt": 100, "retryAt": 101, **overrides}
    return service, line, sent


def test_a_reporting_line_does_not_resume_normal_work_after_a_provider_failure():
    service, line, sent = waiting_line(100)
    assert goal_lines._tick_line(service, "parent", {}, line, 102)
    assert line["state"] == "reporting" and line["pressedAt"] == 100
    assert sent == [("parent", "line", goal_lines.REPORT_NOW)]


@pytest.mark.parametrize("used,now", [(126, 102), (100, 101 + goal_lines.REPORT_GRACE)])
@pytest.mark.parametrize("legacy", [False, True])
def test_a_waiting_report_cannot_bypass_its_token_or_time_limit(monkeypatch, used, now, legacy):
    service, line, sent = waiting_line(used)
    if legacy:
        line.pop("waitingState")
    settled = []
    monkeypatch.setattr(goal_lines, "_settle", lambda *args: settled.append(args))
    assert goal_lines._tick_line(service, "parent", {}, line, now)
    assert settled and not sent


@pytest.mark.parametrize("failure", [RuntimeError("model restoration refused"), AIQuotaExceeded(status_code=429)])
def test_queued_model_preparation_failure_is_durable_and_never_samples_a_fallback(tmp_path, failure):
    service, runtime, platform, source = conversation(tmp_path, [
        START, answer("开始"), answer("子代理初次报告"), answer("恢复后报告")])
    try:
        control = runtime.agent_control
        child = control.spawn(source, "初次调查", background=False).node.session_id
        before = len(platform.requests)

        def refuse(_sid):
            raise failure

        runtime.prepare_session_turn = refuse
        control.send(source, child, "继续调查")
        snapshot = control.wait(source, child, timeout_seconds=10)
        assert snapshot.session_status is AgentStatus.FAILED
        assert str(failure) in snapshot.error
        assert len(platform.requests) == before, "no request may escape on the default or a stale platform"
        session = runtime.store.load(child)
        failed = [e for e in runtime.store.turn_events(child, session.current_turn_id) if e.kind.value == "turn_failed"]
        assert len(failed) == 1
        assert failed[0].data["quota_exhausted"] is isinstance(failure, AIQuotaExceeded)
        assert runtime.durable_state.pending_count(child) == 0, "the failed turn durably adopted its input"
        prepared = []

        def prepare(sid):
            prepared.append(sid)
            refreshed = runtime.store.load(sid)
            refreshed.model_vision = False
            runtime.store.save(refreshed)

        runtime.prepare_session_turn = prepare
        control.send(source, child, "修复配置后继续")
        assert control.wait(source, child, timeout_seconds=10).session_status is AgentStatus.COMPLETED
        assert prepared == [child]
        assert len(platform.requests) == before + 1
        assert runtime.store.load(child).model_vision is False, "sampling must not overwrite preparation with stale metadata"
    finally:
        runtime.close()


def test_cancel_during_queued_preparation_never_reaches_sampling(tmp_path):
    service, runtime, platform, source = conversation(tmp_path, [START, answer("开始"), answer("初次报告")])
    entered, release = threading.Event(), threading.Event()
    try:
        control = runtime.agent_control
        child = control.spawn(source, "初次调查", background=False).node.session_id
        before = len(platform.requests)

        def prepare(_sid):
            entered.set()
            assert release.wait(10)

        runtime.prepare_session_turn = prepare
        control.send(source, child, "继续")
        assert entered.wait(10)
        runtime.cancel(child)
        release.set()
        assert control.wait(source, child, timeout_seconds=10).session_status is AgentStatus.CANCELLED
        assert len(platform.requests) == before
    finally:
        release.set()
        runtime.close()
