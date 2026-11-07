"""A long goal: the Host keeps a conversation going until the work is really done."""
import json
import time

import pytest

from app.agent_runtime.durable_state import GoalStatus
from app.ai import ModelResponse, ModelUsage, ToolCall
from app.project_runner import goal, weave
import test_app_server
from test_app_server import _wait_until
from test_project_runner import build


@pytest.fixture(autouse=True)
def runtime_with_sub_agents(monkeypatch):
    """The Host's runtime has the agent tree the checker runs in."""
    from app.agent_runtime.multi_agent_runtime import MultiAgentRuntime
    monkeypatch.setattr(test_app_server, "DurableAgentRuntime", MultiAgentRuntime)


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


def conversation(tmp_path, responses):
    service, runtime, platform, _, source = build(tmp_path, responses)
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
        START, DONE, answer("1"), REJECTED, DONE, answer("2"), REJECTED, DONE, answer("3"), REJECTED])
    try:
        tick(service, source, 10)
        assert state(service, source)["status"] == "blocked" and "3 times" in state(service, source)["reason"]
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
        goal.control(service, {"threadId": source, "action": "resume"})
        assert state(service, source)["tokenBudget"] == 4_000_000, "resuming a spent goal adds to it"
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


@pytest.mark.parametrize("change", ["no time given", "time nearly up", "budget nearly used", "deepened enough"])
def test_a_pass_finishes_when_there_is_no_room_to_go_deeper(tmp_path, change):
    start = START if change == "no time given" else NIGHT
    service, runtime, _, source = conversation(tmp_path, [start, DONE, answer("做完了"), DEEPER])
    try:
        record = service.weave_goals.get(source)
        if change == "time nearly up":
            record["startedAt"] -= 7 * 3600
        elif change == "budget nearly used":
            record["baseCharged"] -= 0.8 * record["tokenBudget"]
        elif change == "deepened enough":
            record["deepens"] = goal.MAX_DEEPENS
        service.weave_goals.put(source, record)
        tick(service, source, 3)
        final = state(service, source)
        assert final["status"] == "complete" and final["checked"] is True, change
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


def test_the_guide_keeps_ordinary_turns_frugal_and_flips_in_a_goal():
    assert "in an ordinary turn" in weave.GUIDE and "In a long goal the default flips" in weave.GUIDE
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
