"""A long goal: the Host keeps a conversation going until the work is really done."""
import json
import time

import pytest

from app.agent_runtime.durable_state import GoalStatus
from app.ai import ModelResponse, ModelUsage, ToolCall
from app.project_runner import goal, goal_charter, weave
import test_app_server
from test_app_server import _wait_until
from test_project_runner import build


@pytest.fixture(autouse=True)
def runtime_with_sub_agents(monkeypatch):
    """The Host's runtime has the agent tree the checker runs in."""
    from app.agent_runtime.multi_agent_runtime import MultiAgentRuntime
    monkeypatch.setattr(test_app_server, "DurableAgentRuntime", MultiAgentRuntime)
    monkeypatch.setattr(goal_charter, "MAX_HOLDS", 0)  # the charter is asked for in its own tests; here it gives way


def usage(total=10, cached=0):
    return ModelUsage(input_tokens=total, output_tokens=0, total_tokens=total, cached_input_tokens=cached)


def answer(text, **tokens):
    return ModelResponse(text=text, usage=usage(**tokens))


def calling(name, arguments, call_id="call-1", **tokens):
    return ModelResponse(tool_calls=(ToolCall(call_id=call_id, name=name, arguments=arguments),), usage=usage(**tokens))


START = calling("weave_goal", {"objective": "研究一晚"})
DONE = calling("mark_thread_goal", {"status": "complete"}, "call-done")
PASSED = answer(json.dumps({"verdict": "passed", "summary": "跑过了，结果成立", "issues": []}))
REJECTED = answer(json.dumps({"verdict": "needs_changes", "summary": "结果不成立", "issues": ["评估脚本跑不通"]}))


def conversation(tmp_path, responses, tools=()):
    service, runtime, platform, _, source = build(tmp_path, responses, tools=tools)
    service.turn_start({"threadId": source, "input": "你有一整晚", "weave": True})
    settle(service, source)
    return service, runtime, platform, source


def settle(service, source):
    _wait_until(lambda: not service._is_active(source), timeout=10)


def tick(service, source, times=1):
    """Run the driver as the scheduler does, each move given its turn to finish."""
    for _ in range(times):
        service.__dict__.get("_goal_polled", {}).clear()
        goal.advance(service)
        settle(service, source)
        control = service.runtime.agent_control
        for snapshot in control.list_tree(source, include_closed=False):
            control.wait(source, snapshot.node.session_id, timeout_seconds=10)


def state(service, source):
    return goal.view(service, source)


def test_a_goal_keeps_going_until_a_check_agrees(tmp_path):
    service, runtime, platform, source = conversation(tmp_path, [
        START, answer("第一轮做完了"), answer("还在做", total=20), DONE, answer("做完了"), PASSED])
    try:
        assert state(service, source)["status"] == "active"
        tick(service, source)
        assert state(service, source)["turns"] == 1, "a turn that ended is not the end of the goal"
        tick(service, source)
        assert runtime.get_goal(source).status is GoalStatus.COMPLETE, "the agent's claim, read by the driver next"
        assert state(service, source)["phase"] == "checking" and state(service, source)["status"] == "active",             "a claim never shows as finished: seen in real Electron as 'the check could not be completed'"
        tick(service, source, 3)
        final = state(service, source)
        assert final["status"] == "complete" and final["checked"] is True and final["phase"] == "done"
        assert "结果成立" in final["summary"]
        before = len(platform.requests)
        tick(service, source, 2)
        assert len(platform.requests) == before, "a finished goal stays finished"
    finally:
        runtime.close()


def test_a_rejected_claim_goes_back_to_the_work_with_the_findings(tmp_path):
    service, runtime, platform, source = conversation(tmp_path, [
        START, DONE, answer("做完了"), REJECTED, answer("修好了"), DONE, answer("这次真做完了"), PASSED])
    try:
        tick(service, source, 2)
        assert state(service, source)["status"] == "active" and state(service, source)["phase"] == "working"
        tick(service, source, 5)
        assert state(service, source)["checked"] is True
        said = [json.dumps([message.content for message in request.messages], ensure_ascii=False, default=str)
                for request in platform.requests]
        assert any("评估脚本跑不通" in text for text in said), "the next turn is told what the check found"
    finally:
        runtime.close()


def test_a_check_that_keeps_rejecting_stops_for_the_person(tmp_path):
    service, runtime, _, source = conversation(tmp_path, [
        START, DONE, answer("1"), REJECTED, DONE, answer("2"), REJECTED, DONE, answer("3"), REJECTED,
        DONE, answer("4"), REJECTED])
    try:
        tick(service, source, 13)
        assert state(service, source)["status"] == "blocked" and "4 times" in state(service, source)["reason"], \
            "the same point asked again three times after the first is stuck"
    finally:
        runtime.close()


def test_resuming_a_goal_the_checker_stopped_starts_a_new_count(tmp_path):
    service, runtime, platform, source = conversation(tmp_path, [
        START, DONE, answer("1"), REJECTED, DONE, answer("2"), REJECTED, DONE, answer("3"), REJECTED,
        DONE, answer("4"), REJECTED, DONE, answer("5"), REJECTED])
    try:
        tick(service, source, 13)
        assert state(service, source)["status"] == "blocked" and state(service, source)["checks"] == 4
        goal.control(service, {"threadId": source, "action": "resume"})
        tick(service, source, 4)
        final = state(service, source)
        assert final["status"] == "active" and final["checks"] == 5, "one rejection after the person's resume is not a stop"
        said = json.dumps([message.content for message in platform.requests[-1].messages], ensure_ascii=False, default=str)
        assert "评估脚本跑不通" in said, "the resumed turn is handed the checker's points"
    finally:
        runtime.close()


def test_rejections_that_ask_for_less_each_time_do_not_stop_the_goal(tmp_path):
    def rejected(count):
        return answer(json.dumps({"verdict": "needs_changes", "summary": "还差一点",
                                  "major": [f"问题 {i}" for i in range(count)]}))
    service, runtime, _, source = conversation(tmp_path, [
        START, DONE, answer("1"), rejected(4), DONE, answer("2"), rejected(3), DONE, answer("3"), rejected(2),
        DONE, answer("4"), rejected(1), DONE, answer("5"), PASSED])
    try:
        tick(service, source, 16)
        final = state(service, source)
        assert final["status"] == "complete" and final["checked"] is True, final.get("reason")
        record = service.weave_goals.get(source)
        assert record["checks"] == 4 and record.get("rejectionStalls", 0) == 0
    finally:
        runtime.close()


def test_rejections_that_stop_shrinking_still_stop_the_goal(tmp_path):
    def rejected(count):
        return answer(json.dumps({"verdict": "needs_changes", "summary": "没改好", "major": [f"问题 {i}" for i in range(count)]}))
    service, runtime, _, source = conversation(tmp_path, [
        START, DONE, answer("1"), rejected(3), DONE, answer("2"), rejected(2), DONE, answer("3"), rejected(2),
        DONE, answer("4"), rejected(2), DONE, answer("5"), rejected(2)])
    try:
        tick(service, source, 17)
        final = state(service, source)
        assert final["status"] == "blocked" and "5 times" in final["reason"]
    finally:
        runtime.close()


def test_different_findings_each_round_are_progress_not_a_stop(tmp_path):
    # The 2026-11-09 AI-RAN goal: each review found something new, the lead fixed the last round's points, and the
    # old count (problems asked, criteria and majors added up) stopped it after five rounds as "not converging".
    def rejected(round_):
        return answer(json.dumps({"verdict": "needs_changes", "summary": f"第 {round_} 轮",
                                  "major": [f"第 {round_} 轮问题 A", f"第 {round_} 轮问题 B"], "unfixed": []}))
    service, runtime, _, source = conversation(tmp_path, [
        START, DONE, answer("1"), rejected(1), DONE, answer("2"), rejected(2), DONE, answer("3"), rejected(3),
        DONE, answer("4"), rejected(4), DONE, answer("5"), rejected(5), DONE, answer("6"), PASSED])
    try:
        tick(service, source, 19)
        final = state(service, source)
        assert final["status"] == "complete" and final["checked"] is True, final.get("reason")
        assert service.weave_goals.get(source).get("rejectionStalls", 0) == 0
    finally:
        runtime.close()


def test_the_checker_says_which_earlier_points_are_unfixed_and_only_those_count(tmp_path):
    def rejected(round_, unfixed):
        return answer(json.dumps({"verdict": "needs_changes", "summary": "还有旧问题",
                                  "major": [f"maxdiff 第 {round_} 次写法"], "unfixed": unfixed}))
    service, runtime, platform, source = conversation(tmp_path, [
        START, DONE, answer("1"), rejected(1, []), DONE, answer("2"), rejected(2, [1]), DONE, answer("3"),
        rejected(3, [1]), DONE, answer("4"), rejected(4, [1])])
    try:
        tick(service, source, 13)
        final = state(service, source)
        assert final["status"] == "blocked" and "4 times" in final["reason"], "reworded, the same point is still the same"
        said = [json.dumps([message.content for message in request.messages], ensure_ascii=False, default=str)
                for request in platform.requests]
        assert any("1. (major) maxdiff" in text and 'Add \\"unfixed\\"' in text for text in said), \
            "the checker sees the last points numbered and is asked which are unfixed"
    finally:
        runtime.close()


def test_a_checker_model_runs_the_check_and_a_broken_pick_falls_back(tmp_path, monkeypatch):
    service, runtime, _, source = conversation(tmp_path, [START, answer("0"), DONE, answer("1"), REJECTED,
                                                          DONE, answer("2"), REJECTED])
    try:
        resolved = {"broken": False}

        def resolve_model(params):
            if resolved["broken"]:
                raise ValueError("API key is required")
            return {"selection": params["selection"], "model": params["model"], "provider": "openai",
                    "base_url": "", "vision": None, "reasoning": None, "platform": service.runtime.platform,
                    "capability": None}
        monkeypatch.setattr(service, "resolve_model", resolve_model, raising=False)
        spawned = []
        control = service.runtime.agent_control
        original = control.spawn

        def spy(*args, **kwargs):
            spawned.append(kwargs.get("model"))
            return original(*args, **kwargs)
        monkeypatch.setattr(control, "spawn", spy)
        goal.control(service, {"threadId": source, "action": "set_checker_model", "selection": "saved:strong",
                               "model": "strong-1"})
        assert state(service, source)["checkerModel"] == {"selection": "saved:strong", "model": "strong-1"}
        tick(service, source, 4)
        assert spawned and spawned[0]["model"] == "strong-1", "the check runs on the picked model"
        assert runtime.store.load(source).model_selection != "saved:strong", "the conversation keeps its own model"
        resolved["broken"] = True
        tick(service, source, 4)
        assert len(spawned) >= 2 and spawned[-1] is None, "a pick that no longer resolves still gets the check done"
        assert "API key" in state(service, source)["checkerModelError"]
        resolved["broken"] = False
        goal.control(service, {"threadId": source, "action": "set_checker_model", "selection": ""})
        assert state(service, source)["checkerModel"] is None and state(service, source)["checkerModelError"] == ""
        resolved["broken"] = True
        with pytest.raises(ValueError):
            goal.control(service, {"threadId": source, "action": "set_checker_model", "selection": "saved:x", "model": "x"})
    finally:
        runtime.close()


def test_a_checker_that_gives_no_verdict_does_not_count_as_a_pass(tmp_path):
    service, runtime, _, source = conversation(tmp_path, [START, DONE, answer("做完了"), answer("没有 JSON"), answer("还是没有")])
    try:
        tick(service, source, 5)
        final = state(service, source)
        assert final["status"] == "complete" and final["checked"] is False, "shown as unchecked, not as checked"
    finally:
        runtime.close()


def test_the_last_verdict_counts_when_a_checker_gives_more_than_one():
    # A real checker's reply: a short verdict, then the full one. Reading from the first "{" to the
    # last "}" lost it, and a report with real defects went on as "could not be checked".
    reply = ('{"verdict":"needs_changes","summary":"PDF 不可用，{见下}"}\n\n'
             '{"verdict":"needs_changes","summary":"全文","issues":["公式吞掉了第 28 到 181 行"]}\n\n'
             "运行时验收记录：本轮修改的 1 个源文件还没有当前验收证据 {注}")
    assert goal._verdict(reply)["issues"] == ["公式吞掉了第 28 到 181 行"]
    assert goal._verdict('先说明 {这不是 JSON} 再给 {"verdict":"passed"}')["verdict"] == "passed"
    assert goal._verdict('{"verdict":"maybe"}') is None and goal._verdict("没有") is None


def test_a_checker_stopped_by_a_limit_is_asked_for_its_verdict_before_it_is_replaced(tmp_path):
    service, runtime, platform, source = conversation(tmp_path, [START, DONE, answer("做完了"), answer("读到一半"), PASSED])
    try:
        tick(service, source)
        checker = service.weave_goals.get(source)["checkerId"]
        session = service.store.load(checker)
        session.status = type(session.status).LIMIT_REACHED
        service.store.save(session)
        tick(service, source, 2)
        assert state(service, source)["checked"] is True
        said = json.dumps([message.content for message in platform.requests[-1].messages], ensure_ascii=False, default=str)
        assert goal.CHECK_NOW[:30] in said, "the same checker, not a new one that has seen nothing"
    finally:
        runtime.close()


@pytest.mark.parametrize("status", ["RUNNING", "WAITING_APPROVAL"])
def test_a_checker_nothing_is_running_is_replaced_not_waited_for(tmp_path, status):
    # Killing the app mid-check left the goal "checking" for good: on disk the checker was still running.
    service, runtime, _, source = conversation(tmp_path, [START, DONE, answer("做完了"), answer("读到一半"), PASSED])
    try:
        tick(service, source)
        first = service.weave_goals.get(source)["checkerId"]
        session = service.store.load(first)
        session.status = getattr(type(session.status), status)
        service.store.save(session)
        tick(service, source)
        assert service.weave_goals.get(source)["checkerId"] != first, "a new checker, not an endless wait"
        tick(service, source)
        assert state(service, source)["checked"] is True
    finally:
        runtime.close()


def test_a_goal_waiting_for_an_approval_says_so(tmp_path):
    service, runtime, _, source = conversation(tmp_path, [START, answer("0")])
    try:
        session = service.store.load(source)
        session.status = type(session.status).WAITING_APPROVAL
        service.store.save(session)
        assert state(service, source)["phase"] == "approval"
    finally:
        runtime.close()


PAUSE = calling("mark_thread_goal", {"status": "paused"}, "call-pause")


def test_the_person_asking_to_pause_stops_the_goal(tmp_path):
    # Real run: "请先暂停一下" was answered "已暂停" with no way to pause, and the next goal turn began seconds later.
    service, runtime, platform, source = conversation(tmp_path, [START, answer("0"), PAUSE, answer("已暂停"), answer("不该发生")])
    try:
        service.turn_start({"threadId": source, "input": "请先暂停一下", "weave": True})
        settle(service, source)
        assert runtime.get_goal(source).status is GoalStatus.PAUSED
        assert state(service, source)["reason"] == goal.PAUSED_ASKED
        said = json.dumps([message.content for message in platform.requests[-1].messages], ensure_ascii=False, default=str)
        assert goal.PAUSED_NOTE[:30] in said, "the lead is told it really is paused"
        before = len(platform.requests)
        tick(service, source, 3)
        assert len(platform.requests) == before, "a paused goal starts no turn"
        goal.control(service, {"threadId": source, "action": "resume"})
        tick(service, source)
        assert len(platform.requests) == before + 1, "the person's resume brings it back"
    finally:
        runtime.close()


def test_a_goal_turn_cannot_pause_its_own_goal(tmp_path):
    service, runtime, platform, source = conversation(tmp_path, [START, answer("0"), PAUSE, answer("我停了")])
    try:
        tick(service, source)
        assert runtime.get_goal(source).status is GoalStatus.ACTIVE, "no person asked; the Host keeps the goal going"
        said = json.dumps([message.content for message in platform.requests[-1].messages], ensure_ascii=False, default=str)
        assert "Only the person can pause" in said
    finally:
        runtime.close()


def test_turns_without_a_tool_stop_as_stalled(tmp_path):
    service, runtime, platform, source = conversation(tmp_path, [START, answer("0"), answer("1"), answer("2"), answer("3")])
    try:
        tick(service, source, 6)
        assert state(service, source)["status"] == "blocked" and "no progress" in state(service, source)["reason"]
        assert state(service, source)["turns"] == 2
    finally:
        runtime.close()


def test_a_tool_that_keeps_failing_is_not_progress(tmp_path):
    # Seen in real Electron: a refused mark_thread_goal, turn after turn, every three seconds until the budget.
    refused = calling("mark_thread_goal", {"status": "not-a-status"})
    service, runtime, _, source = conversation(tmp_path, [START, answer("0"), refused, answer("a"), refused, answer("b"), answer("c")])
    try:
        tick(service, source, 6)
        assert state(service, source)["status"] == "blocked" and "no progress" in state(service, source)["reason"]
        assert state(service, source)["turns"] == 2
    finally:
        runtime.close()


def test_the_budget_counts_cached_input_at_a_tenth_and_stops_the_goal(tmp_path):
    service, runtime, _, source = conversation(tmp_path, [
        calling("weave_goal", {"objective": "研究", "token_budget": 2_000_000}),
        answer("读了很多缓存", total=1_000_000, cached=1_000_000), answer("没读缓存", total=2_100_000), answer("不该发生")])
    try:
        tick(service, source)
        assert state(service, source)["status"] == "active"
        assert 90_000 < state(service, source)["tokens"] < 110_000, "a million cached tokens cost a tenth"
        tick(service, source, 2)
        assert state(service, source)["status"] == "budget_limited"
        with pytest.raises(ValueError, match="explicitly set"):
            goal.control(service, {"threadId": source, "action": "resume"})
        assert state(service, source)["tokenBudget"] == 2_000_000, "resume cannot silently add spending authority"
        goal.control(service, {"threadId": source, "action": "resume_with_budget", "tokenBudget": 3_000_000})
        assert state(service, source)["tokenBudget"] == 3_000_000
    finally:
        runtime.close()


def test_past_the_wrap_share_the_lead_is_told_to_finish_and_claim(tmp_path):
    # Real night: the budget ran out in the last verification step; the work was done, never claimed, never checked.
    service, runtime, platform, source = conversation(tmp_path, [
        calling("weave_goal", {"objective": "研究", "token_budget": 2_000_000}),
        answer("花了很多", total=1_800_000), answer("收尾")])
    try:
        assert state(service, source)["reason"] == ""
        tick(service, source)
        record = service.weave_goals.get(source)
        assert record["wrapUp"] is True
        said = " ".join(str(m.content) for m in platform.requests[-1].messages)
        assert "Start nothing new" in said, "the next goal turn says it"
    finally:
        runtime.close()


def test_a_running_turn_is_told_once_through_a_tool_result(tmp_path):
    from types import SimpleNamespace
    from app.agent_runtime.tools import ToolResult
    service, runtime, _, source = conversation(tmp_path, [START, answer("开始")])
    try:
        record = service.weave_goals.get(source)
        record.update(wrapUp=True, tokens=900, tokenBudget=1000)
        service.weave_goals.put(source, record)
        weave_session = SimpleNamespace(session_id=source, weave=True)
        first = goal.annotate(service, weave_session, ToolResult(True, "读到了", {}))
        again = goal.annotate(service, weave_session, ToolResult(True, "又读到了", {}))
        assert "Start nothing new" in first.content and "90%" in first.content
        assert again.content == "又读到了", "once"
        plain = SimpleNamespace(session_id=source, weave=False)
        record = service.weave_goals.get(source)
        record["wrapTold"] = False
        service.weave_goals.put(source, record)
        assert goal.annotate(service, plain, ToolResult(True, "x", {})).content == "x", "only build mode has goals"
    finally:
        runtime.close()


def test_the_note_reaches_a_running_goal_turn_through_the_runtime(tmp_path):
    service, runtime, platform, source = conversation(tmp_path, [
        START, answer("开始"), calling("get_thread_state", {}, "call-state"), answer("收尾了")])
    try:
        from app.project_runner.goal_common import charged
        record = service.weave_goals.get(source)
        spent = charged(service.store.load(source).usage) - record["baseCharged"]
        record["tokenBudget"] = int(spent / 0.9)  # about 90% used before the next goal turn
        assert spent / record["tokenBudget"] >= goal.WRAP_SHARE
        service.weave_goals.put(source, record)
        tick(service, source)
        results = [event.data["content"] for event in service.store.events(source)
                   if event.kind.value == "tool_completed" and event.data.get("tool") == "get_thread_state"]
        assert results and "Runtime note: Budget" in results[-1]
    finally:
        runtime.close()


def test_a_larger_budget_takes_the_wrap_up_back(tmp_path):
    service, runtime, platform, source = conversation(tmp_path, [
        calling("weave_goal", {"objective": "研究", "token_budget": 2_000_000}),
        answer("花了很多", total=1_800_000), answer("收尾"), answer("继续")])
    try:
        tick(service, source)
        assert service.weave_goals.get(source)["wrapUp"] is True
        record = service.weave_goals.get(source)
        record["tokenBudget"] = 8_000_000
        service.weave_goals.put(source, record)
        tick(service, source)
        assert not service.weave_goals.get(source)["wrapUp"]
    finally:
        runtime.close()


def test_running_out_says_which_side_of_the_claim_it_was(tmp_path):
    service, runtime, _, source = conversation(tmp_path, [
        calling("weave_goal", {"objective": "研究", "token_budget": 2_000_000}), answer("没读缓存", total=2_100_000)])
    try:
        tick(service, source)
        assert state(service, source)["status"] == "budget_limited"
        assert service.weave_goals.get(source)["reason"] == goal.OUT_WORKING
    finally:
        runtime.close()
    (tmp_path / "b").mkdir()
    service, runtime, _, source = conversation(tmp_path / "b", [START, DONE, answer("做完了")])
    try:
        record = service.weave_goals.get(source)
        record["tokenBudget"] = 1
        service.weave_goals.put(source, record)
        tick(service, source)
        assert service.weave_goals.get(source)["reason"] == goal.OUT_CHECKING
    finally:
        runtime.close()


def test_the_person_stopping_a_turn_pauses_the_goal(tmp_path):
    service, runtime, platform, source = conversation(tmp_path, [START, answer("继续"), answer("恢复后"), answer("x"), answer("y")])
    try:
        record = service.weave_goals.get(source)
        session = service.store.load(source)
        session.status = type(session.status).CANCELLED
        session.current_turn_id = "stopped"
        service.store.save(session)
        before = len(platform.requests)
        tick(service, source)
        assert state(service, source)["status"] == "paused" and len(platform.requests) == before
        goal.control(service, {"threadId": source, "action": "resume"})
        tick(service, source)
        assert state(service, source)["status"] == "active" and len(platform.requests) == before + 1
    finally:
        runtime.close()


def test_explicit_goal_budget_saves_without_resuming_and_retries_do_not_add_twice(tmp_path):
    service, runtime, _, source = conversation(tmp_path, [START, answer("暂停前")])
    try:
        goal.control(service, {"threadId": source, "action": "pause"})
        before = state(service, source)
        params = {"threadId": source, "action": "set_budget", "tokenBudget": 5_200_000,
                  "expectedTokenBudget": before["tokenBudget"], "expectedStartedAt": before["startedAt"]}
        goal.control(service, params)
        assert state(service, source)["tokenBudget"] == 5_200_000
        assert state(service, source)["status"] == "paused"
        goal.control(service, params)
        assert state(service, source)["tokenBudget"] == 5_200_000
        with pytest.raises(ValueError, match="budget changed"):
            goal.control(service, {**params, "tokenBudget": 6_000_000})
        with pytest.raises(ValueError, match="goal changed"):
            goal.control(service, {**params, "expectedStartedAt": before["startedAt"] + 1})
        goal.control(service, {"threadId": source, "action": "resume_with_budget", "tokenBudget": 5_200_000})
        assert state(service, source)["status"] == "active"
        assert state(service, source)["tokenBudget"] == 5_200_000
    finally:
        runtime.close()


@pytest.mark.parametrize("budget", [True, 0, -1, 1.5, "4000000", 1_000_000_001])
def test_invalid_goal_budgets_do_not_change_the_saved_cap(tmp_path, budget):
    service, runtime, _, source = conversation(tmp_path, [START, answer("准备好了")])
    try:
        before = state(service, source)["tokenBudget"]
        with pytest.raises(ValueError):
            goal.control(service, {"threadId": source, "action": "set_budget", "tokenBudget": budget})
        assert state(service, source)["tokenBudget"] == before
    finally:
        runtime.close()


def test_a_failed_turn_waits_and_tries_again(tmp_path):
    service, runtime, platform, source = conversation(tmp_path, [START, answer("0"), answer("重试了")])
    try:
        session = service.store.load(source)
        session.status = type(session.status).FAILED
        session.current_turn_id = "failed"
        session.error = "connection reset"
        service.store.save(session)
        before = len(platform.requests)
        tick(service, source)
        record = service.weave_goals.get(source)
        assert record["failures"] == 1 and record["retryAt"] > time.time() and len(platform.requests) == before
        record["retryAt"] = 0
        service.weave_goals.put(source, record)
        tick(service, source)
        assert len(platform.requests) == before + 1
    finally:
        runtime.close()


def test_a_goal_survives_a_new_driver_and_its_prompt_is_not_shown_as_the_persons(tmp_path):
    service, runtime, _, source = conversation(tmp_path, [START, answer("0"), answer("1"), answer("2")])
    try:
        assert goal.Goals(service.project_store.home).get(source)["phase"] == "working"
        tick(service, source)
        from app.app_server import _event_item_identity
        said = [event for event in runtime.store.events(source) if event.kind.value == "user_message"]
        assert [event.data["source"] for event in said] == ["user", "goal"]
        assert [_event_item_identity(event) is not None for event in said] == [True, False]
    finally:
        runtime.close()


def without_git(service, source):
    import os, shutil, stat
    folder = f"{service.store.load(source).workspace_dir}/.git"
    shutil.rmtree(folder, onerror=lambda func, path, _: (os.chmod(path, stat.S_IWRITE), func(path)))


def test_a_goal_starts_in_a_folder_with_no_repository(tmp_path):
    service, runtime, _, source = conversation(tmp_path, [answer("0"), START, answer("1"), answer("2")])
    try:
        without_git(service, source)
        service.turn_start({"threadId": source, "input": "开始", "weave": True})
        settle(service, source)
        assert state(service, source)["status"] == "active"
    finally:
        runtime.close()


def test_a_folder_that_cannot_start_a_project_says_so_when_proposing(tmp_path):
    service, runtime, _, source = conversation(tmp_path, [answer("0"), calling("weave_propose", {"objective": "造"}), answer("1")])
    try:
        without_git(service, source)
        service.turn_start({"threadId": source, "input": "提议", "weave": True})
        settle(service, source)
        assert service.weave_proposals.get(source) is None
        failed = [event for event in runtime.store.events(source) if event.kind.value == "tool_failed"]
        assert "weave_goal" in failed[-1].data["content"]
    finally:
        runtime.close()


def test_a_project_checkout_is_not_a_project_and_new_conversations_go_to_the_repository(tmp_path):
    service, runtime, _, source = conversation(tmp_path, [answer("0")])
    try:
        run = service.project_run_create({"operationId": "x", "threadId": source, "objective": "造", "tokenBudget": 1000, "maxEpisodes": 2})
        checkout = service.project_store.home / "workspaces" / run["id"]
        checkout.mkdir(parents=True)
        opened = service.thread_start({"workspace": str(checkout), "permissionMode": "workspace"})["thread"]
        assert opened["workspace"] == str(service.store.load(source).workspace_dir)
        assert not service._internal_workspace(opened["workspace"]) and service._internal_workspace(checkout)
    finally:
        runtime.close()


def test_the_overview_carries_the_goal_and_the_scheduler_drives_it(tmp_path):
    service, runtime, _, source = conversation(tmp_path, [START, answer("0"), answer("1"), answer("2")])
    try:
        assert service.project_overview({"threadId": source})["goal"]["status"] == "active"
        service.project_runner.tick()
        settle(service, source)
        assert state(service, source)["turns"] == 1
        assert goal.control(service, {"threadId": source, "action": "dismiss"})["goal"] is None
        with pytest.raises(ValueError):
            goal.control(service, {"threadId": source, "action": "pause"})
    finally:
        runtime.close()


def failing(platform, *errors):
    """The provider raises each error in turn before the scripted answers resume."""
    pending, original = list(errors), platform.execute_chat

    def execute(profile, request):
        if pending:
            platform.requests.append(request)
            raise pending.pop(0)
        return original(profile, request)
    platform.execute_chat = execute


def busy():
    from app.ai.errors import AITransportError
    return AITransportError("Error code: 429 - upstream out of capacity", retryable=True, status_code=429)


def fresh(tmp_path, responses):
    from app.agent_runtime.contracts import AgentLimits
    service, runtime, platform, _, source = build(tmp_path, responses)
    runtime.limits = AgentLimits(transport_retries=0)
    return service, runtime, platform, source


def test_a_first_turn_the_provider_refused_runs_again_and_starts_the_goal(tmp_path):
    # Real Electron, real model: a 429 on the first turn ended it, and no goal ever started.
    service, runtime, platform, source = fresh(tmp_path, [START, answer("开始了")])
    failing(platform, busy())
    try:
        service.turn_start({"threadId": source, "input": "你有一整晚", "weave": True})
        settle(service, source)
        record = service.weave_retries.get(source)
        assert record and record["attempts"] == 1 and record["retryAt"] > time.time()
        before = len(platform.requests)
        tick(service, source)
        assert len(platform.requests) == before, "it waits before trying again"
        record["retryAt"] = 0
        service.weave_retries.put(source, record)
        tick(service, source)
        assert state(service, source)["status"] == "active", "the retried turn started the goal"
        said = [event for event in runtime.store.events(source) if event.kind.value == "user_message"]
        assert len(said) == 1, "the same message is tried again, not sent twice"
        tick(service, source)
        assert service.weave_retries.get(source) is None, "a turn that went through ends the retries"
    finally:
        runtime.close()


def test_a_provider_that_keeps_refusing_is_tried_again_with_a_longer_wait(tmp_path):
    service, runtime, platform, source = fresh(tmp_path, [answer("好了")])
    failing(platform, busy(), busy(), busy())
    try:
        service.turn_start({"threadId": source, "input": "你有一整晚", "weave": True})
        settle(service, source)
        first = service.weave_retries.get(source)
        service.weave_retries.put(source, {**first, "retryAt": 0})
        tick(service, source)
        second = service.weave_retries.get(source)
        assert second["attempts"] == 2 and second["since"] == first["since"]
        assert second["retryAt"] - time.time() > goal.RETRY_BASE * 1.5, "the wait doubles"
        service.weave_retries.put(source, {**second, "since": time.time() - goal.RETRY_WINDOW - 1, "retryAt": 0})
        tick(service, source)
        assert service.weave_retries.get(source) is None, "after a whole night of refusals it is the person's call"
        before = len(platform.requests)
        tick(service, source)
        assert len(platform.requests) == before
        service.turn_start({"threadId": source, "input": "再试一次", "weave": True})
        settle(service, source)
        assert service.store.load(source).final_text == "好了" and service.weave_retries.get(source) is None
    finally:
        runtime.close()


def test_a_new_message_after_a_retry_went_through_gets_its_own_wait(tmp_path):
    service, runtime, platform, source = fresh(tmp_path, [answer("第一条好了")])
    failing(platform, busy())
    try:
        service.turn_start({"threadId": source, "input": "第一条", "weave": True})
        settle(service, source)
        record = service.weave_retries.get(source)
        service.weave_retries.put(source, {**record, "since": time.time() - goal.RETRY_WINDOW + 5, "retryAt": 0})
        tick(service, source)
        assert service.weave_retries.get(source) is None, "a turn that went through ends the retries"
        failing(platform, busy())
        service.turn_start({"threadId": source, "input": "第二条", "weave": True})
        settle(service, source)
        fresh_wait = service.weave_retries.get(source)
        assert fresh_wait and fresh_wait["attempts"] == 1 and time.time() - fresh_wait["since"] < 60
    finally:
        runtime.close()


def test_a_refusal_that_waiting_cannot_fix_is_not_retried(tmp_path):
    from app.ai.errors import AIQuotaExceeded
    service, runtime, platform, source = fresh(tmp_path, [])
    failing(platform, AIQuotaExceeded(status_code=429))
    try:
        service.turn_start({"threadId": source, "input": "你有一整晚", "weave": True})
        settle(service, source)
        assert service.weave_retries.get(source) is None, "an exhausted quota needs the person"
    finally:
        runtime.close()


def test_a_chat_turn_or_a_person_who_moved_on_is_not_retried(tmp_path):
    service, runtime, platform, source = fresh(tmp_path, [answer("聊天回复")])
    failing(platform, busy())
    try:
        service.turn_start({"threadId": source, "input": "你好", "weave": True})
        settle(service, source)
        record = service.weave_retries.get(source)
        service.weave_retries.put(source, {**record, "turn": "an-older-turn", "retryAt": 0})
        before = len(platform.requests)
        tick(service, source)
        assert len(platform.requests) == before and service.weave_retries.get(source) is None
    finally:
        runtime.close()


def test_a_goal_waits_out_a_provider_outage_instead_of_counting_it_as_failures(tmp_path):
    service, runtime, platform, source = conversation(tmp_path, [START, answer("恢复了")])
    from app.agent_runtime.contracts import AgentLimits
    runtime.limits = AgentLimits(transport_retries=0)
    failing(platform, *[busy() for _ in range(goal.MAX_FAILURES + 2)])
    try:
        for _ in range(2 * (goal.MAX_FAILURES + 2)):  # each failure: launch, then settle
            record = service.weave_goals.get(source)
            service.weave_goals.put(source, {**record, "retryAt": 0})
            tick(service, source)
        record = service.weave_goals.get(source)
        assert state(service, source)["status"] == "active", record["reason"]
        assert record["failures"] > goal.MAX_FAILURES and state(service, source)["retryAt"] > time.time()
        failing(platform, busy())
        service.weave_goals.put(source, {**record, "retryAt": 0, "failingSince": time.time() - goal.RETRY_WINDOW - 1})
        tick(service, source, 2)
        assert state(service, source)["status"] == "blocked", "a whole night without a provider goes to the person"
    finally:
        runtime.close()


def test_a_goal_waits_out_an_empty_quota_and_says_it_is_the_quota(tmp_path):
    # A real night: the account's quota ran out, the goal burned its eight failures in twenty minutes and stopped,
    # and the hub said "provider unavailable" over a balance only the person can refill.
    from app.agent_runtime.contracts import AgentLimits
    from app.ai.errors import AIQuotaExceeded
    service, runtime, platform, source = conversation(tmp_path, [START, answer("额度回来了")])
    runtime.limits = AgentLimits(transport_retries=0)
    failing(platform, *[AIQuotaExceeded(status_code=429) for _ in range(goal.MAX_FAILURES + 2)])
    try:
        for _ in range(2 * (goal.MAX_FAILURES + 2)):
            record = service.weave_goals.get(source)
            service.weave_goals.put(source, {**record, "retryAt": 0})
            tick(service, source)
        record = service.weave_goals.get(source)
        assert state(service, source)["status"] == "active", record["reason"]
        assert record["failures"] > goal.MAX_FAILURES and record["retryKind"] == "quota"
        shown = goal.view(service, source)
        assert shown["retryKind"] == "quota" and shown["retryAt"] > time.time()
        service.weave_goals.put(source, {**record, "retryAt": 0})
        tick(service, source, 2)
        assert service.weave_goals.get(source)["retryKind"] == "", "a turn that went through clears it"
    finally:
        runtime.close()


def test_the_person_sending_again_while_a_retry_waits_restarts_the_wait(tmp_path):
    service, runtime, platform, source = fresh(tmp_path, [])
    failing(platform, busy(), busy(), busy())
    try:
        service.turn_start({"threadId": source, "input": "第一条", "weave": True})
        settle(service, source)
        first = service.weave_retries.get(source)
        service.weave_retries.put(source, {**first, "retryAt": 0})
        tick(service, source)  # the Host's retry fails too: the same wait goes on
        assert service.weave_retries.get(source)["attempts"] == 2
        service.turn_start({"threadId": source, "input": "第二条", "weave": True})
        settle(service, source)
        mine = service.weave_retries.get(source)
        assert mine["attempts"] == 1 and mine["turn"] == service.store.load(source).current_turn_id, \
            "the person's own message is not the Host's third attempt"
    finally:
        runtime.close()


NIGHT = calling("weave_goal", {"objective": "研究一晚", "hours": 8})
DEEPER = answer(json.dumps({"verdict": "passed", "summary": "结论成立", "issues": [], "deeper": ["再加一组消融实验"]}))


def said_in(platform, start=0):
    return [json.dumps([message.content for message in request.messages], ensure_ascii=False, default=str)
            for request in platform.requests[start:]]


def test_a_correct_result_with_most_of_the_night_left_goes_deeper(tmp_path):
    # Real run: given a whole night, the model declared the goal done after about an hour.
    service, runtime, platform, source = conversation(tmp_path, [
        NIGHT, DONE, answer("做完了"), DEEPER, answer("加了消融"), DONE, answer("这次做深了"), PASSED])
    try:
        assert service.weave_goals.get(source)["hours"] == 8
        tick(service, source, 2)
        record = service.weave_goals.get(source)
        assert record["deepens"] == 1 and record["phase"] == "working", "a pass with time left is not the end"
        assert state(service, source)["status"] == "active"
        checker = [text for text in said_in(platform) if "You are an independent checker" in text]
        assert checker and "Besides the verdict" in checker[0] and "8 h" in checker[0], "the checker is told the time"
        before = len(platform.requests)
        tick(service, source)
        assert any("再加一组消融实验" in text and "h the person gave" in text for text in said_in(platform, before))
        tick(service, source, 4)
        final = state(service, source)
        assert final["status"] == "complete" and final["checked"] is True and final["deepens"] == 1
    finally:
        runtime.close()


@pytest.mark.parametrize("change", ["no time and deepened enough", "no time and budget nearly used", "time nearly up",
                                    "budget nearly used", "deepened enough"])
def test_a_pass_finishes_when_there_is_no_room_to_go_deeper(tmp_path, change):
    start = START if change.startswith("no time") else NIGHT
    service, runtime, _, source = conversation(tmp_path, [start, DONE, answer("做完了"), DEEPER])
    try:
        record = service.weave_goals.get(source)
        if change == "time nearly up":
            record["startedAt"] -= 7 * 3600
        elif change.endswith("budget nearly used"):
            record["baseCharged"] -= 0.8 * record["tokenBudget"]
        elif change == "deepened enough":
            record["deepens"] = goal.MAX_DEEPENS
        elif change == "no time and deepened enough":
            record["deepens"] = goal.NO_TIME_DEEPENS
        service.weave_goals.put(source, record)
        tick(service, source, 3)
        final = state(service, source)
        assert final["status"] == "complete" and final["checked"] is True, change
    finally:
        runtime.close()


def test_with_no_time_given_a_sound_result_with_budget_left_still_goes_deeper(tmp_path):
    # Real "继续吧" goal: no time given, three quarters of the budget left, and no revision round could ever start.
    service, runtime, platform, source = conversation(tmp_path, [START, DONE, answer("做完了"), DEEPER, answer("加了消融")])
    try:
        tick(service, source, 2)
        record = service.weave_goals.get(source)
        assert record["deepens"] == 1 and record["phase"] == "working"
        checker = [text for text in said_in(platform) if "You are an independent checker" in text]
        assert checker and "No time was given" in checker[0] and "of its token budget remains" in checker[0]
        tick(service, source)
        assert any("再加一组消融实验" in text and "of the token budget is left" in text for text in said_in(platform))
    finally:
        runtime.close()


def test_a_goal_turn_tells_the_lead_to_delegate_and_names_its_open_sub_agents(tmp_path):
    service, runtime, platform, source = conversation(tmp_path, [
        NIGHT, answer("开始了"), answer("文献调研完成"), answer("在整合")])
    try:
        tool_result = [m for m in service.store.load(source).messages if m.role.value == "tool"]
        assert any("weave_lines" in str(m.content) for m in tool_result), "weave_goal says so at once"
        control = service.runtime.agent_control
        child = control.spawn(source, "调研文献", role="survey", history_mode="none", background=True)
        control.wait(source, child.node.session_id, timeout_seconds=10)
        assert state(service, source)["agents"] == 0, "a finished sub-agent is not working"
        before = len(platform.requests)
        tick(service, source)
        prompts = [str(m.content) for m in platform.requests[before].messages
                   if m.role.value == "user" and "Continue pursuing this durable Loom goal" in str(m.content)]
        text = prompts[-1]
        assert "weave_lines" in text and f"survey {child.node.session_id}" in text
        assert "report not yet closed" in text and "h the person gave" in text
        control.close_agent(source, child.node.session_id)
        assert goal._agents_line(service, source, service.weave_goals.get(source)) == "No sub-agent is open."
    finally:
        runtime.close()


def test_the_guide_keeps_ordinary_turns_frugal_and_says_how_a_goal_splits():
    assert "in an ordinary turn" in weave.GUIDE and "the Host asks whether to split it into lines" in weave.GUIDE
    assert "default flips" not in weave.GUIDE, "a goal starts alone now; the old line said the opposite"
    # Real morning: a new direction in a conversation whose goal had run out was done in one short turn.
    assert "new direction and no long goal is running" in weave.GUIDE
    hours = next(tool for tool in weave.tools(type("S", (), {})()) if tool.name == "weave_goal").input_schema
    assert "hours" in hours["properties"]


def test_the_hub_counts_the_sub_agents_working_for_a_goal(tmp_path):
    import threading
    service, runtime, platform, source = conversation(tmp_path, [NIGHT, answer("开始了"), answer("调研完了")])
    release, original = threading.Event(), platform.execute_chat

    def execute(profile, request):
        if "慢慢调研" in json.dumps([m.content for m in request.messages], ensure_ascii=False, default=str):
            release.wait(10)
        return original(profile, request)
    platform.execute_chat = execute
    try:
        control = service.runtime.agent_control
        child = control.spawn(source, "慢慢调研", role="survey", history_mode="none", background=True)
        _wait_until(lambda: state(service, source)["agents"] == 1, timeout=10)
        release.set()
        control.wait(source, child.node.session_id, timeout_seconds=10)
        assert state(service, source)["agents"] == 0
    finally:
        release.set()
        runtime.close()


def test_time_from_the_person_starts_a_goal_first():
    # Live run 2026-11-07: told "you have two hours, I'm out", the model did it all in one turn in 30 minutes,
    # no goal, no check, no sub-agents: the guide only asked for a goal for "open-ended" work.
    assert "gives you time" in weave.GUIDE and "in your first response" in weave.GUIDE
    tool = next(tool for tool in weave.tools(type("S", (), {})()) if tool.name == "weave_goal")
    assert "Call it first whenever the person gives you time" in tool.description


def test_the_checker_is_told_what_the_lead_dropped(tmp_path):
    # Real run: training and evaluation left the list before the claim; the check never saw them.
    proof = [{"call_id": "call-1"}]
    first = {"plan": [{"step": "冒烟", "status": "in_progress"}, {"step": "训练 3000 步", "status": "pending"},
                      {"step": "配对评估", "status": "pending"}]}
    second = {"plan": [{"step": "冒烟", "status": "completed", "outcome": "passed", "evidence_refs": proof},
                       {"step": "结论", "status": "completed", "outcome": "passed", "evidence_refs": proof}],
              "explanation": "冒烟结果为负，不再训练"}
    service, runtime, platform, source = conversation(tmp_path, [
        START, calling("update_plan", first, "call-p1"), calling("update_plan", second, "call-p2"), DONE,
        answer("交给检查"), PASSED])
    try:
        tick(service, source, 2)
        said = [" ".join(str(m.content) for m in request.messages) for request in platform.requests]
        checker = [text for text in said if "You are an independent checker" in text]
        assert checker, "the check ran"
        assert "dropped these steps before they were finished" in checker[0]
        assert "训练 3000 步" in checker[0] and "冒烟结果为负，不再训练" in checker[0]
    finally:
        runtime.close()


def test_a_checker_of_a_list_that_dropped_nothing_hears_nothing_about_it(tmp_path):
    service, runtime, platform, source = conversation(tmp_path, [START, DONE, answer("交给检查"), PASSED])
    try:
        tick(service, source, 2)
        said = [" ".join(str(m.content) for m in request.messages) for request in platform.requests]
        checker = [text for text in said if "You are an independent checker" in text]
        assert checker and "dropped these steps" not in checker[0]
    finally:
        runtime.close()

def test_overview_exposes_independent_checker_without_a_project_run(tmp_path):
    service, runtime, _, source = conversation(tmp_path, [START, answer("已经提交"), PASSED])
    try:
        record = service.weave_goals.get(source)
        goal._spawn_checker(service, source, record, "独立检查报告")
        checker = service.weave_goals.get(source)["checkerId"]
        runtime.agent_control.wait(source, checker, timeout_seconds=10)
        overview = service.project_overview({"threadId": source})
        assert overview["run"] is None and overview["agents"] == []
        agent = next(item for item in overview["conversationAgents"] if item["id"] == checker)
        assert agent["role"] == "goal-check" and agent["parentId"] == source
        assert agent["state"] == "completed" and not agent["executionRunning"]
        runtime.agent_control.close_agent(source, checker)
        assert service.project_overview({"threadId": source})["conversationAgents"] == []
    finally:
        runtime.close()


def test_conversation_agent_overview_excludes_siblings_but_includes_nested_children(tmp_path):
    service, runtime, _, source = conversation(tmp_path, [START, answer("开始"), answer("调查"), answer("复核"), answer("旁支")])
    try:
        control = runtime.agent_control
        first = control.spawn(source, "调查", role="survey", history_mode="none", background=True).node.session_id
        control.wait(source, first, timeout_seconds=10)
        child = control.spawn(first, "复核", role="reviewer", history_mode="none", background=True).node.session_id
        control.wait(source, child, timeout_seconds=10)
        sibling = control.spawn(source, "旁支", role="worker", history_mode="none", background=True).node.session_id
        control.wait(source, sibling, timeout_seconds=10)
        assert {item["id"] for item in service._conversation_agent_views(first)["conversationAgents"]} == {child}
        assert {item["id"] for item in service._conversation_agent_views(source)["conversationAgents"]} == {first, child, sibling}
    finally:
        runtime.close()
