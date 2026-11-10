"""A research goal's judgment (direction stage, mid-course referee) and the fixes from a real six-hour run
(thread 262978b8, 2026-11-10): a lead left idle after its own turn failed, a question nobody waited for, a finish
review asked three times, a card that read 0/11 all along."""
import json
import threading
import time

import pytest

from app.agent_runtime.contracts import AgentEventKind as E, AgentStatus
from app.ai import ModelResponse, ToolCall
from app.project_runner import goal, goal_charter, goal_depth, goal_lines, weave
import test_project_goal as base
from test_project_goal import answer, calling, settle, state

GOAL_DEPTH = True  # tests/conftest.py turns the direction stage and the referee off everywhere else


@pytest.fixture(autouse=True)
def runtime_with_sub_agents(monkeypatch):
    from app.agent_runtime import turn_plan
    from app.agent_runtime.multi_agent_runtime import MultiAgentRuntime
    monkeypatch.setattr(base.test_app_server, "DurableAgentRuntime", MultiAgentRuntime)
    monkeypatch.setattr(turn_plan, "MAX_HOLDS", 0)
    monkeypatch.setattr(goal_charter, "MAX_HOLDS", 0)
    monkeypatch.setattr(goal_charter, "NOTE_HOLDS", 0)
    monkeypatch.setattr(goal, "MAX_STALLS", 99)  # scripted leads answer without tools while referees work


CHARTER = {"deliverable": "一份可以给审稿人评判的研究稿：问题与动机、相关工作与新意、方法、与公平基线的对比实验（含消融和不确定度）、局限，"
                          "附上能复现每个数字的代码和数据。",
           "criteria": ["相关工作对比至少覆盖五篇近年论文，并说明新意在哪里", "主结果与两个公平基线对比，给出置信区间",
                        "一条命令复现文中所有数字"],
           "phases": ["文献与选题", "主实验", "写作与自审"], "kind": "research"}


def candidate(at, piloted=False, **extra):
    value = {"title": f"方向{at}", "question": f"第 {at} 个研究问题是否成立", "closest_work": [f"arXiv:2501.0000{at} 最接近的工作"],
             "new": f"与最接近工作相比，第 {at} 个方向新在哪里", "pilot": f"十分钟小实验验证方向 {at}",
             "signal": f"小实验误块率下降超过百分之十则保留方向 {at}", "kill": f"小实验没有任何差异则放弃方向 {at}", **extra}
    if piloted:
        value["pilot_result"] = f"方向 {at} 的小实验：误块率从 0.031 降到 0.02{at}"
    return value


NAMED = calling("weave_directions", {"candidates": [candidate(i) for i in (1, 2, 3)]}, "call-named")
PILOTED = calling("weave_directions", {"candidates": [candidate(i, True) for i in (1, 2, 3)]}, "call-piloted")
RANKED = answer("比较完毕。" + json.dumps({"summary": "方向 2 最有价值", "against": {"1": "已有工作做过", "2": "样本偏小", "3": "太难"},
                                       "pairs": [{"a": 1, "b": 2, "better": 2, "why": "更新"}], "ranking": [2, 1, 3],
                                       "advice": "多种子，加大样本"}, ensure_ascii=False))
REVIEWED = answer(json.dumps({"summary": "中途看了一遍：结果成立但证据单薄", "closest_work": ["Foo 2024: 已经做过 BER 与 BLER 的区分"],
                              "reject": ["命题 1 是符号不变性，属于显然结论", "只有一个随机种子"],
                              "requests": ["主结果用三个随机种子重跑并给出置信区间", "与 Foo 2024 的方法在相同数据上对比"]},
                             ensure_ascii=False))
CLAIMS = {"claims": [{"claim": "主结果误块率 0.012", "command": "python eval.py --seed 0", "output": "results/eval.json"}]}


class Router:
    """The scripted model per role: the lead, the direction referee, the mid-course referee and the checker each
    answer from their own script, whatever order they run in."""

    def __init__(self, platform, lead, direction=(), review=(), checker=()):
        self.platform, self.lock = platform, threading.Lock()
        self.scripts = {"lead": list(lead), "direction": list(direction), "review": list(review), "checker": list(checker)}
        platform.responses = []
        platform.execute_chat = self.execute

    def execute(self, _profile, request):
        said = " ".join(str(message.content) for message in request.messages)
        role = ("direction" if "independent research advisor" in said else "review" if "senior referee" in said
                else "checker" if "independent checker" in said else "lead")
        with self.lock:
            self.platform.requests.append(request)
            script = self.scripts[role]
            return script.pop(0) if script else answer(f"{role} 没有更多内容")


def run_tool():
    from app.agent_runtime.contracts import ToolEffect
    from app.agent_runtime.tools import AgentTool, ToolResult
    runs = []
    tool = AgentTool(name="run_it", description="Run something.", input_schema={
        "type": "object", "properties": {}, "additionalProperties": True},
        handler=lambda _context, arguments: (runs.append(arguments), ToolResult(ok=True, content="ran", data={}))[1],
        effect=ToolEffect.MUTATING)
    return tool, runs


def research(tmp_path, lead, direction=(), review=(), checker=()):
    from app.agent_runtime.contracts import AgentLimits
    from app.agent_runtime.permissions import PermissionMode
    tool, runs = run_tool()
    service, runtime, platform, _, source = base.build(tmp_path, [], tools=(tool,))
    runtime.limits = AgentLimits(transport_retries=0, model_retries=0)
    router = Router(platform, lead, direction, review, checker)
    session = runtime.store.load(source)
    session.permission_mode = PermissionMode.FULL_ACCESS
    runtime.store.save(session)
    service.turn_start({"threadId": source, "input": "深入研究一下，看看能不能发论文，你有六个小时", "weave": True})
    settle(service, source)
    return service, runtime, platform, source, router, runs


def tick(service, source, times=1):
    for _ in range(times):
        service.__dict__.get("_goal_polled", {}).clear()
        goal.advance(service)
        settle(service, source)
        control = service.runtime.agent_control
        for snapshot in control.list_tree(source, include_closed=False):
            control.wait(source, snapshot.node.session_id, timeout_seconds=10)


def said(platform, start=0):
    return [json.dumps([str(message.content) for message in request.messages], ensure_ascii=False)
            for request in platform.requests[start:]]


def tool_results(service, source, name):
    return [event.data.get("content", "") for event in service.store.events(source)
            if event.kind in {E.TOOL_COMPLETED, E.TOOL_FAILED} and event.data.get("tool") == name]


START = calling("weave_goal", {"objective": "深入研究 AI-RAN，产出可投稿的证据与论文草稿", "hours": 6, **CHARTER}, "call-goal")


# --- the direction stage --------------------------------------------------------------------------------------------

def test_a_research_goal_holds_changing_work_until_directions_are_named_then_lets_pilots_run(tmp_path):
    service, runtime, _, source, _, runs = research(tmp_path, [
        START, calling("run_it", {"step": "直接开干"}, "call-early"), NAMED,
        calling("run_it", {"step": "试点"}, "call-pilot"), answer("试点中")])
    try:
        held = tool_results(service, source, "run_it")[0]
        assert "no direction has been chosen" in held and "weave_directions" in held
        assert runs == [{"step": "试点"}], "named directions open the pilots; nothing ran before them"
        assert service.weave_goals.get(source)["directions"]["state"] == "declared"
        assert "Pilot each one cheaply" in tool_results(service, source, "weave_directions")[0]
    finally:
        runtime.close()


@pytest.mark.parametrize("change,expected", [
    ({"candidates": [candidate(1), candidate(2)]}, "3 to 5"),
    ({"candidates": [candidate(1), candidate(2), {**candidate(3), "closest_work": []}]}, "closest_work"),
    ({"candidates": [candidate(1), candidate(2), candidate(3, existing=["results/calib_main.json"])]}, "limitations"),
    ({"candidates": [candidate(1), candidate(1), candidate(3)]}, "different"),
    ({"skip_reason": "不需要"}, "fixed the direction"),
])
def test_directions_must_be_real_choices(tmp_path, change, expected):
    service, runtime, _, source, _, _ = research(tmp_path, [START, answer("先这样")])
    try:
        with pytest.raises(ValueError, match=expected):
            goal_depth.set_directions(service, source, change)
    finally:
        runtime.close()


def test_piloted_directions_are_ranked_by_a_referee_whose_ranking_reaches_the_lead(tmp_path):
    service, runtime, platform, source, router, runs = research(tmp_path, [
        START, NAMED, PILOTED, answer("等排名"),
        answer("referee 在排名，我继续试点"),  # the goal turn that runs beside the referee
        # the next goal turn is handed the ranking; choosing against it needs a reason
        calling("weave_directions", {"choose": 3}, "call-choose-3"),
        calling("weave_directions", {"choose": 2, "reason": "听裁判的"}, "call-choose-2"),
        calling("run_it", {"step": "主实验"}, "call-main"), answer("开始主实验")], direction=[RANKED])
    try:
        assert service.weave_goals.get(source)["directions"]["state"] == "ranking"
        tick(service, source)  # spawns the referee, which answers; the lead works on beside it
        tick(service, source)  # reads its ranking, and brings the lead back with it
        record = service.weave_goals.get(source)
        assert record["directions"]["state"] == "chosen" and record["directions"]["chosen"] == 2
        assert record["directions"]["ranking"]["ranking"] == [2, 1, 3]
        refused = tool_results(service, source, "weave_directions")
        assert any("needs a reason" in text for text in refused), "against the ranking only with a reason"
        heard = [text for text in said(platform) if "ranked your research directions" in text]
        assert heard and "已有工作做过" in heard[0], "the ranking and the case against each reach the lead"
        lead_requests = [text for text in said(platform) if "independent research advisor" not in text]
        assert lead_requests[-1].count("ranked your research directions") == 1, "given once, not again in a tool result"
        assert runs == [{"step": "主实验"}]
        advisor = [request for request in platform.requests if "independent research advisor" in str(request.messages)]
        assert advisor and "方向 2 的小实验" in str(advisor[0].messages), "the referee is given the pilot results"
        shown = goal.view(service, source)["directions"]
        assert shown["state"] == "chosen" and shown["chosen"] == 2 and shown["candidates"][1] == "方向2"
    finally:
        runtime.close()


def test_a_finding_reaches_a_lead_in_the_middle_of_a_turn_inside_its_next_tool_result_once(tmp_path):
    from app.agent_runtime.tools import ToolResult
    service, runtime, _, source, _, _ = research(tmp_path, [START, answer("好")])
    try:
        record = service.weave_goals.get(source)
        goal_depth._tell(record, "裁判的排名：方向 2 第一")
        service.weave_goals.put(source, record)
        session = service.store.load(source)
        first = goal_depth.annotate(service, session, ToolResult(True, "ran", {}))
        second = goal_depth.annotate(service, session, ToolResult(True, "ran", {}))
        assert "裁判的排名" in first.content and first.content.startswith("ran") and second.content == "ran"
    finally:
        runtime.close()


def test_a_research_claim_without_a_direction_goes_back_once_then_reaches_the_checker_with_the_gap(tmp_path, monkeypatch):
    monkeypatch.setattr(goal_depth, "MAX_HOLDS", 0)  # the hold gave way; the claim still asks
    service, runtime, platform, source, _, _ = research(tmp_path, [
        START, calling("web_search_stub", {}, "call-x"), calling("weave_claims", CLAIMS, "call-claims"),
        calling("mark_thread_goal", {"status": "complete"}, "call-done"), answer("做完了")])
    try:
        record = service.weave_goals.get(source)
        held = [text for text in tool_results(service, source, "mark_thread_goal")]
        assert any("No research direction was chosen" in text for text in held)
        assert goal_depth.claim_gap(record)
        assert "Direction: no direction was compared and chosen" in goal_depth.checker_lines(record)
    finally:
        runtime.close()


def test_skipping_the_stage_needs_the_persons_reason_and_the_checker_sees_it(tmp_path):
    reason = "用户明确指定了要复现 LLR 温度标定这一个方向，原话是只做这一项，所以不需要比较方向"
    service, runtime, _, source, _, runs = research(tmp_path, [
        START, calling("weave_directions", {"skip_reason": reason}, "call-skip"),
        calling("run_it", {"step": "直接做"}, "call-run"), answer("好")])
    try:
        record = service.weave_goals.get(source)
        assert record["directions"]["state"] == "skipped" and runs == [{"step": "直接做"}]
        assert reason in goal_depth.checker_lines(record) and not goal_depth.claim_gap(record)
    finally:
        runtime.close()


def test_the_stage_gives_way_rather_than_cost_the_turn(tmp_path):
    service, runtime, _, source, _, runs = research(tmp_path, [
        START, *[calling("run_it", {"step": i}, f"call-{i}") for i in range(4)], answer("做了")])
    try:
        assert runs == [{"step": 2}, {"step": 3}], "asked twice, then it guards nothing"
        assert service.weave_goals.get(source)["directions"]["state"] == "gave_way"
    finally:
        runtime.close()


def test_a_referee_that_gives_no_ranking_is_tried_again_then_the_lead_chooses_alone(tmp_path):
    service, runtime, platform, source, _, _ = research(tmp_path, [START, NAMED, PILOTED, answer("等")],
                                                         direction=[answer("看不出来"), answer("还是看不出来")])
    try:
        tick(service, source, 4)
        record = service.weave_goals.get(source)
        assert record["directions"]["state"] == "ranked" and record["directions"]["ranking"] is None
        assert any("could not rank your directions" in text for text in said(platform))
        assert sum("independent research advisor" in text for text in said(platform)) == 2
    finally:
        runtime.close()


# --- the mid-course referee -----------------------------------------------------------------------------------------

def chosen(tmp_path, after, review=(), checker=()):
    return research(tmp_path, [START, NAMED, PILOTED, answer("等排名"), answer("排名中，继续试点"),
                               calling("weave_directions", {"choose": 2}, "call-choose"),
                               *after], direction=[RANKED], review=review, checker=checker)


def test_results_in_the_ledger_bring_a_mid_course_referee_whose_requests_become_criteria(tmp_path):
    service, runtime, platform, source, _, _ = chosen(tmp_path, [
        calling("weave_claims", CLAIMS, "call-claims"), answer("有结果了"),
        calling("read_workspace_text", {"path": "README.md"}, "call-read"), answer("继续")], review=[REVIEWED])
    try:
        tick(service, source, 2)  # ranking beside a goal turn, then the lead chooses and records a result
        assert service.weave_goals.get(source)["claims"]
        tick(service, source, 2)  # the referee is due: spawned, answers, and is read
        record = service.weave_goals.get(source)
        assert record["earlyReview"]["state"] == "done" and len(record["earlyReview"]["added"]) == 2
        added = [item for item in record["charter"]["criteria"] if item["origin"] == "early review"]
        assert [item["text"] for item in added] == ["主结果用三个随机种子重跑并给出置信区间", "与 Foo 2024 的方法在相同数据上对比"]
        assert record["reviews"][-1]["verdict"] == "early_review", "the final check checks each point was answered"
        assert "命题 1 是符号不变性" in goal._previous_review(record)
        tick(service, source)
        assert any("mid-course referee" in text and "命题 1 是符号不变性" in text for text in said(platform))
        reviewer = [request for request in platform.requests if "senior referee" in str(request.messages)]
        assert "Search the literature" in str(reviewer[0].messages) and "eval.py" in str(reviewer[0].messages)
        assert goal.view(service, source)["earlyReview"]["state"] == "done"
    finally:
        runtime.close()


def test_no_mid_course_referee_without_results_early_in_the_time(tmp_path):
    service, runtime, platform, source, _, _ = chosen(tmp_path, [answer("还在读")])
    try:
        tick(service, source, 3)
        assert "earlyReview" not in service.weave_goals.get(source)
        assert not any("senior referee" in text for text in said(platform))
    finally:
        runtime.close()


def test_a_third_of_the_time_gone_brings_the_referee_without_results(tmp_path):
    service, runtime, platform, source, _, _ = chosen(tmp_path, [answer("还在读"), answer("继续读")], review=[REVIEWED])
    try:
        tick(service, source, 2)
        record = service.weave_goals.get(source)
        service.weave_goals.put(source, {**record, "startedAt": time.time() - 0.35 * 6 * 3600})
        tick(service, source, 2)
        assert service.weave_goals.get(source)["earlyReview"]["state"] == "done"
    finally:
        runtime.close()


def test_the_referee_is_not_started_when_too_little_time_is_left(tmp_path):
    service, runtime, platform, source, _, _ = chosen(tmp_path, [answer("还在读"), answer("继续读")], review=[REVIEWED])
    try:
        tick(service, source, 2)
        record = service.weave_goals.get(source)
        service.weave_goals.put(source, {**record, "startedAt": time.time() - 0.8 * 6 * 3600})
        tick(service, source, 2)
        assert "earlyReview" not in service.weave_goals.get(source)
    finally:
        runtime.close()


def test_the_referees_are_not_the_work_nor_open_sub_agents_of_the_lead():
    assert set(goal_depth.ROLES) <= set(goal.REFEREES)


# --- the fixes from the real run ------------------------------------------------------------------------------------

def test_a_lead_whose_own_turn_failed_is_brought_back_while_its_lines_run(tmp_path, monkeypatch):
    service, runtime, platform, source = base.conversation(tmp_path, [base.START, answer("0"), answer("接着做")])
    monkeypatch.setattr(goal_lines, "live", lambda record: [{"title": "文献线", "state": "running"}])
    monkeypatch.setattr(goal_lines, "advance", lambda *_args: False)
    try:
        before = len(platform.requests)
        tick(service, source)
        assert len(platform.requests) == before, "a lead that stopped to wait for its lines stays stopped"
        session = service.store.load(source)
        session.status, session.current_turn_id, session.error = AgentStatus.FAILED, "failed", "connection reset"
        service.store.save(session)
        tick(service, source)
        record = service.weave_goals.get(source)
        assert record["failures"] == 1 and len(platform.requests) == before
        service.weave_goals.put(source, {**record, "retryAt": 0})
        tick(service, source)
        assert len(platform.requests) == before + 1, "it did not choose to wait: it is brought back"
        assert goal.LEAD_FAILED in said(platform)[-1]
    finally:
        runtime.close()


def test_a_running_goal_tells_the_lead_never_to_end_with_a_question(tmp_path):
    service, runtime, platform, source = base.conversation(tmp_path, [base.START, answer("0"), answer("1")])
    try:
        tick(service, source)
        assert weave.GOAL_RUNNING in said(platform)[-1]
        assert weave.GOAL_RUNNING not in said(platform)[0], "only once a goal runs"
    finally:
        runtime.close()


def test_the_lead_marks_criteria_it_believes_met_and_the_check_alone_passes_them(tmp_path):
    service, runtime, _, source, _, _ = research(tmp_path, [
        START, calling("weave_directions", {"skip_reason": "用户明确指定了只做 LLR 温度标定这一个方向，原话如此，不必比较"}, "call-skip"),
        calling("weave_note", {"text": "主实验完成，结果在 results/eval.json", "current_phase": 1,
                               "met": [{"id": "C2", "evidence": "results/eval.json 第 3 行，三个种子"}]}, "call-note"),
        calling("weave_note", {"text": "再记一条笔记，引用不存在的标准", "current_phase": 1,
                               "met": [{"id": "C99", "evidence": "不存在的标准不能自评"}]}, "call-bad"),
        answer("好")])
    try:
        record = service.weave_goals.get(source)
        c2 = next(item for item in record["charter"]["criteria"] if item["id"] == "C2")
        assert c2["status"] == "open" and c2["selfStatus"] == "met", "its word, not the check's"
        shown = next(item for item in goal.view(service, source)["charter"]["criteria"] if item["id"] == "C2")
        assert shown["status"] == "open" and shown["self"] == "met"
        assert "its word, not evidence" in goal_charter.checker_lines(record)
        assert any("names a criterion by its id" in text for text in tool_results(service, source, "weave_note")),             "an unknown id is refused"
    finally:
        runtime.close()


def test_a_goal_records_the_machine_and_tells_the_lead(tmp_path, monkeypatch):
    monkeypatch.setattr(goal_depth, "probe", lambda workspace: {"cpus": 16, "gpus": ["NVIDIA GeForce RTX 3070 Ti, 8192 MiB"],
                                                                "memoryGb": 32, "diskFreeGb": 120.5})
    service, runtime, platform, source = base.conversation(tmp_path, [base.START, answer("0"), answer("1")])
    try:
        started = tool_results(service, source, "weave_goal")[0]
        assert "RTX 3070 Ti" in started and "one large download at a time" in started
        tick(service, source)
        assert "RTX 3070 Ti" in said(platform)[-1]
    finally:
        runtime.close()


def test_the_probe_never_fails_a_goal(monkeypatch, tmp_path):
    import subprocess
    def boom(*_args, **_kwargs):
        raise OSError("no nvidia-smi")
    monkeypatch.setattr(subprocess, "run", boom)
    found = goal_depth.probe(str(tmp_path))
    assert found["gpus"] == [] and found["cpus"] >= 1
    assert "none found" in goal_depth.machine_line({"machine": found})


def test_in_a_goal_the_finish_review_asks_once_not_three_times(tmp_path):
    from test_automatic_verification import Responses, write
    from test_development_feedback import make_runtime
    platform = Responses([write(), ModelResponse(text="两条线在跑，我先停在这里。"),
                          ModelResponse(text="等它们的报告回来。"), ModelResponse(text="不该被要第三次")])
    runtime, session, _ = make_runtime(tmp_path, platform)
    runtime.goal_owns_claim = lambda _session_id: True
    try:
        result = runtime.start_turn(session.session_id, "继续研究")
        assert result.status is AgentStatus.COMPLETED and result.final_text.startswith("等它们的报告回来。")
        attempts = [e for e in runtime.store.events(session.session_id) if e.kind is E.VERIFICATION_FINISH_REQUESTED]
        assert [e.data["exhausted"] for e in attempts] == [False, True]
        assert len(platform.requests) == 3
    finally:
        runtime.close()
