"""A long goal's charter, notes and referee review: the deliverable an expert would hand in, held to fixed criteria."""
import json
import time

import pytest

from app.agent_runtime.tools import ToolContext
from app.project_runner import goal, goal_charter
import test_project_goal as base
from test_project_goal import answer, calling, conversation, tick, state
from test_project_goal_lines import exec_tool, said_to_lead


@pytest.fixture(autouse=True)
def runtime_with_sub_agents(monkeypatch):
    from app.agent_runtime import turn_plan
    from app.agent_runtime.multi_agent_runtime import MultiAgentRuntime
    monkeypatch.setattr(base.test_app_server, "DurableAgentRuntime", MultiAgentRuntime)
    monkeypatch.setattr(turn_plan, "MAX_HOLDS", 0)  # the task list has its own tests; here the research work just runs


CHARTER = {"deliverable": "一份可以给审稿人评判的研究稿：问题与动机、相关工作与新意、方法、与公平基线的对比实验（含消融和不确定度）、局限，"
                          "附上能复现每个数字的代码和数据。",
           "criteria": ["相关工作对比至少覆盖五篇近年论文，并说明新意在哪里", "主结果与两个公平基线对比，给出置信区间",
                        "消融实验说明每个组件的贡献", "一条命令复现文中所有数字"],
           "phases": ["文献与新意", "方向试点", "主实验", "写作与自审"], "kind": "research"}


LEAD_CRITERIA = len(CHARTER["criteria"])
STANDARD_RESEARCH = len(goal_charter.STANDARD["research"])
ALL = LEAD_CRITERIA + STANDARD_RESEARCH  # the lead's criteria, then the Host's for research
CLAIMS = {"claims": [{"claim": "主结果误块率 0.012（95% 置信区间 0.010–0.014）", "command": "python eval.py --seed 0 1 2",
                      "output": "results/eval.json"}]}


def web_search():
    """A literature search the test model can call: research claims need one in the goal."""
    from app.agent_runtime.contracts import ToolEffect
    from app.agent_runtime.tools import AgentTool, ToolResult
    return AgentTool(name="web_search", description="Search the web.", input_schema={
        "type": "object", "properties": {}, "additionalProperties": True},
        handler=lambda _context, _arguments: ToolResult(ok=True, content="五篇相关论文", data={}),
        effect=ToolEffect.READ_ONLY, open_world=True)  # reads from outside; READ_ONLY so the test thread needs no approval


def start_with_charter(*after):
    return [calling("weave_goal", {"objective": "研究一晚", "hours": 8}, "call-goal"),
            calling("weave_charter", CHARTER, "call-charter"), calling("web_search", {"q": "相关工作"}, "call-search"),
            calling("weave_claims", CLAIMS, "call-claims"), *after]


def research(tmp_path, responses):
    return conversation(tmp_path, responses, tools=(web_search(),))


# --- the charter itself -------------------------------------------------------------------------------------------

@pytest.mark.parametrize("change,expected", [
    ({"deliverable": "一篇论文"}, "deliverable"),
    ({"criteria": ["做实验", "写论文"]}, "acceptance criteria"),
    ({"criteria": ["一条足够具体的验收标准甲", "一条足够具体的验收标准甲", "一条足够具体的验收标准乙"]}, "different"),
    ({"phases": ["全部"]}, "phases"),
])
def test_a_charter_must_be_concrete(change, expected):
    with pytest.raises(ValueError, match=expected):
        goal_charter.set_charter({}, {**CHARTER, **change})


def test_revising_takes_a_reason_and_a_left_out_criterion_stays_dropped():
    record = {}
    goal_charter.set_charter(record, CHARTER)
    fewer = {**CHARTER, "criteria": CHARTER["criteria"][:3]}
    with pytest.raises(ValueError, match="reason"):
        goal_charter.set_charter(record, fewer)
    changed = goal_charter.set_charter(record, {**fewer, "reason": "时间不够，先不做复现脚本"})
    assert changed["dropped"] == [CHARTER["criteria"][3]]
    gone = [item for item in record["charter"]["criteria"] if item["status"] == "dropped"]
    assert [item["text"] for item in gone] == [CHARTER["criteria"][3]] and gone[0]["note"] == "时间不够，先不做复现脚本"
    goal_charter.set_charter(record, {**CHARTER, "reason": "又能做了"})
    back = [item for item in record["charter"]["criteria"] if item["text"] == CHARTER["criteria"][3]]
    assert len(back) == 1 and back[0]["status"] == "open" and back[0]["id"] == "C4", "listed again: the same criterion, open"
    assert len(record["charter"]["revisions"]) == 2


def test_a_review_round_becomes_criteria_and_a_phase_of_the_charter():
    record = {}
    goal_charter.set_charter(record, CHARTER)
    added = goal_charter.add_review_round(record, 1, ["在第三个信道模型下验证结论是否稳健", "短"])
    assert [item["id"] for item in added] == [f"C{ALL + 1}"] and added[0]["origin"] == "review 1"
    charter = record["charter"]
    assert charter["phases"][-1] == "Revision round 1" and charter["current"] == len(charter["phases"]) - 1


def test_the_verdict_marks_each_criterion():
    record = {}
    goal_charter.set_charter(record, CHARTER)
    goal_charter.apply_verdict(record, [{"id": "C1", "met": True, "evidence": "列了六篇"}, {"id": "C2", "met": False,
                                        "evidence": "没有置信区间"}, {"id": "C99", "met": True}, "junk"])
    status = {item["id"]: (item["status"], item["note"]) for item in record["charter"]["criteria"]}
    assert status["C1"] == ("met", "列了六篇") and status["C2"] == ("unmet", "没有置信区间") and status["C3"][0] == "open"


def test_notes_are_kept_and_the_latest_are_shown():
    record = {}
    with pytest.raises(ValueError):
        goal_charter.add_note(record, "做了")
    for number in range(goal_charter.MAX_NOTES + 5):
        goal_charter.add_note(record, f"第 {number} 条：跑完了基线，误块率 0.031，下一步做消融", now=1000 + number)
    assert len(record["notes"]) == goal_charter.MAX_NOTES
    shown = "\n".join(goal_charter.context_lines(record))
    assert f"第 {goal_charter.MAX_NOTES + 4} 条" in shown and "第 0 条" not in shown


# --- in a running goal --------------------------------------------------------------------------------------------

def test_work_that_changes_things_waits_for_the_charter(tmp_path):
    tools, runs = exec_tool()
    service, runtime, platform, _, source = base.build(tmp_path, [
        calling("weave_goal", {"objective": "研究一晚"}, "call-goal"),
        calling("update_plan", {"plan": [{"step": "写章程", "status": "in_progress"}, {"step": "做", "status": "pending"}]}, "call-plan"),
        calling("read_it", {}, "call-read"), calling("run_it", {}, "call-run"),
        calling("weave_charter", CHARTER, "call-charter"), calling("run_it", {}, "call-run-2"), answer("开始了")], tools=tools)
    try:
        from app.agent_runtime.contracts import PermissionMode
        session = runtime.store.load(source)
        session.permission_mode = PermissionMode.FULL_ACCESS
        runtime.store.save(session)
        service.turn_start({"threadId": source, "input": "你有一整晚", "weave": True})
        base.settle(service, source)
        assert len(runs) == 2, "the read ran; the first run waited for the charter; the one after it ran"
        assert any(goal_charter.HELD[:40] in text for text in said_to_lead(platform))
        assert service.weave_goals.get(source)["charter"]["kind"] == "research"
    finally:
        runtime.close()


def test_the_lead_reads_its_charter_and_notes_every_goal_turn(tmp_path):
    service, runtime, platform, source = research(tmp_path, start_with_charter(
        calling("weave_note", {"text": "试点了三个方向，温度头最有希望：误块率从 0.031 降到 0.012，选它", "current_phase": 1}, "call-note"),
        answer("第一轮到这"), answer("第二轮")))
    try:
        tick(service, source)
        said = said_to_lead(platform)[-1]
        assert "Charter. Deliverable:" in said and "C2 (open)" in said and "[方向试点]" in said
        assert "温度头最有希望" in said
        assert said.count("Name the deliverable a top expert") == 1, "weave_goal's answer had it; with a charter written the turn adds none"
    finally:
        runtime.close()


def checker_texts(platform):
    return [text for text in said_to_lead(platform) if "You are an independent checker" in text]


def referee(verdict, **fields):
    return answer(json.dumps({"verdict": verdict, "summary": "复现了主结果", **fields}, ensure_ascii=False))


def test_the_referee_judges_each_criterion_and_its_requests_come_back_as_a_review(tmp_path):
    rejected = referee("needs_changes", criteria=[{"id": "C1", "met": True, "evidence": "六篇"},
                                                 {"id": "C2", "met": False, "evidence": "没有置信区间"}],
                       major=["主结果没有置信区间"], minor=["图 2 缺单位"])
    service, runtime, platform, source = research(tmp_path, start_with_charter(
        base.DONE, answer("做完了"), rejected,
        calling("weave_note", {"text": "回复审稿：主结果补了 95% 置信区间（bootstrap 1000 次）；图 2 补了单位", "current_phase": 3}, "call-reply"),
        base.DONE, answer("改好了"), referee("passed", criteria=[{"id": f"C{n}", "met": True} for n in range(1, ALL + 1)])))
    try:
        tick(service, source, 2)
        record = service.weave_goals.get(source)
        assert record["reviews"][0]["verdict"] == "needs_changes" and record["reviews"][0]["major"] == ["主结果没有置信区间"]
        statuses = {item["id"]: item["status"] for item in record["charter"]["criteria"]}
        assert statuses["C1"] == "met" and statuses["C2"] == "unmet"
        tick(service, source, 4)
        back = [text for text in said_to_lead(platform) if "did not accept the work yet" in text]
        assert back and "Unmet criteria:\\n- C2: 主结果与两个公平基线对比，给出置信区间 (没有置信区间)" in back[0]  # said_to_lead is JSON
        assert "Minor:\\n- 图 2 缺单位" in back[0]
        second = checker_texts(platform)[-1]
        assert "Acceptance criteria:" in second and "C4: 一条命令复现文中所有数字" in second
        assert "(major) 主结果没有置信区间" in second and "补了 95% 置信区间" in second, "the referee checks the response"
        final = state(service, source)
        assert final["status"] == "complete" and final["checked"] is True
        assert [review["verdict"] for review in final["reviews"]] == ["needs_changes", "passed"]
        assert all(item["status"] == "met" for item in final["charter"]["criteria"])
    finally:
        runtime.close()


def test_a_sound_result_with_time_left_gets_a_revision_round_in_the_charter(tmp_path):
    passed = referee("passed", criteria=[{"id": f"C{n}", "met": True} for n in range(1, ALL + 1)],
                     deeper=["在第三个信道模型下验证结论是否稳健", "补一张误块率随信噪比变化的完整曲线"])
    service, runtime, platform, source = research(tmp_path, start_with_charter(
        base.DONE, answer("做完了"), passed, answer("开始修改轮")))
    try:
        tick(service, source, 3)
        record = service.weave_goals.get(source)
        added = [item for item in record["charter"]["criteria"] if item["origin"] == "review 1"]
        assert [item["text"] for item in added] == ["在第三个信道模型下验证结论是否稳健", "补一张误块率随信噪比变化的完整曲线"]
        assert record["charter"]["phases"][-1] == "Revision round 1" and record["deepens"] == 1
        said = said_to_lead(platform)[-1]
        assert "Review round 1" in said and "not new work" in said and f"C{ALL + 1}" in said
        assert state(service, source)["status"] == "active"
    finally:
        runtime.close()


def test_a_goal_from_before_charters_still_gets_its_revision_requests(tmp_path):
    passed = referee("passed", deeper=["在第三个信道模型下验证结论是否稳健"])
    service, runtime, platform, source = conversation(tmp_path, [
        calling("weave_goal", {"objective": "研究一晚", "hours": 8}, "call-goal"), base.DONE, answer("做完了"), passed, answer("继续")])
    try:
        tick(service, source, 3)
        assert "在第三个信道模型下验证结论是否稳健" in said_to_lead(platform)[-1]
    finally:
        runtime.close()


def test_the_hub_sees_the_charter_and_the_reviews(tmp_path):
    service, runtime, platform, source = research(tmp_path, start_with_charter(answer("开始")))
    try:
        view = state(service, source)
        assert view["charter"]["kind"] == "research" and len(view["charter"]["criteria"]) == ALL
        assert view["charter"]["phases"][0] == "文献与新意" and view["reviews"] == []
    finally:
        runtime.close()


def test_charter_and_notes_belong_to_a_running_goal(tmp_path):
    service, runtime, platform, _, source = base.build(tmp_path, [])
    try:
        session = runtime.store.load(source)
        session.weave = True
        runtime.store.save(session)
        context = ToolContext(session_id=source, turn_id="t", workspace=tmp_path)
        with pytest.raises(ValueError, match="weave_goal first"):
            runtime.tools.get("weave_charter").handler(context, CHARTER)
        with pytest.raises(ValueError, match="running long goal"):
            runtime.tools.get("weave_note").handler(context, {"text": "这是一条足够长的笔记，说明做了什么"})
    finally:
        runtime.close()


# --- the Host's bar: standard criteria, the claims ledger, the checker's evidence ----------------------------------

def test_a_research_charter_gets_the_standard_criteria_and_the_lead_cannot_drop_them():
    # Real goal: the lead chartered "new report sections"; one seed, no literature, and the check judged only that.
    record = {}
    goal_charter.set_charter(record, CHARTER)
    standard = [item for item in record["charter"]["criteria"] if item["origin"] == "standard"]
    assert len(standard) == STANDARD_RESEARCH and standard[0]["text"].startswith("相关工作与新意")
    assert any("排除实现错误" in item["text"] for item in standard), "a negative result must rule out a bug"
    goal_charter.set_charter(record, {**CHARTER, "criteria": CHARTER["criteria"][:3], "reason": "时间不够"})
    after = {item["text"]: item["status"] for item in record["charter"]["criteria"]}
    assert all(after[item["text"]] == "open" for item in standard), "a revision leaves the Host's criteria alone"
    assert len([text for text in after if text in {item["text"] for item in standard}]) == STANDARD_RESEARCH
    lines = goal_charter.checker_lines(record)
    assert "(standard)" in lines and "set by the Host" in lines


def test_the_standard_criteria_follow_the_kind_and_the_language():
    english = {"deliverable": "A tested command-line tool that converts the logs, with documentation and examples.",
               "criteria": ["Converts every sample log", "Handles empty input", "Runs on Windows"],
               "phases": ["Build", "Test"], "kind": "software"}
    record = {}
    goal_charter.set_charter(record, english)
    texts = [item["text"] for item in record["charter"]["criteria"] if item["origin"] == "standard"]
    assert len(texts) == len(goal_charter.STANDARD["software"]) and texts[0].startswith("Tests:")
    other = {}
    goal_charter.set_charter(other, {**english, "kind": "writing"})
    assert not [item for item in other["charter"]["criteria"] if item["origin"] == "standard"]


def test_a_note_names_the_phase_and_moves_it(tmp_path):
    # Real goal: current_phase was optional, never given, and the hub showed the first phase all night.
    service, runtime, platform, source = research(tmp_path, start_with_charter(answer("开始")))
    try:
        context = ToolContext(session_id=source, turn_id="t", workspace=tmp_path)
        with pytest.raises(ValueError, match="current_phase"):
            runtime.tools.get("weave_note").handler(context, {"text": "跑完了基线，误块率 0.031，下一步做消融"})
        result = runtime.tools.get("weave_note").handler(context, {"text": "跑完了基线，误块率 0.031，下一步做消融",
                                                                   "current_phase": 2})
        assert "主实验" in result.content and service.weave_goals.get(source)["charter"]["current"] == 2
    finally:
        runtime.close()


def test_a_research_claim_without_literature_or_ledger_goes_back_then_reaches_the_checker_with_the_gaps(tmp_path):
    held = [calling("weave_goal", {"objective": "研究一晚", "hours": 8}, "call-goal"),
            calling("weave_charter", CHARTER, "call-charter"), base.DONE, answer("做完了"),
            calling("mark_thread_goal", {"status": "complete"}, "call-done-2"), answer("还是做完了"),
            calling("mark_thread_goal", {"status": "complete"}, "call-done-3"), answer("真的做完了"),
            referee("needs_changes", major=["没有文献"])]
    service, runtime, platform, source = conversation(tmp_path, held)
    try:
        said = "\n".join(said_to_lead(platform))
        assert "no literature search ran" in said and "claims ledger is empty" in said
        assert runtime.get_goal(source).status.value == "active", "a held claim does not count"
        tick(service, source, 3)
        checker = checker_texts(platform)
        assert checker and "claimed completion although the Host found this missing" in checker[-1]
        assert "no literature search ran" in checker[-1]
    finally:
        runtime.close()


def test_a_claim_moves_to_the_last_phase_and_the_checker_reruns_the_ledger_and_sees_the_commands(tmp_path):
    from app.agent_runtime.contracts import ToolEffect
    from app.agent_runtime.tools import AgentTool, ToolResult
    run = AgentTool(name="exec_it", description="Run a command.", input_schema={
        "type": "object", "properties": {}, "additionalProperties": True}, effect=ToolEffect.MUTATING,
        handler=lambda _context, _arguments: ToolResult(ok=True, content="exit=0", data={
            "argv": ["python", "eval.py", "--seed", "0"], "returncode": 0}))
    service, runtime, platform, source = conversation(tmp_path, start_with_charter(
        calling("exec_it", {}, "call-exec"), base.DONE, answer("做完了"), referee("needs_changes", major=["置信区间太宽"])),
        tools=(web_search(), run))
    try:
        record = service.weave_goals.get(source)
        assert record["charter"]["current"] == len(CHARTER["phases"]) - 1 and record["literature"] is True
        tick(service, source, 2)
        checker = checker_texts(platform)[-1]
        assert "Claims ledger the agent recorded" in checker and "python eval.py --seed 0 1 2" in checker
        assert "Commands the work ran during this goal" in checker and "[0] python eval.py --seed 0" in checker
    finally:
        runtime.close()


def test_a_new_goal_keeps_where_the_last_one_ended_and_re_verifies_what_was_never_checked(tmp_path):
    # Real conversation: a goal ended "complete" with no check, and the next goal built on its results unread.
    service, runtime, platform, source = research(tmp_path, start_with_charter(
        calling("weave_note", {"text": "信道估计器全面落后 LS：误块率 0.40 对 0.17，结论是 MSE 更可行", "current_phase": 2},
                "call-note"), answer("先到这")))
    try:
        record = service.weave_goals.get(source)
        record["phase"] = "done"
        service.weave_goals.put(source, record)
        runtime.set_goal_status(source, goal.GoalStatus.COMPLETE)
        context = ToolContext(session_id=source, turn_id="t2", workspace=tmp_path)
        result = runtime.tools.get("weave_goal").handler(context, {"objective": "下一阶段：BLER 感知的信道估计"})
        assert "WITHOUT an independent check" in result.content and "信道估计器全面落后 LS" in result.content
        fresh = service.weave_goals.get(source)
        assert fresh["previous"]["checked"] is False and fresh["literature"] is True and "notes" not in fresh
        goal_charter.set_charter(fresh, CHARTER)
        inherited = [item for item in fresh["charter"]["criteria"] if item["text"].startswith("继承的结果")]
        assert len(inherited) == 1 and inherited[0]["origin"] == "standard"
        checked = {**record, "checked": True}
        assert not goal_charter._inherits_unchecked({"previous": goal._carry(checked, "x")})
    finally:
        runtime.close()


# --- fewer, sharper gates --------------------------------------------------------------------------------------------

def test_looking_things_up_is_never_held_for_the_charter(tmp_path):
    # Real run: four literature searches were refused before the charter, the opposite of "survey, then decide".
    tools, runs = exec_tool()
    service, runtime, platform, source = conversation(tmp_path, [
        calling("weave_goal", {"objective": "研究一晚"}, "call-goal"), calling("web_search", {"q": "相关工作"}, "call-search"),
        calling("run_it", {}, "call-run"), answer("先查了文献")], tools=(web_search(), *tools))
    try:
        said = "\n".join(said_to_lead(platform))
        assert "五篇相关论文" in said, "the search ran with no charter yet"
        assert not runs and goal_charter.HELD[:40] in said, "changing work still waits for the charter"
    finally:
        runtime.close()


def test_weave_goal_takes_the_charter_in_the_same_call(tmp_path):
    tools, runs = exec_tool()
    service, runtime, platform, source = conversation(tmp_path, [
        calling("weave_goal", {"objective": "研究一晚", **CHARTER}, "call-goal"), calling("run_it", {}, "call-run"),
        answer("开始了")], tools=tools)
    try:
        record = service.weave_goals.get(source)
        assert record["charter"]["deliverable"].startswith("一份可以给审稿人") and len(runs) == 1, "no second step, no hold"
        assert "C5 (standard)" in "\n".join(said_to_lead(platform))
        context = ToolContext(session_id=source, turn_id="t2", workspace=tmp_path)
        runtime.set_goal_status(source, goal.GoalStatus.COMPLETE)
        record["phase"] = "done"
        service.weave_goals.put(source, record)
        with pytest.raises(ValueError, match="acceptance criteria"):
            runtime.tools.get("weave_goal").handler(context, {"objective": "下一个", **CHARTER, "criteria": ["太少"]})
        assert service.weave_goals.get(source)["phase"] == "done", "a bad charter starts nothing"
    finally:
        runtime.close()


def test_a_goal_claim_is_not_held_by_the_task_list_and_the_check_sees_what_it_left_open(tmp_path):
    # Real goals: mark_thread_goal refused three times for unpassed step evidence, while the check decides what done is.
    plan = calling("update_plan", {"plan": [{"step": "跑主实验", "status": "in_progress"},
                                            {"step": "写报告", "status": "pending"}]}, "call-plan")
    service, runtime, platform, source = research(tmp_path, start_with_charter(
        plan, base.DONE, answer("做完了"), referee("needs_changes", major=["报告没写"])))
    try:
        assert service.weave_goals.get(source)["phase"] == "checking" or runtime.get_goal(source).status.value == "complete"
        tick(service, source, 2)
        checker = checker_texts(platform)[-1]
        assert "still shows these steps unfinished" in checker and "写报告 (pending)" in checker
    finally:
        runtime.close()


def test_a_goal_turn_that_wrote_no_note_owes_one_at_the_next_start(tmp_path):
    tools, runs = exec_tool()
    service, runtime, platform, source = conversation(tmp_path, [
        calling("weave_goal", {"objective": "研究一晚", **CHARTER}, "call-goal"), answer("开始了"),
        calling("run_it", {}, "call-run-1"), answer("第一轮做了实验，没写笔记"),
        calling("run_it", {}, "call-run-2"),
        calling("weave_note", {"text": "第一轮：基线误块率 0.031，三个种子；下一步做消融", "current_phase": 2}, "call-note"),
        calling("run_it", {}, "call-run-3"), answer("第二轮")], tools=tools)
    try:
        tick(service, source)
        assert service.weave_goals.get(source).get("noteDue") is not True, "the first goal turn owes nothing yet"
        tick(service, source)
        said = said_to_lead(platform)
        assert any(goal.NOTE_DUE[:40] in text for text in said), "the next goal turn is told"
        assert any(goal_charter.NOTE_HELD[:40] in text for text in said), "its first changing call waits, once"
        assert len(runs) == 2 and service.weave_goals.get(source)["noteDue"] is False
    finally:
        runtime.close()


def test_the_standard_criteria_speak_the_conversations_language():
    english = {**CHARTER, "deliverable": "A referee-ready study of Bloom filter error with code that reproduces every number."}
    record = {}
    goal_charter.set_charter(record, english, language="zh")
    assert any(item["text"].startswith("相关工作与新意") for item in record["charter"]["criteria"])
    other = {}
    goal_charter.set_charter(other, CHARTER, language="latin")
    assert any(item["text"].startswith("Related work and novelty") for item in other["charter"]["criteria"])
    auto = {}
    goal_charter.set_charter(auto, CHARTER, language="auto")
    assert any(item["text"].startswith("相关工作与新意") for item in auto["charter"]["criteria"]), "auto reads the script"


def test_threshold_decisions_are_recorded_with_their_numbers(tmp_path):
    held = [calling("weave_goal", {"objective": "研究一晚", "hours": 8}, "call-goal"),
            calling("weave_charter", CHARTER, "call-charter"), base.DONE, answer("做完了")]
    service, runtime, platform, source = conversation(tmp_path, held)
    try:
        decisions = service.weave_goals.get(source)["decisions"]
        assert decisions[-1]["what"] == "claim_held" and decisions[-1]["missing"] == 2 and "tokens" in decisions[-1]
    finally:
        runtime.close()
