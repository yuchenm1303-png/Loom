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
    from app.agent_runtime.multi_agent_runtime import MultiAgentRuntime
    monkeypatch.setattr(base.test_app_server, "DurableAgentRuntime", MultiAgentRuntime)


CHARTER = {"deliverable": "一份可以给审稿人评判的研究稿：问题与动机、相关工作与新意、方法、与公平基线的对比实验（含消融和不确定度）、局限，"
                          "附上能复现每个数字的代码和数据。",
           "criteria": ["相关工作对比至少覆盖五篇近年论文，并说明新意在哪里", "主结果与两个公平基线对比，给出置信区间",
                        "消融实验说明每个组件的贡献", "一条命令复现文中所有数字"],
           "phases": ["文献与新意", "方向试点", "主实验", "写作与自审"], "kind": "research"}


def start_with_charter(*after):
    return [calling("weave_goal", {"objective": "研究一晚", "hours": 8}, "call-goal"),
            calling("weave_charter", CHARTER, "call-charter"), *after]


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
    assert [item["id"] for item in added] == ["C5"] and added[0]["origin"] == "review 1"
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
    service, runtime, platform, source = conversation(tmp_path, start_with_charter(
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
    service, runtime, platform, source = conversation(tmp_path, start_with_charter(
        base.DONE, answer("做完了"), rejected,
        calling("weave_note", {"text": "回复审稿：主结果补了 95% 置信区间（bootstrap 1000 次）；图 2 补了单位"}, "call-reply"),
        base.DONE, answer("改好了"), referee("passed", criteria=[{"id": f"C{n}", "met": True} for n in range(1, 5)])))
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
    passed = referee("passed", criteria=[{"id": f"C{n}", "met": True} for n in range(1, 5)],
                     deeper=["在第三个信道模型下验证结论是否稳健", "补一张误块率随信噪比变化的完整曲线"])
    service, runtime, platform, source = conversation(tmp_path, start_with_charter(
        base.DONE, answer("做完了"), passed, answer("开始修改轮")))
    try:
        tick(service, source, 3)
        record = service.weave_goals.get(source)
        added = [item for item in record["charter"]["criteria"] if item["origin"] == "review 1"]
        assert [item["text"] for item in added] == ["在第三个信道模型下验证结论是否稳健", "补一张误块率随信噪比变化的完整曲线"]
        assert record["charter"]["phases"][-1] == "Revision round 1" and record["deepens"] == 1
        said = said_to_lead(platform)[-1]
        assert "Review round 1" in said and "not new work" in said and "C5" in said
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
    service, runtime, platform, source = conversation(tmp_path, start_with_charter(answer("开始")))
    try:
        view = state(service, source)
        assert view["charter"]["kind"] == "research" and len(view["charter"]["criteria"]) == 4
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
