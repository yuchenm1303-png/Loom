"""In build mode each request is sized (long goal or one turn) by the Host's gate, not by the model spotting keywords."""
import pytest

from app.agent_runtime.contracts import PermissionMode
from app.agent_runtime.durable_state import GoalStatus
from app.project_runner import goal_scope
import test_project_goal as base
from test_project_goal import answer, calling
from test_project_goal_lines import exec_tool, said_to_lead

GOAL_SCOPE_GATE = True  # conftest leaves this module's gate on


@pytest.fixture(autouse=True)
def runtime_with_sub_agents(monkeypatch):
    from app.agent_runtime.multi_agent_runtime import MultiAgentRuntime
    monkeypatch.setattr(base.test_app_server, "DurableAgentRuntime", MultiAgentRuntime)
    from app.project_runner import goal_charter
    monkeypatch.setattr(goal_charter, "MAX_HOLDS", 0)


PLAN = calling("update_plan", {"plan": [{"step": "看看", "status": "in_progress"}, {"step": "做", "status": "pending"}]},
               "call-plan")
CARRY_ON = calling("update_plan", {"plan": [{"step": "看看", "status": "in_progress"}, {"step": "做", "status": "pending"}],
                                   "explanation": "新消息接着改同一份文件里的错别字"}, "call-plan-2")
ONE_TURN = calling("weave_one_turn", {"reason": "改一个错别字，这一轮就能改完并核对"}, "call-one")


def run(tmp_path, responses, text="深入研究一下 AI-RAN，看看能做出什么成果", weave_mode=True, before=None):
    tools, runs = exec_tool()
    service, runtime, platform, _, source = base.build(tmp_path, responses, tools=tools)
    session = runtime.store.load(source)
    session.permission_mode = PermissionMode.FULL_ACCESS
    runtime.store.save(session)
    if before:
        before(service, source)
    service.turn_start({"threadId": source, "input": text, "weave": weave_mode})
    base.settle(service, source)
    return service, runtime, platform, source, runs


def held(platform, text=goal_scope.HELD):
    return sum(said.count(text[:60]) for said in said_to_lead(platform)[-1:])


def test_a_request_that_changes_things_is_sized_first_and_a_goal_takes_over(tmp_path):
    service, runtime, platform, source, runs = run(tmp_path, [
        PLAN, calling("read_it", {}, "call-read"), calling("run_it", {}, "call-run"),
        calling("weave_goal", {"objective": "深入研究 AI-RAN 并交出成果"}, "call-goal"),
        calling("run_it", {}, "call-run-2"), answer("开始了")])
    try:
        assert len(runs) == 2, "the read ran, the first change waited, the one after the goal ran"
        assert held(platform) == 1
        assert service.runtime.get_goal(source).status is GoalStatus.ACTIVE
    finally:
        runtime.close()


def test_a_quick_task_says_so_and_goes_on_without_a_goal(tmp_path):
    service, runtime, platform, source, runs = run(tmp_path, [
        PLAN, calling("run_it", {}, "call-run"), ONE_TURN, calling("run_it", {}, "call-run-2"), answer("改好了")],
        text="把 README 里的错别字改一下")
    try:
        assert len(runs) == 1 and held(platform) == 1
        assert service.weave_goals.get(source) is None
        assert goal_scope.CHOSEN in said_to_lead(platform)[-1]
    finally:
        runtime.close()


def test_reading_and_answering_are_never_held(tmp_path):
    service, runtime, platform, source, runs = run(tmp_path, [
        PLAN, calling("read_it", {}, "call-read"), answer("AI-RAN 是……")], text="AI-RAN 是什么？")
    try:
        assert len(runs) == 1 and held(platform) == 0
    finally:
        runtime.close()


def test_looking_something_up_is_not_a_change():
    assert {"web_search", "web_search_status"} <= goal_scope.LOOKUPS


def test_a_chat_turn_is_not_sized(tmp_path):
    service, runtime, platform, source, runs = run(tmp_path, [PLAN, calling("run_it", {}, "call-run"), answer("好")],
                                                   weave_mode=False)
    try:
        assert len(runs) == 1 and held(platform) == 0
    finally:
        runtime.close()


def test_a_one_turn_request_that_grows_is_asked_once_more(tmp_path, monkeypatch):
    monkeypatch.setattr(goal_scope, "GROW_AFTER", 2)
    service, runtime, platform, source, runs = run(tmp_path, [
        PLAN, ONE_TURN, *[calling("run_it", {}, f"call-run-{n}") for n in range(3)],
        calling("weave_goal", {"objective": "原来不止一轮：做完整个研究"}, "call-goal"),
        calling("run_it", {}, "call-run-after"), answer("转成长目标了")])
    try:
        assert len(runs) == 3, "two ran, the third waited for the second question, the one after the goal ran"
        assert any("has grown past a quick task (2 changing" in said for said in said_to_lead(platform))
        assert service.runtime.get_goal(source).status is GoalStatus.ACTIVE
    finally:
        runtime.close()


def test_it_gives_way_rather_than_cost_the_turn(tmp_path):
    service, runtime, platform, source, runs = run(tmp_path, [
        PLAN, *[calling("run_it", {}, f"call-run-{n}") for n in range(goal_scope.MAX_HOLDS + 1)], answer("做了")])
    try:
        assert len(runs) == 1, "held twice, then it ran"
        assert service.weave_scopes[source]["choice"] == "gave_way"
    finally:
        runtime.close()


def test_a_running_project_is_sized_already(tmp_path, monkeypatch):
    monkeypatch.setattr(goal_scope, "_project_running", lambda service, thread: True)
    service, runtime, platform, source, runs = run(tmp_path, [PLAN, calling("run_it", {}, "call-run"), answer("好")])
    try:
        assert len(runs) == 1 and held(platform) == 0
    finally:
        runtime.close()


def test_each_new_request_is_sized_again(tmp_path):
    service, runtime, platform, source, runs = run(tmp_path, [
        PLAN, ONE_TURN, calling("run_it", {}, "call-run"), answer("改好了"),
        CARRY_ON, calling("run_it", {}, "call-run-2"), ONE_TURN, calling("run_it", {}, "call-run-3"), answer("也改好了")],
        text="改个错别字")
    try:
        assert len(runs) == 1 and held(platform) == 0
        service.turn_start({"threadId": source, "input": "再改一个", "weave": True})
        base.settle(service, source)
        assert len(runs) == 2 and held(platform) == 1, "a new request: asked again, not carried over"
    finally:
        runtime.close()


def test_a_finished_goal_does_not_size_the_next_request(tmp_path):
    def finished(service, source):
        from app.agent_runtime.durable_state import GoalStatus
        from app.project_runner import goal
        goal.start(service, source, "旧的目标", 1_000_000)
        goal._finish(service, source, service.weave_goals.get(source), "做完了", True)
    service, runtime, platform, source, runs = run(tmp_path, [
        PLAN, calling("run_it", {}, "call-run"), ONE_TURN, calling("run_it", {}, "call-run-2"), answer("好")],
        before=finished)
    try:
        assert len(runs) == 1 and held(platform) == 1
    finally:
        runtime.close()


def test_one_turn_needs_a_reason():
    service = type("S", (), {"project_runner": type("R", (), {"guard": __import__("threading").Lock()})()})()
    with pytest.raises(ValueError, match="reason"):
        goal_scope.choose_one_turn(service, "s", "t", "小事")
    assert goal_scope.choose_one_turn(service, "s", "t", "只改一行配置，这一轮能改完并验证") == goal_scope.CHOSEN
    assert service.weave_scopes["s"]["choice"] == "turn"
