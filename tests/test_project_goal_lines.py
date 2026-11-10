"""Lines of a long goal: the Host asks for the split, runs the sub-agents, and hands the reports back."""
import json
import threading
import time

import pytest

from app.agent_runtime.contracts import AgentLimits, AgentStatus, PermissionMode
from app.agent_runtime.durable_state import GoalStatus
from app.ai import ModelResponse, ModelUsage, ToolCall
from app.ai.errors import AITransportError
from app.agent_runtime.tools import AgentTool, ToolResult
from app.agent_runtime.contracts import ToolEffect
from app.project_runner import goal, goal_charter, goal_lines, weave
import test_app_server
from test_app_server import _wait_until
from test_project_runner import build


@pytest.fixture(autouse=True)
def runtime_with_sub_agents(monkeypatch):
    from app.agent_runtime.multi_agent_runtime import MultiAgentRuntime
    monkeypatch.setattr(test_app_server, "DurableAgentRuntime", MultiAgentRuntime)
    monkeypatch.delenv(goal_lines.MODE_ENV, raising=False)
    monkeypatch.setattr(goal, "LINES_EVERY", 0)
    monkeypatch.setattr(goal_lines, "SPLIT_AFTER", 0)  # the question comes at once unless a test is about when it comes
    monkeypatch.setattr(goal_charter, "MAX_HOLDS", 0)  # the charter is asked for in its own tests; here it gives way
    real = goal_lines._program  # checks name a program; the test machine need not have one called python on PATH
    monkeypatch.setattr(goal_lines, "_program", lambda name, workspace: name in {"python", "pytest"} or real(name, workspace))


def usage(total=10, cached=0):
    return ModelUsage(input_tokens=total, output_tokens=0, total_tokens=total, cached_input_tokens=cached)


def answer(text, **tokens):
    return ModelResponse(text=text, usage=usage(**tokens))


def calling(name, arguments, call_id="call-1", **tokens):
    return ModelResponse(tool_calls=(ToolCall(call_id=call_id, name=name, arguments=arguments),), usage=usage(**tokens))


BRIEF = ("目标：在 results/{0} 下跑这条线的实验。先读 README.md 和 sim.py 了解模型；结果写成 results/{0}/data.csv，"
         "报告里给出关键数字和误差。只改 results/{0}，不要动别的目录，也不要改 sim.py。")


def line(name, writes=(), **extra):
    """A read-only line unless it is given paths; one that writes comes with its check."""
    writes = list(writes)
    check = {"check": ["python", "check.py", name]} if writes else {}
    return {"title": name, "brief": BRIEF.format(name), "writes": writes, **check, **extra}


START = calling("weave_goal", {"objective": "研究一晚"})
SOLO = calling("weave_lines", {"solo_reason": "步骤彼此依赖"}, "call-solo")
DONE = calling("mark_thread_goal", {"status": "complete"}, "call-done")
PASSED = answer(json.dumps({"verdict": "passed", "summary": "跑过了", "issues": []}))


def split(*lines):
    return calling("weave_lines", {"lines": list(lines)}, "call-lines")


class Router:
    """The scripted model per conversation: a line answers from its own script, whatever order they run in."""

    def __init__(self, platform, lead, lines=None):
        self.platform, self.lead, self.lines, self.lock = platform, list(lead), {k: list(v) for k, v in (lines or {}).items()}, threading.Lock()
        platform.responses = []
        platform.execute_chat = self.execute

    def execute(self, _profile, request):
        said = " ".join(str(message.content) for message in request.messages)
        with self.lock:
            self.platform.requests.append(request)
            for name, script in self.lines.items():
                if f"Your line: {name}" in said:
                    item = script.pop(0) if script else answer(f"{name} 没有更多内容")
                    break
            else:
                item = PASSED if "independent checker" in said else self.lead.pop(0)
        if isinstance(item, threading.Event):
            item.wait(10)
            return answer("等到了")
        if isinstance(item, Exception):
            raise item
        return item


def busy():
    return AITransportError("Error code: 429 - upstream out of capacity", retryable=True, status_code=429)


def conversation(tmp_path, lead, lines=None, tools=(), input_text="你有一整晚"):
    service, runtime, platform, _, source = build(tmp_path, [], tools=tools)
    runtime.limits = AgentLimits(transport_retries=0, model_retries=0)
    router = Router(platform, lead, lines)
    if tools:  # the tools under test run without asking
        session = runtime.store.load(source)
        session.permission_mode = PermissionMode.FULL_ACCESS
        runtime.store.save(session)
    service.turn_start({"threadId": source, "input": input_text, "weave": True})
    settle(service, source)
    return service, runtime, platform, source, router


def settle(service, source):
    _wait_until(lambda: not service._is_active(source), timeout=60)


def wait_lines(service, source, timeout=10):
    control = service.runtime.agent_control
    for snapshot in control.list_tree(source, include_closed=False):
        control.wait(source, snapshot.node.session_id, timeout_seconds=timeout)


def tick(service, source, times=1):
    for _ in range(times):
        service.__dict__.get("_goal_polled", {}).clear()
        goal.advance(service)
        settle(service, source)
        wait_lines(service, source)


def record_of(service, source):
    return service.weave_goals.get(source)


def said_to_lead(platform):
    return [json.dumps([str(message.content) for message in request.messages], ensure_ascii=False)
            for request in platform.requests]


def exec_tool():
    runs = []

    def handler(_context, arguments):
        runs.append(arguments)
        return ToolResult(ok=True, content="ran", data={})
    tool = AgentTool(name="run_it", description="Run something.", input_schema={
        "type": "object", "properties": {}, "additionalProperties": True}, handler=handler, effect=ToolEffect.MUTATING)
    read = AgentTool(name="read_it", description="Read something.", input_schema={
        "type": "object", "properties": {}, "additionalProperties": True}, handler=handler, effect=ToolEffect.READ_ONLY)
    return (tool, read), runs


PLAN = {"name": "update_plan"}


def test_a_round_runs_in_sub_agents_and_the_reports_come_back_to_the_lead(tmp_path):
    service, runtime, platform, source, _ = conversation(tmp_path, [
        START, split(line("甲"), line("乙")), answer("线开始了，我结束这一轮"), DONE, answer("做完了")],
        {"甲": [answer("甲的报告：临界点 1.00", total=100)], "乙": [answer("乙的报告：偏差 0.02", total=200)]})
    try:
        record = record_of(service, source)
        assert record["linesDecision"] == "lines" and len(record["lines"]) == 2
        agents = [item["agentId"] for item in record["lines"]]
        assert all(agents) and service.runtime.agent_graph.get(agents[0]).history_mode.value == "none"
        assert service.store.load(agents[0]).model == service.store.load(source).model
        wait_lines(service, source)
        requests = len(platform.requests)
        tick(service, source)  # collects, and brings the lead back in the same move
        record = record_of(service, source)
        assert [item["state"] for item in record["lines"]] == ["done", "done"]
        assert {runtime.agent_graph.get(agent).relation_status.value for agent in agents} == {"closed"}, "closed by the Host"
        assert len(platform.requests) > requests
        lead_turn = said_to_lead(platform)[requests]
        assert "甲的报告：临界点 1.00" in lead_turn and "乙的报告：偏差 0.02" in lead_turn
        tick(service, source, 4)
        final = goal.view(service, source)
        assert final["status"] == "complete" and final["checked"] is True
        assert record_of(service, source)["lineStats"] == {"rounds": 1, "started": 2, "done": 2, "failed": 0, "tokens": 300}
        assert final["tokens"] >= 300, "the budget counts the lines"
    finally:
        runtime.close()


def test_the_lead_is_not_brought_back_while_a_line_runs(tmp_path):
    gate = threading.Event()
    service, runtime, platform, source, _ = conversation(tmp_path, [
        START, split(line("甲"), line("乙")), answer("等报告"), answer("整合好了")],
        {"甲": [answer("甲做完了")], "乙": [gate]})
    try:
        _wait_until(lambda: record_of(service, source)["lines"] and not runtime.agent_control.snapshot(
            source, record_of(service, source)["lines"][0]["agentId"]).execution_running, timeout=10)
        lead_before = record_of(service, source)["turns"]
        service.__dict__.get("_goal_polled", {}).clear()
        goal.advance(service)
        settle(service, source)
        record = record_of(service, source)
        assert record["turns"] == lead_before, "one line is still running: the lead spends nothing"
        assert [item["state"] for item in record["lines"]] == ["done", "running"]
        assert goal.view(service, source)["agents"] == 1 and goal.view(service, source)["lines"][1]["state"] == "running"
        gate.set()
        wait_lines(service, source)
        tick(service, source)
        assert record_of(service, source)["turns"] == lead_before + 1
    finally:
        gate.set()
        runtime.close()


def test_the_host_asks_for_the_split_before_work_that_changes_things(tmp_path):
    tools, runs = exec_tool()
    service, runtime, platform, source, _ = conversation(tmp_path, [
        START,
        calling("update_plan", {"plan": [{"step": "一", "status": "in_progress"}, {"step": "二", "status": "pending"}]}, "call-plan"),
        calling("read_it", {}, "call-read"), calling("run_it", {}, "call-run"), SOLO, calling("run_it", {}, "call-run-2"), answer("做了")], tools=tools)
    try:
        record = record_of(service, source)
        assert record["linesDecision"] == "solo" and record["soloReasons"] == ["步骤彼此依赖"]
        assert len(runs) == 2, "the read ran; the first run was held until the decision; the second ran"
        assert runs[0] == {} and record["linesHolds"] == 1
    finally:
        runtime.close()


def test_the_question_gives_way_rather_than_cost_the_turn(tmp_path):
    tools, runs = exec_tool()
    plan = calling("update_plan", {"plan": [{"step": "一", "status": "in_progress"}, {"step": "二", "status": "pending"}]}, "call-plan")
    service, runtime, platform, source, _ = conversation(tmp_path, [
        START, plan, *[calling("run_it", {}, f"call-{n}") for n in range(goal_lines.MAX_HOLDS + 1)], answer("做了")], tools=tools)
    try:
        assert len(runs) == 1 and record_of(service, source)["linesDecision"] == "gave_way"
    finally:
        runtime.close()


def test_no_question_after_the_host_comes_back_in_another_mode(tmp_path, monkeypatch):
    tools, runs = exec_tool()
    plan = calling("update_plan", {"plan": [{"step": "一", "status": "in_progress"}, {"step": "二", "status": "pending"}]}, "call-plan")
    service, runtime, _, source, _ = conversation(tmp_path, [START, answer("开始"), plan, calling("run_it", {}, "call-run"), answer("做了")], tools=tools)
    try:
        assert record_of(service, source)["linesDecision"] == "later"
        monkeypatch.setenv(goal_lines.MODE_ENV, "prompt")
        tick(service, source)
        assert len(runs) == 1, "the mode that asked is gone; nothing else can answer"
    finally:
        runtime.close()


PLAN_CALL = {"plan": [{"step": "一", "status": "in_progress"}, {"step": "二", "status": "pending"}]}


def test_the_turn_that_started_lines_stops_there_in_hold_mode(tmp_path, monkeypatch):
    monkeypatch.setenv(goal_lines.LEAD_ENV, "hold")
    # Real toolkit run: the lead stayed, polled wait_agent ten times and re-did work, 0.64M against 0.03M for one that stopped.
    tools, runs = exec_tool()
    gate = threading.Event()
    service, runtime, platform, source, _ = conversation(tmp_path, [
        START, calling("update_plan", PLAN_CALL, "call-plan"), split(line("甲")), calling("run_it", {}, "call-held"), answer("等报告")],
        {"甲": [gate]}, tools=tools)
    try:
        assert runs == [], "not while its line runs"
        record = record_of(service, source)
        assert record["linesWaitHolds"] == 1
        assert any(goal_lines.WAIT[:40] in text for text in said_to_lead(platform))
    finally:
        gate.set()
        runtime.close()


def test_stopping_gives_way_rather_than_cost_the_turn_in_hold_mode(tmp_path, monkeypatch):
    monkeypatch.setenv(goal_lines.LEAD_ENV, "hold")
    tools, runs = exec_tool()
    gate = threading.Event()
    service, runtime, _, source, _ = conversation(tmp_path, [
        START, calling("update_plan", PLAN_CALL, "call-plan"), split(line("甲")),
        *[calling("run_it", {}, f"call-{n}") for n in range(goal_lines.MAX_WAIT_HOLDS + 1)], answer("等报告")],
        {"甲": [gate]}, tools=tools)
    try:
        assert len(runs) == 1 and service.store.load(source).status is AgentStatus.COMPLETED
    finally:
        gate.set()
        runtime.close()


def test_a_turn_the_person_started_is_not_held_in_hold_mode(tmp_path, monkeypatch):
    monkeypatch.setenv(goal_lines.LEAD_ENV, "hold")
    from types import SimpleNamespace
    gate = threading.Event()
    service, runtime, _, source, _ = conversation(tmp_path, [
        START, calling("update_plan", PLAN_CALL, "call-plan"), split(line("甲")), answer("等报告")], {"甲": [gate]})
    try:
        record = record_of(service, source)
        assert goal_lines.live(record)
        call = [SimpleNamespace(name="run_it")]
        started = SimpleNamespace(weave=True, session_id=source, current_turn_id=record["linesTurn"])
        assert goal_lines.hold(service, started, None, call), "the turn that started them is"
        theirs = SimpleNamespace(weave=True, session_id=source, current_turn_id="the-persons-turn")
        assert goal_lines.hold(service, theirs, None, call) is None
    finally:
        gate.set()
        runtime.close()


# --- the lead beside its lines (the default): it keeps working, and is told when a line finishes -----------------

def begin(tmp_path, lead, lines=None, tools=(), input_text="你有一整晚"):
    """conversation() without waiting for the lead's turn to end: for a test that acts while it runs."""
    service, runtime, platform, _, source = build(tmp_path, [], tools=tools)
    runtime.limits = AgentLimits(transport_retries=0, model_retries=0)
    router = Router(platform, lead, lines)
    if tools:
        session = runtime.store.load(source)
        session.permission_mode = PermissionMode.FULL_ACCESS
        runtime.store.save(session)
    service.turn_start({"threadId": source, "input": input_text, "weave": True})
    return service, runtime, platform, source, router


def gated_tool(gate):
    """A tool the lead's turn waits in until the test lets it go."""
    def handler(_context, _arguments):
        gate.wait(15)
        return ToolResult(ok=True, content="slow done", data={})
    return AgentTool(name="slow_it", description="Wait for the test.", input_schema={
        "type": "object", "properties": {}, "additionalProperties": True}, handler=handler, effect=ToolEffect.READ_ONLY)


def test_the_lead_keeps_working_while_its_lines_run(tmp_path):
    tools, runs = exec_tool()
    gate = threading.Event()
    service, runtime, platform, source, _ = conversation(tmp_path, [
        START, calling("update_plan", PLAN_CALL, "call-plan"), split(line("甲")), calling("run_it", {}, "call-beside"), answer("先做别的")],
        {"甲": [gate]}, tools=tools)
    try:
        assert goal_lines.live(record_of(service, source)), "its line is still running"
        assert runs == [{}], "the lead's own work ran beside the line"
        assert not any(goal_lines.WAIT[:40] in text for text in said_to_lead(platform))
        assert "Keep working" in "".join(said_to_lead(platform)) or "keep working" in "".join(said_to_lead(platform)).lower()
    finally:
        gate.set()
        runtime.close()


def test_a_report_reaches_the_lead_inside_its_next_tool_result_and_only_once(tmp_path):
    slow = threading.Event()
    tools, runs = exec_tool()
    service, runtime, platform, source, _ = begin(tmp_path, [
        START, calling("update_plan", PLAN_CALL, "call-plan"), split(line("甲")), calling("slow_it", {}, "call-slow"),
        calling("read_it", {}, "call-read-1"), calling("read_it", {}, "call-read-2"), answer("整合好了")],
        {"甲": [answer("甲的报告：临界点 1.00", total=100)]}, tools=(*tools, gated_tool(slow)))
    try:
        _wait_until(lambda: record_of(service, source) and record_of(service, source)["lines"], timeout=30)
        agent = record_of(service, source)["lines"][0]["agentId"]
        _wait_until(lambda: not runtime.agent_control.snapshot(source, agent).execution_running, timeout=30)
        service.__dict__.get("_goal_polled", {}).clear()
        goal.advance(service)  # the line is collected while the lead's turn is still running
        assert record_of(service, source)["lines"][0]["state"] == "done"
        slow.set()
        settle(service, source)
        seen = [text for text in said_to_lead(platform) if "Runtime note" in text and "甲的报告：临界点 1.00" in text]
        assert seen, "the report arrived in a tool result of the lead's own turn"
        first = said_to_lead(platform).index(seen[0])
        assert "甲的报告：临界点 1.00" not in "".join(said_to_lead(platform)[first - 1:first]), "not before it finished"
        record = record_of(service, source)
        assert record["lines"][0]["told"] is True and record["reports"] == "", "nothing is handed over a second time"
    finally:
        slow.set()
        runtime.close()


def test_at_the_end_of_the_round_only_the_reports_not_yet_heard_are_handed_over(tmp_path):
    slow, gate = threading.Event(), threading.Event()
    tools, _ = exec_tool()
    service, runtime, platform, source, _ = begin(tmp_path, [
        START, calling("update_plan", PLAN_CALL, "call-plan"), split(line("甲"), line("乙")), calling("slow_it", {}, "call-slow"),
        calling("read_it", {}, "call-read"), answer("等乙")],
        {"甲": [answer("甲的报告：临界点 1.00")], "乙": [gate]}, tools=(*tools, gated_tool(slow)))
    try:
        _wait_until(lambda: record_of(service, source) and len(record_of(service, source)["lines"]) == 2, timeout=30)
        first = record_of(service, source)["lines"][0]["agentId"]
        _wait_until(lambda: not runtime.agent_control.snapshot(source, first).execution_running, timeout=30)
        service.__dict__.get("_goal_polled", {}).clear()
        goal.advance(service)
        slow.set()
        settle(service, source)  # the lead heard 甲 and ended its turn; 乙 is still running
        record = record_of(service, source)
        assert [line["state"] for line in record["lines"]] == ["done", "running"] and record["lines"][0]["told"]
        gate.set()
        wait_lines(service, source)
        record = record_of(service, source)
        goal_lines.advance(service, source, record)  # the last line is collected; the lead is not brought back yet
        assert [line["state"] for line in record["lines"]] == ["done", "done"]
        assert "## 乙" in record["reports"] and "## 甲" not in record["reports"], "only what the lead has not heard"
    finally:
        slow.set()
        gate.set()
        runtime.close()


def test_a_digest_of_the_untold_lines_leaves_out_the_told_ones():
    told = {"title": "甲", "state": "done", "report": "甲的报告", "writes": [], "changed": [], "told": True}
    fresh = {"title": "乙", "state": "done", "report": "乙的报告", "writes": [], "changed": []}
    record = {"lines": [told, fresh]}
    text = goal_lines._reports(record)
    assert "乙的报告" in text and "甲的报告" not in text
    assert goal_lines._reports({"lines": [told]}) == "", "nothing left to hand over"


def _beside(tmp_path, writes=("results/甲",)):
    """A session beside a running line that writes `writes`, for the Host's rules about the lead's own calls."""
    from types import SimpleNamespace
    gate = threading.Event()
    service, runtime, _, source, _ = conversation(tmp_path, [
        START, calling("update_plan", PLAN_CALL, "call-plan"), split(line("甲", writes=writes)), answer("先歇着")], {"甲": [gate]})
    record = record_of(service, source)
    lead = SimpleNamespace(weave=True, session_id=source, current_turn_id=record["linesTurn"], workspace_dir=str(tmp_path))
    return service, runtime, gate, lead


def call_of(name, **arguments):
    from types import SimpleNamespace
    return SimpleNamespace(name=name, arguments=arguments)


def test_the_lead_cannot_write_what_a_running_line_writes(tmp_path):
    service, runtime, gate, lead = _beside(tmp_path)
    try:
        refused = goal_lines.hold(service, lead, None, [call_of("write_workspace_text", path="results/甲/data.csv", text="x")])
        assert refused and "甲" in refused[0][1].content and "results/甲" in refused[0][1].content
        assert goal_lines.hold(service, lead, None, [call_of("write_workspace_text", path="notes/整合.md", text="x")]) is None
        assert goal_lines.hold(service, lead, None, [call_of("write_workspace_text", path="scratch:probe.py", text="x")]) is None
        assert goal_lines.hold(service, lead, None, [call_of("write_workspace_text", path=str(tmp_path / "results" / "甲" / "a.txt"), text="x")]), \
            "an absolute path inside the workspace is the same file"
        patch = "*** Begin Patch\n*** Update File: results/甲/model.py\n@@\n-a\n+b\n*** End Patch"
        assert goal_lines.hold(service, lead, None, [call_of("apply_patch", patch=patch)])
        moved = {"changes": [{"action": "move", "path": "notes/a.md", "move_to": "results/甲/a.md"}]}
        assert goal_lines.hold(service, lead, None, [call_of("apply_patch", **moved)])
        only = goal_lines.hold(service, lead, None, [call_of("write_workspace_text", path="notes/a.md", text="x"),
                                                     call_of("write_workspace_text", path="results/甲/b.md", text="x")])
        assert len(only) == 1, "only the call that writes the line's file is refused"
    finally:
        gate.set()
        runtime.close()


def test_a_line_that_only_reads_owns_nothing(tmp_path):
    service, runtime, gate, lead = _beside(tmp_path, writes=())
    try:
        assert goal_lines.hold(service, lead, None, [call_of("write_workspace_text", path="results/甲/data.csv", text="x")]) is None
    finally:
        gate.set()
        runtime.close()


def test_the_lead_is_told_to_stop_looking_at_lines_that_have_not_changed(tmp_path):
    service, runtime, gate, lead = _beside(tmp_path)
    try:
        look = [call_of("wait_agent", agent_id="x")]
        assert goal_lines.hold(service, lead, None, look) is None and goal_lines.hold(service, lead, None, look) is None
        refused = goal_lines.hold(service, lead, None, look)
        assert refused and "nothing" not in refused[0][1].content[:5] and "looked at your lines" in refused[0][1].content
        for _ in range(goal_lines.MAX_WAIT_HOLDS):
            goal_lines.hold(service, lead, None, look)
        assert goal_lines.hold(service, lead, None, look) is None, "never at the cost of the turn"
    finally:
        gate.set()
        runtime.close()


def test_a_line_changing_state_lets_the_lead_look_again(tmp_path):
    service, runtime, gate, lead = _beside(tmp_path, writes=())
    try:
        look = [call_of("weave_status")]
        for _ in range(goal_lines.POLL_LIMIT + 1):
            goal_lines.hold(service, lead, None, look)
        assert record_of(service, lead.session_id)["linesPolls"] > goal_lines.POLL_LIMIT
        gate.set()
        wait_lines(service, lead.session_id)
        service.__dict__.get("_goal_polled", {}).clear()
        goal.advance(service)
        record = record_of(service, lead.session_id)
        assert record["lines"][0]["state"] == "done" and record["linesPolls"] == 0
    finally:
        gate.set()
        runtime.close()


def test_the_answer_to_weave_lines_names_the_paths_the_lead_must_leave(tmp_path):
    gate = threading.Event()
    service, runtime, platform, source, _ = conversation(tmp_path, [
        START, calling("update_plan", PLAN_CALL, "call-plan"), split(line("甲", writes=["results/甲"])), answer("先做别的")], {"甲": [gate]})
    try:
        said = " ".join(said_to_lead(platform))
        assert "does not write results/甲" in said and "Keep working" in said
    finally:
        gate.set()
        runtime.close()


def test_the_guide_follows_the_lead_mode(monkeypatch):
    monkeypatch.setenv(goal_lines.LEAD_ENV, "hold")
    assert "no work of your own meanwhile" in goal_lines.guide()
    monkeypatch.delenv(goal_lines.LEAD_ENV)
    assert goal_lines.lead_mode() == "parallel" and "you keep working" in goal_lines.guide()
    monkeypatch.setenv(goal_lines.LEAD_ENV, "nonsense")
    assert goal_lines.lead_mode() == "parallel"


def batch(*calls):
    return ModelResponse(tool_calls=tuple(ToolCall(call_id=f"call-b{at}", name=name, arguments=arguments)
                                          for at, (name, arguments) in enumerate(calls)), usage=usage())


def test_a_goal_asked_for_beside_other_work_still_starts(tmp_path):
    # Real run, "you have eight hours": weave_goal came with a read and without a list, the whole batch was
    # refused, the model reissued only the read, and the night's work ran with no goal at all.
    tools, runs = exec_tool()
    service, runtime, platform, source, _ = conversation(tmp_path, [
        batch(("weave_goal", {"objective": "研究一晚"}), ("run_it", {})), answer("先到这里")], tools=tools)
    try:
        assert record_of(service, source) is not None, "the goal started"
        assert runs == [], "the call that needed a list did not run"
        said = said_to_lead(platform)[-1]
        assert "task list first" in said
    finally:
        runtime.close()


def test_the_list_runs_while_the_work_beside_it_waits_for_the_split(tmp_path):
    tools, runs = exec_tool()
    service, runtime, platform, source, _ = conversation(tmp_path, [
        START, batch(("update_plan", PLAN_CALL), ("run_it", {})), answer("先到这里")], tools=tools)
    try:
        assert runs == [] and record_of(service, source)["linesHolds"] == 1
        kinds = [event.kind.value for event in service.store.events(source)]
        assert "plan_updated" in kinds, "the list is not refused with what the Host is asking about"
    finally:
        runtime.close()


def test_no_question_outside_a_goal(tmp_path):
    tools, runs = exec_tool()
    plan = calling("update_plan", {"plan": [{"step": "一", "status": "in_progress"}, {"step": "二", "status": "pending"}]}, "call-plan")
    service, runtime, _, source, _ = conversation(tmp_path, [plan, calling("run_it", {}, "call-run"), answer("做了")], tools=tools)
    try:
        assert len(runs) == 1 and record_of(service, source) is None
    finally:
        runtime.close()


@pytest.mark.parametrize("lines,expected", [
    ([line("甲", ["a/b"]), line("乙", ["a"])], "both write"),
    ([line("甲", ["src\\x.py"]), line("乙", ["./src/x.py"])], "both write"),
    ([line("甲", resource="GPU"), line("乙", resource="gpu")], "both need gpu"),
    ([line(name) for name in "甲乙丙丁戊"], "At most"),
    ([{"title": "甲", "brief": "做", "writes": []}], "self-contained"),
    ([{"title": "甲", "brief": BRIEF.format("甲"), "writes": ["results/甲"]}], "has no check"),
    ([line("甲", check="python " + "x" * goal_lines.CHECK_CHARS)], "too long"),
    ([], "solo_reason"),
])
def test_what_cannot_run_side_by_side_is_refused_with_the_reason(tmp_path, lines, expected):
    service, runtime, _, source, _ = conversation(tmp_path, [START, answer("开始")])
    try:
        with pytest.raises(ValueError, match=expected):
            goal_lines.start(service, source, {"lines": lines})
        assert not record_of(service, source)["lines"], "nothing of the batch starts"
    finally:
        runtime.close()


def test_separate_paths_and_read_only_lines_are_fine(tmp_path):
    service, runtime, _, source, _ = conversation(tmp_path, [
        START, split(line("甲", ["src/a"]), line("乙", ["src/ab"]), line("丙", []), line("丁", [])), answer("开始")])
    try:
        assert len(record_of(service, source)["lines"]) == 4
    finally:
        runtime.close()


def test_one_round_at_a_time_and_not_during_a_check(tmp_path):
    gate = threading.Event()
    service, runtime, _, source, _ = conversation(tmp_path, [START, split(line("甲")), answer("开始")], {"甲": [gate]})
    try:
        with pytest.raises(ValueError, match="still running"):
            goal_lines.start(service, source, {"lines": [line("乙")]})
        gate.set()
        wait_lines(service, source)
        record = record_of(service, source)
        record.update(lines=[], phase="checking")
        service.weave_goals.put(source, record)
        with pytest.raises(ValueError, match="independent check"):
            goal_lines.start(service, source, {"lines": [line("乙")]})
    finally:
        gate.set()
        runtime.close()


def test_a_line_waits_out_the_provider_and_keeps_what_it_did(tmp_path):
    service, runtime, platform, source, _ = conversation(tmp_path, [
        START, split(line("甲")), answer("开始"), answer("整合")],
        {"甲": [busy(), answer("甲的报告")]})
    try:
        wait_lines(service, source)
        tick(service, source)
        item = record_of(service, source)["lines"][0]
        assert item["state"] == "waiting" and item["retryAt"] > time.time() and goal.view(service, source)["lines"][0]["retryAt"]
        turns = record_of(service, source)["turns"]
        tick(service, source)
        assert record_of(service, source)["lines"][0]["state"] == "waiting", "not before its time"
        assert record_of(service, source)["turns"] == turns, "the lead waits with it"
        record = record_of(service, source)
        record["lines"][0]["retryAt"] = 0
        service.weave_goals.put(source, record)
        agent = item["agentId"]
        tick(service, source)
        assert record_of(service, source)["lines"][0]["agentId"] == agent, "the same agent, with its history"
        tick(service, source)
        assert record_of(service, source)["lines"][0]["report"] == "甲的报告"
        assert record_of(service, source)["lines"][0]["state"] == "done"
    finally:
        runtime.close()


def test_a_provider_that_stays_down_past_the_lines_time_goes_to_the_lead_as_a_failure(tmp_path):
    service, runtime, platform, source, _ = conversation(tmp_path, [
        START, split(line("甲")), answer("开始"), answer("我自己做")], {"甲": [busy()]})
    try:
        wait_lines(service, source)
        tick(service, source)
        record = record_of(service, source)
        record["lines"][0]["deadline"] = 0
        service.weave_goals.put(source, record)
        tick(service, source)
        record = record_of(service, source)
        assert record["lines"][0]["state"] == "failed" and record["lineStats"]["failed"] == 1
        assert any("did not finish" in text and "stayed unavailable" in text for text in said_to_lead(platform))
    finally:
        runtime.close()


def test_a_line_that_fails_for_another_reason_is_not_retried(tmp_path):
    error = AITransportError("bad request", retryable=False, status_code=400)
    service, runtime, platform, source, _ = conversation(tmp_path, [
        START, split(line("甲")), answer("开始"), answer("我自己做")], {"甲": [error]})
    try:
        wait_lines(service, source)
        tick(service, source)
        item = record_of(service, source)["lines"][0]
        assert item["state"] == "failed" and item["attempts"] == 0
        assert any("did not finish" in text for text in said_to_lead(platform))
    finally:
        runtime.close()


def test_a_line_over_its_limit_is_stopped_and_asked_for_its_report(tmp_path):
    gate = threading.Event()
    service, runtime, platform, source, _ = conversation(tmp_path, [
        START, split(line("甲")), answer("开始"), answer("整合")],
        {"甲": [answer("中间"), answer("甲来不及了，只做到一半")]})
    try:
        wait_lines(service, source)
        record = record_of(service, source)
        session = service.store.load(record["lines"][0]["agentId"])
        session.status = AgentStatus.CANCELLED
        service.store.save(session)
        tick(service, source)
        wait_lines(service, source)
        said = said_to_lead(platform)[-1]
        assert goal_lines.REPORT_NOW[:30] in said
        tick(service, source)
        assert record_of(service, source)["lines"][0]["state"] == "done"
        assert record_of(service, source)["lines"][0]["report"] == "甲来不及了，只做到一半"
    finally:
        gate.set()
        runtime.close()


@pytest.mark.parametrize("field,value", [("cap", 0), ("deadline", 0)])
def test_a_running_line_past_its_tokens_or_time_is_cancelled(tmp_path, monkeypatch, field, value):
    gate = threading.Event()
    service, runtime, _, source, _ = conversation(tmp_path, [START, split(line("甲")), answer("开始")], {"甲": [gate]})
    cancelled = []
    original = runtime.cancel
    monkeypatch.setattr(runtime, "cancel", lambda session_id: (cancelled.append(session_id), gate.set(), original(session_id))[2])
    try:
        record = record_of(service, source)
        record["lines"][0][field] = value
        service.weave_goals.put(source, record)
        service.__dict__.get("_goal_polled", {}).clear()
        goal.advance(service)
        assert cancelled == [record["lines"][0]["agentId"]]
        assert record_of(service, source)["lines"][0]["state"] == "reporting"
    finally:
        gate.set()
        runtime.close()


def test_a_line_that_will_not_report_is_closed_as_it_is(tmp_path, monkeypatch):
    gate = threading.Event()
    service, runtime, _, source, _ = conversation(tmp_path, [START, split(line("甲")), answer("开始"), answer("整合")], {"甲": [gate]})
    monkeypatch.setattr(runtime, "cancel", lambda session_id: None)
    try:
        record = record_of(service, source)
        record["lines"][0].update(state="reporting", cap=0, pressedAt=time.time() - goal_lines.REPORT_GRACE - 1)
        service.weave_goals.put(source, record)
        service.__dict__.get("_goal_polled", {}).clear()
        goal.advance(service)
        item = record_of(service, source)["lines"][0]
        assert item["state"] == "failed" and "before it could report" in item["why"]
    finally:
        gate.set()
        runtime.close()


def test_a_line_waiting_for_an_approval_is_given_back_to_the_lead(tmp_path):
    service, runtime, platform, source, _ = conversation(tmp_path, [
        START, split(line("甲")), answer("开始"), answer("我来做")], {"甲": [answer("写到一半")]})
    try:
        wait_lines(service, source)
        record = record_of(service, source)
        session = service.store.load(record["lines"][0]["agentId"])
        session.status = AgentStatus.WAITING_APPROVAL
        service.store.save(session)
        tick(service, source)
        item = record_of(service, source)["lines"][0]
        assert item["state"] == "failed" and "approval" in item["why"]
        assert runtime.store.load(source).permission_mode.value == "workspace", "a goal never widens permissions"
    finally:
        runtime.close()


def test_a_line_the_host_lost_in_a_restart_carries_on(tmp_path):
    service, runtime, platform, source, _ = conversation(tmp_path, [
        START, split(line("甲")), answer("开始"), answer("整合")], {"甲": [answer("一半"), answer("甲做完了")]})
    try:
        wait_lines(service, source)
        record = record_of(service, source)
        agent = record["lines"][0]["agentId"]
        session = service.store.load(agent)
        session.status = AgentStatus.RUNNING
        service.store.save(session)
        tick(service, source)
        wait_lines(service, source)
        item = record_of(service, source)["lines"][0]
        assert item["lost"] == 1 and item["agentId"] == agent
        tick(service, source)
        assert record_of(service, source)["lines"][0]["report"] == "甲做完了"
    finally:
        runtime.close()


def test_a_line_lost_again_and_again_goes_to_the_lead(tmp_path):
    service, runtime, _, source, _ = conversation(tmp_path, [
        START, split(line("甲")), answer("开始"), answer("整合")], {"甲": [answer("一半")]})
    try:
        wait_lines(service, source)
        record = record_of(service, source)
        record["lines"][0]["lost"] = goal_lines.MAX_LOST
        service.weave_goals.put(source, record)
        session = service.store.load(record["lines"][0]["agentId"])
        session.status = AgentStatus.RUNNING
        service.store.save(session)
        tick(service, source)
        assert record_of(service, source)["lines"][0]["state"] == "failed"
    finally:
        runtime.close()


@pytest.mark.parametrize("how", ["budget", "pause", "dismiss"])
def test_a_goal_that_stops_stops_everything_it_opened(tmp_path, how):
    gate = threading.Event()
    service, runtime, _, source, _ = conversation(tmp_path, [START, split(line("甲")), answer("开始")], {"甲": [gate]})
    try:
        record = record_of(service, source)
        agent = record["lines"][0]["agentId"]
        extra = runtime.agent_control.spawn(source, "另外一个", role="worker", history_mode="none", background=False).node.session_id
        if how == "budget":
            record["tokenBudget"] = 1
            service.weave_goals.put(source, record)
            service.__dict__.get("_goal_polled", {}).clear()
            goal.advance(service)
            assert goal.view(service, source)["status"] == "budget_limited"
        else:
            goal.control(service, {"threadId": source, "action": how})
        gate.set()
        graph = runtime.agent_graph
        assert graph.get(agent).relation_status.value == "closed"
        assert graph.get(extra).relation_status.value == "closed", "the lead's own sub-agents too"
    finally:
        gate.set()
        runtime.close()


def test_a_finished_goal_leaves_no_sub_agent_open(tmp_path):
    # Real long run: one sub-agent the lead spawned was never closed.
    service, runtime, _, source, _ = conversation(tmp_path, [START, SOLO, DONE, answer("做完了")])
    try:
        extra = runtime.agent_control.spawn(source, "复核一下", role="worker", history_mode="none", background=False).node.session_id
        tick(service, source, 4)
        assert goal.view(service, source)["checked"] is True
        assert runtime.agent_graph.get(extra).relation_status.value == "closed"
    finally:
        runtime.close()


def test_resuming_after_a_pause_hands_over_what_stopped_lines_had_said(tmp_path):
    gate = threading.Event()
    service, runtime, platform, source, _ = conversation(tmp_path, [
        START, split(line("甲")), answer("开始"), answer("接着做")], {"甲": [gate]})
    try:
        goal.control(service, {"threadId": source, "action": "pause"})
        gate.set()
        goal.control(service, {"threadId": source, "action": "resume"})
        tick(service, source)
        assert any("did not finish" in text and "Stopped by the person" in text for text in said_to_lead(platform))
    finally:
        gate.set()
        runtime.close()


def test_a_claim_with_reports_unread_is_not_checked_yet(tmp_path):
    service, runtime, platform, source, _ = conversation(tmp_path, [
        START, split(line("甲")), DONE, answer("都做完了"), answer("读完报告，再看看"), DONE, answer("真做完了")],
        {"甲": [answer("甲发现一个问题")]})
    try:
        assert runtime.get_goal(source).status is GoalStatus.COMPLETE
        wait_lines(service, source)
        tick(service, source)
        assert runtime.get_goal(source).status is GoalStatus.ACTIVE
        assert not record_of(service, source)["checkerId"]
        assert any("甲发现一个问题" in text for text in said_to_lead(platform))
        tick(service, source, 4)
        assert goal.view(service, source)["checked"] is True
    finally:
        runtime.close()


def test_reports_survive_a_lead_turn_the_provider_failed(tmp_path):
    service, runtime, platform, source, _ = conversation(tmp_path, [
        START, split(line("甲")), answer("开始"), busy(), answer("整合了")], {"甲": [answer("甲的报告")]})
    try:
        wait_lines(service, source)
        tick(service, source)
        record = record_of(service, source)
        assert record["failures"] == 0
        tick(service, source)
        record = record_of(service, source)
        assert record["failures"] == 1
        record["retryAt"] = 0
        service.weave_goals.put(source, record)
        tick(service, source)
        assert "甲的报告" in said_to_lead(platform)[-1], "the failed turn's message stays in the history"
    finally:
        runtime.close()


def test_a_long_report_is_cut_for_the_leads_history():
    lines = [{"title": "甲", "report": "很" * (goal_lines.REPORT_CHARS + 50), "state": "done", "why": "", "changed": [], "writes": []}]
    text = goal_lines._digest(lines)
    assert len(text) < goal_lines.REPORT_CHARS + 500 and "(cut:" in text


def test_the_reports_say_what_the_host_saw_change():
    lines = [{"title": "甲", "report": "x", "state": "done", "why": "", "changed": ["a/1.py", "b/2.py"], "writes": ["a"]},
             {"title": "乙", "report": "y", "state": "done", "why": "", "changed": ["b/2.py"], "writes": ["b"]},
             {"title": "丙", "report": "z", "state": "done", "why": "", "changed": ["c.txt"], "writes": []}]
    text = goal_lines._digest(lines)
    assert "outside what it declared: b/2.py" in text
    assert 'b/2.py was changed by both "甲" and "乙"' in text
    assert "outside what it declared: c.txt" in text


def test_each_line_gets_a_share_of_what_is_left(tmp_path):
    service, runtime, _, source, _ = conversation(tmp_path, [START, split(line("甲"), line("乙")), answer("开始")])
    try:
        record = record_of(service, source)
        expected = int((record["tokenBudget"] - 0) * goal_lines.BUDGET_SHARE / 2)
        assert abs(record["lines"][0]["cap"] - expected) <= expected * 0.01
        assert f"{record['lines'][0]['cap']:,}" in " ".join(str(m.content) for m in service.store.load(record["lines"][0]["agentId"]).messages)
    finally:
        runtime.close()


def test_too_little_budget_or_time_refuses_lines(tmp_path):
    service, runtime, _, source, _ = conversation(tmp_path, [START, answer("开始")])
    try:
        record = record_of(service, source)
        record["tokens"] = int(record["tokenBudget"] * 0.95)
        service.weave_goals.put(source, record)
        with pytest.raises(ValueError, match="budget"):
            goal_lines.start(service, source, {"lines": [line("甲")]})
        record.update(tokens=0, hours=1.0, startedAt=time.time() - 3599)
        service.weave_goals.put(source, record)
        with pytest.raises(ValueError, match="time"):
            goal_lines.start(service, source, {"lines": [line("甲")]})
    finally:
        runtime.close()


def test_a_line_time_is_a_share_of_the_persons_time(tmp_path):
    service, runtime, _, source, _ = conversation(tmp_path, [
        calling("weave_goal", {"objective": "两小时", "hours": 2}), split(line("甲")), answer("开始")])
    try:
        record = record_of(service, source)
        seconds = record["lines"][0]["deadline"] - record["lines"][0]["startedAt"]
        assert 2 * 3600 * goal_lines.TIME_SHARE * 0.95 < seconds <= 2 * 3600 * goal_lines.TIME_SHARE
    finally:
        runtime.close()


def test_a_line_cannot_start_sub_agents_of_its_own(tmp_path):
    service, runtime, _, source, _ = conversation(tmp_path, [START, split(line("甲")), answer("开始")], {"甲": [answer("报告")]})
    try:
        wait_lines(service, source)
        agent = record_of(service, source)["lines"][0]["agentId"]
        with pytest.raises(ValueError, match="works by itself"):
            runtime.agent_control.spawn(agent, "再派一个")
    finally:
        runtime.close()


def test_one_line_failing_to_start_does_not_stop_the_others(tmp_path, monkeypatch):
    service, runtime, _, source, _ = conversation(tmp_path, [START, answer("开始"), answer("整合")], {"乙": [answer("乙的报告")]})
    control = runtime.agent_control
    original = control.spawn

    def spawn(parent, task, **kwargs):
        if "Your line: 甲" in task:
            raise RuntimeError("active agent tree limit reached")
        return original(parent, task, **kwargs)
    monkeypatch.setattr(control, "spawn", spawn)
    try:
        goal_lines.start(service, source, {"lines": [line("甲"), line("乙")]})
        record = record_of(service, source)
        assert record["lines"][0]["state"] == "failed" and record["lines"][1]["state"] == "running"
        assert record["lineStats"]["failed"] == 1 and record["lineStats"]["started"] == 1
    finally:
        runtime.close()


def test_all_lines_failing_to_start_still_reaches_the_lead(tmp_path, monkeypatch):
    service, runtime, platform, source, _ = conversation(tmp_path, [START, answer("开始"), answer("我自己做")])

    def spawn(*_args, **_kwargs):
        raise RuntimeError("active agent tree limit reached")
    monkeypatch.setattr(runtime.agent_control, "spawn", spawn)
    try:
        goal_lines.start(service, source, {"lines": [line("甲")]})
        tick(service, source)
        assert any("tree limit" in text for text in said_to_lead(platform))
    finally:
        runtime.close()


def test_a_deepen_pass_asks_for_the_split_again(tmp_path):
    deeper = answer(json.dumps({"verdict": "passed", "summary": "对", "issues": [], "deeper": ["再做一个实验"]}))
    service, runtime, _, source, router = conversation(tmp_path, [
        calling("weave_goal", {"objective": "两小时", "hours": 8}), SOLO, DONE, answer("做完了")])
    try:
        router.platform.execute_chat = lambda profile, request: deeper if "independent checker" in " ".join(
            str(m.content) for m in request.messages) else router.execute(profile, request)
        assert record_of(service, source)["linesDecision"] == "solo"
        tick(service, source, 2)
        record = record_of(service, source)
        assert record["deepens"] == 1 and record["linesDecision"] == "pending" and record["linesHolds"] == 0
    finally:
        runtime.close()


@pytest.mark.parametrize("value,tool,says", [
    ("prompt", False, "spawn_agent"), ("off", False, "do not use spawn_agent"), ("lines", True, "weave_lines"), ("nonsense", True, "weave_lines")])
def test_the_mode_decides_tool_and_guide(tmp_path, monkeypatch, value, tool, says):
    monkeypatch.setenv(goal_lines.MODE_ENV, value)
    assert goal_lines.mode() in goal_lines.MODES and says in goal_lines.guide()
    service, runtime, _, source, _ = conversation(tmp_path, [START, answer("开始")])
    try:
        names = [item.name for item in weave.tools(service)]
        assert ("weave_lines" in names) is tool
        assert (record_of(service, source)["linesDecision"] == "later") is tool
    finally:
        runtime.close()


def fail_checker(service, source, runtime):
    """The checker's own request fails on a provider that is unavailable."""
    checker = record_of(service, source)["checkerId"]
    runtime.agent_control.wait(source, checker, timeout_seconds=10)
    return checker


class CheckerDown(Router):
    """The checker's requests fail until told otherwise."""
    down = True
    error = staticmethod(busy)

    def execute(self, profile, request):
        said = " ".join(str(message.content) for message in request.messages)
        if "independent checker" in said and self.down:
            self.platform.requests.append(request)
            raise self.error()
        return super().execute(profile, request)


def checker_conversation(tmp_path, error=None):
    service, runtime, platform, _, source = build(tmp_path, [])
    runtime.limits = AgentLimits(transport_retries=0, model_retries=0)
    router = CheckerDown(platform, [START, SOLO, DONE, answer("做完了")])
    if error:
        router.error = error
    service.turn_start({"threadId": source, "input": "你有一整晚", "weave": True})
    settle(service, source)
    tick(service, source)
    return service, runtime, source, router


def test_a_checker_the_provider_failed_waits_it_out_instead_of_counting_as_a_faulty_check(tmp_path):
    # A real goal ended "could not be checked" after two connection errors.
    service, runtime, source, router = checker_conversation(tmp_path)
    try:
        checker = fail_checker(service, source, runtime)
        tick(service, source)
        record = record_of(service, source)
        assert record["checkFaults"] == 0 and record["checkerId"] == checker and record["checkerRetryAt"] > time.time()
        view = goal.view(service, source)
        assert view["status"] == "active" and view["phase"] == "checking" and view["retryAt"] == record["checkerRetryAt"]
        tick(service, source)
        assert record_of(service, source)["checkerRetryAt"] == record["checkerRetryAt"], "not before its time"
        router.down = False
        record["checkerRetryAt"] = 1
        service.weave_goals.put(source, record)
        tick(service, source)  # asks the same checker to carry on
        tick(service, source)  # reads its verdict
        final = goal.view(service, source)
        assert final["status"] == "complete" and final["checked"] is True
    finally:
        runtime.close()


def test_a_checker_failing_for_another_reason_is_not_waited_for(tmp_path):
    bad = lambda: AITransportError("bad request", retryable=False, status_code=400)  # noqa: E731
    service, runtime, source, _ = checker_conversation(tmp_path, bad)
    try:
        fail_checker(service, source, runtime)
        tick(service, source)
        record = record_of(service, source)
        assert not record.get("checkerRetryAt") and record["checkFaults"] == 1
    finally:
        runtime.close()


def test_a_provider_down_past_the_window_ends_as_unchecked_not_forever(tmp_path):
    service, runtime, source, router = checker_conversation(tmp_path)
    try:
        fail_checker(service, source, runtime)
        for _ in range(4):
            record = record_of(service, source)
            if record["phase"] == "done":
                break
            record["checkerFailingSince"] = time.time() - goal.RETRY_WINDOW - 1
            service.weave_goals.put(source, record)
            tick(service, source)
        final = goal.view(service, source)
        assert final["status"] == "complete" and final["checked"] is False
    finally:
        runtime.close()


def test_the_lead_starts_alone_and_is_asked_only_once_the_goal_has_grown(tmp_path, monkeypatch):
    # Real runs: on goals one agent finished in about an hour, a split forced at the start cost 2 to 5 times the tokens.
    monkeypatch.setattr(goal_lines, "SPLIT_AFTER", 1000)
    tools, runs = exec_tool()
    service, runtime, platform, source, _ = conversation(tmp_path, [
        START, calling("update_plan", PLAN_CALL, "call-plan"), calling("run_it", {}, "call-small"),
        calling("run_it", {}, "call-grown", total=2000), SOLO, calling("run_it", {}, "call-after"), answer("做了")], tools=tools)
    try:
        record = record_of(service, source)
        assert len(runs) == 2, "small and after ran; the step that took the goal past the mark was held for the question"
        assert record["linesDecision"] == "solo" and record["linesHolds"] == 1 and record["linesGrown"] is True
        assert any(goal_lines.HELD_LATER[:40] in text for text in said_to_lead(platform))
    finally:
        runtime.close()


def test_a_small_goal_is_never_asked(tmp_path, monkeypatch):
    monkeypatch.setattr(goal_lines, "SPLIT_AFTER", 10 ** 9)
    tools, runs = exec_tool()
    service, runtime, _, source, _ = conversation(tmp_path, [
        START, calling("update_plan", PLAN_CALL, "call-plan"), *[calling("run_it", {}, f"call-{n}") for n in range(4)],
        answer("做了")], tools=tools)
    try:
        assert len(runs) == 4 and record_of(service, source)["linesDecision"] == "later"
        assert not record_of(service, source).get("linesHolds")
    finally:
        runtime.close()


def command_tool():
    """Like exec: what it ran and how it exited, in the result's data; a command that exits non-zero is still a
    completed tool call, as with the real exec."""
    def handler(_context, arguments):
        code = int(arguments.get("code", 0))
        return ToolResult(ok=True, content=f"exit={code}", data={"argv": ["cmd", "/c", arguments["cmd"]], "returncode": code,
                                                                     "running": False})
    return AgentTool(name="run_cmd", description="Run a command.", input_schema={
        "type": "object", "properties": {"cmd": {"type": "string"}, "code": {"type": "integer"}}, "required": ["cmd"],
        "additionalProperties": False}, handler=handler, effect=ToolEffect.READ_ONLY)


def run_cmd(cmd, code=0, call_id="call-cmd"):
    return calling("run_cmd", {"cmd": cmd, "code": code}, call_id)


def test_a_line_that_passed_its_check_is_done_and_the_lead_reads_so(tmp_path):
    service, runtime, platform, source, _ = conversation(tmp_path, [
        START, split(line("甲", ["results/甲"])), answer("开始"), answer("整合")],
        {"甲": [run_cmd("python check.py 甲", 1, "c1"), run_cmd("python  check.py 甲", 0, "c2"), answer("甲的报告")]},
        tools=(command_tool(),))
    try:
        wait_lines(service, source)
        tick(service, source)
        item = record_of(service, source)["lines"][0]
        assert item["state"] == "done" and item["checkResult"] == "passed" and not item["sentBack"], "the last run counts"
        first = str(service.store.load(item["agentId"]).messages[0].content)
        assert "Done when this command passes" in first and "python check.py 甲" in first
        assert any("Check `python check.py 甲`: passed" in text for text in said_to_lead(platform))
        assert goal.view(service, source)["lines"][0]["check"] == "passed"
    finally:
        runtime.close()


def test_a_line_that_did_not_run_its_check_is_sent_back_once(tmp_path):
    service, runtime, platform, source, _ = conversation(tmp_path, [
        START, split(line("甲", ["results/甲"])), answer("开始"), answer("整合")],
        {"甲": [answer("甲说做完了"), run_cmd("python check.py 甲", 0), answer("甲跑过验证了")]}, tools=(command_tool(),))
    try:
        wait_lines(service, source)
        tick(service, source)
        item = record_of(service, source)["lines"][0]
        assert item["state"] == "running" and item["sentBack"] and item["checkResult"] == "not_run"
        assert any("your check was not run" in text for text in said_to_lead(platform))
        wait_lines(service, source)
        tick(service, source)
        item = record_of(service, source)["lines"][0]
        assert item["state"] == "done" and item["checkResult"] == "passed" and item["report"] == "甲跑过验证了"
    finally:
        runtime.close()


def test_a_line_still_failing_its_check_goes_to_the_lead_marked(tmp_path):
    service, runtime, platform, source, _ = conversation(tmp_path, [
        START, split(line("甲", ["results/甲"])), answer("开始"), answer("整合")],
        {"甲": [run_cmd("python check.py 甲", 2, "c1"), answer("甲没修好"), run_cmd("python check.py 甲", 2, "c2"),
                answer("还是没过")]}, tools=(command_tool(),))
    try:
        wait_lines(service, source)
        tick(service, source)
        assert record_of(service, source)["lines"][0]["sentBack"]
        wait_lines(service, source)
        tick(service, source)
        item = record_of(service, source)["lines"][0]
        assert item["state"] == "done" and item["checkResult"] == "failed", "sent back once, not again"
        assert any("did NOT pass" in text and "还是没过" in text for text in said_to_lead(platform))
        assert goal.view(service, source)["lines"][0]["check"] == "failed"
    finally:
        runtime.close()


def test_a_line_ended_another_way_still_says_how_its_check_went(tmp_path):
    service, runtime, platform, source, _ = conversation(tmp_path, [
        START, split(line("甲", ["results/甲"])), answer("开始"), answer("我来做")],
        {"甲": [run_cmd("python check.py 甲", 3), answer("写到一半")]}, tools=(command_tool(),))
    try:
        wait_lines(service, source)
        record = record_of(service, source)
        session = service.store.load(record["lines"][0]["agentId"])
        session.status = AgentStatus.WAITING_APPROVAL
        service.store.save(session)
        tick(service, source)
        item = record_of(service, source)["lines"][0]
        assert item["state"] == "failed" and item["checkResult"] == "failed"
        assert any("did NOT pass" in text for text in said_to_lead(platform))
    finally:
        runtime.close()


def test_a_read_only_line_needs_no_check(tmp_path):
    service, runtime, platform, source, _ = conversation(tmp_path, [
        START, split(line("甲", [])), answer("开始"), answer("整合")], {"甲": [answer("读完了")]})
    try:
        wait_lines(service, source)
        tick(service, source)
        item = record_of(service, source)["lines"][0]
        assert item["state"] == "done" and not item["check"] and not item["sentBack"]
        assert not any("Check `" in text for text in said_to_lead(platform))
    finally:
        runtime.close()


def test_a_claim_is_called_a_claim_until_the_check(tmp_path):
    # Real run: told "Durable goal marked complete.", the lead reported "independently verified" before any check.
    service, runtime, platform, source, _ = conversation(tmp_path, [START, SOLO, DONE, answer("在核验中")])
    try:
        said = said_to_lead(platform)[-1]
        assert goal.CLAIMED[:40] in said and "Durable goal marked complete." not in said
    finally:
        runtime.close()


def test_a_goal_outside_build_mode_keeps_the_plain_answer(tmp_path):
    service, runtime, platform, source, _ = conversation(tmp_path, [answer("好")])
    try:
        runtime.set_goal(source, "普通目标")
        tool = runtime.tools.get("mark_thread_goal")
        from app.agent_runtime.tools import ToolContext
        result = tool.handler(ToolContext(session_id=source, turn_id="t", workspace=tmp_path), {"status": "complete"})
        assert result.content == "Durable goal marked complete."
    finally:
        runtime.close()


def test_outside_a_goal_there_is_nothing_to_split(tmp_path):
    service, runtime, _, source, _ = conversation(tmp_path, [answer("你好")])
    try:
        with pytest.raises(ValueError, match="long goal"):
            goal_lines.start(service, source, {"lines": [line("甲")]})
    finally:
        runtime.close()


def test_a_new_message_is_told_the_old_list_is_not_its_own(tmp_path):
    # Real run: asked to sum up, the model handed in the earlier request's list unchanged and went on editing.
    tools, runs = exec_tool()
    undone = {"plan": [{"step": "修正 README", "status": "in_progress"}, {"step": "冒烟终检", "status": "pending"}]}
    service, runtime, platform, source, router = conversation(tmp_path, [
        calling("update_plan", undone, "call-p1"), answer("先停在这")], tools=tools, input_text="修文档")
    try:
        router.lead += [calling("read_it", {}, "call-read"), answer("总结如下")]
        service.turn_start({"threadId": source, "input": "总结一下我们现在的工作", "weave": True})
        settle(service, source)
        said = said_to_lead(platform)[-1]
        assert "written for an earlier request" in said and "修文档" in said
        assert "Task list from an earlier request" in said, "the list itself is labelled for the model too"
        assert runs == [], "the read waited for this request's list"
    finally:
        runtime.close()


def test_a_goal_turn_reads_its_list_as_its_own(tmp_path):
    # Only the person's new message makes a list "earlier": the Host's own continuation of a goal does not.
    undone = {"plan": [{"step": "实验", "status": "in_progress"}, {"step": "报告", "status": "pending"}]}
    service, runtime, platform, source, _ = conversation(tmp_path, [
        START, calling("update_plan", undone, "call-p1"), SOLO, answer("先到这"), answer("接着做")])
    try:
        tick(service, source)
        said = said_to_lead(platform)[-1]
        assert "Continue pursuing" in said or "durable Loom goal" in said, "this is the goal's own turn"
        assert "Task list from an earlier request" not in said and "Current task milestones" in said
    finally:
        runtime.close()


def test_a_check_must_be_a_command_line_not_a_description():
    # Real lead: "运行 `python experiments/analyze.py` 退出码 0，且断言 ..." – the Host found no such run, both lines "not run".
    prose = [line("甲", ["results/甲"], check="运行 `python experiments/analyze.py` 退出码 0，且断言摘要存在"),
             line("乙", ["notes"], check="notes/audit_memo.md 存在；包含五个小节"),
             line("丙", ["x"], check="Run the tests and ensure they pass")]
    for item in prose:
        with pytest.raises(ValueError, match="not a program on this computer"):
            goal_lines._validated([item])
    assert goal_lines._validated([line("丁", ["y"], check="python check.py 丁 --strict")])[0]["check"].startswith("python")


def test_a_check_in_any_shape_a_lead_sends_is_understood(tmp_path, monkeypatch):
    # Real lead, refused nine times: a JSON list inside a string, the whole command line as one list item,
    # an unquoted interpreter path with spaces, and `python` where only the workspace's venv has one.
    root = tmp_path / "ai run"
    interpreter = root / ".venv" / "Scripts" / "python.exe"
    interpreter.parent.mkdir(parents=True)
    interpreter.write_text("")
    (root / "src").mkdir()
    want = [str(interpreter), "src/check.py"]
    shapes = [json.dumps(want), [f"{interpreter} src/check.py"], f"{interpreter} src/check.py", f'"{interpreter}" src/check.py']
    for shape in shapes:
        assert goal_lines._check_argv("甲", shape, root) == want, shape
    monkeypatch.setattr(goal_lines.shutil, "which", lambda name: None)
    assert goal_lines._check_argv("甲", ["python", "src/check.py"], root) == want, "the workspace's own interpreter"
    with pytest.raises(ValueError, match="not a program on this computer"):
        goal_lines._check_argv("甲", ["no-such-tool", "x.py"], tmp_path / "elsewhere")


def test_an_exhausted_quota_is_waited_out_by_a_running_goal_only(tmp_path):
    from app.project_runner.goal_common import transient
    quota = {"provider_failure": True, "retryable": False, "quota_exhausted": True}
    assert transient(quota, quota=True) and not transient(quota), "a first turn with no goal still needs the person"
    assert not transient({"provider_failure": True, "retryable": False}, quota=True), "bad credentials never wait"


def test_a_line_stopped_before_its_report_still_hands_over_its_last_words(tmp_path):
    from app.ai import AIMessage, MessageRole
    session = type("S", (), {"messages": [AIMessage(MessageRole.USER, "go"), AIMessage(MessageRole.ASSISTANT, "做到第三步，指标已写入 results/la.json"),
                                          AIMessage(MessageRole.TOOL, "x", tool_call_id="c")]})()
    service = type("Svc", (), {"store": type("St", (), {"load": staticmethod(lambda _id: session)})()})()
    assert "做到第三步" in goal_lines._last_words(service, "line")
    session.messages = [AIMessage(MessageRole.USER, "go")]
    assert goal_lines._last_words(service, "line") == ""


def test_lines_say_how_many_processors_they_use_and_are_packed_into_the_machine(monkeypatch):
    # Real goal: two 8-thread trainings refused as "both need cpu" on a machine with 20 logical processors.
    monkeypatch.setattr(goal_lines.os, "cpu_count", lambda: 20)
    two = goal_lines._validated([line("甲", cpus=8), line("乙", cpus=8), line("丙")])
    assert [item["cpus"] for item in two] == [8, 8, 0], "light work takes no share"
    with pytest.raises(ValueError, match="want 24 logical processors and this machine has 18"):
        goal_lines._validated([line(name, cpus=8) for name in "甲乙丙"])
    with pytest.raises(ValueError, match="give cpus"):
        goal_lines._validated([line("甲", resource="cpu")])
    with pytest.raises(ValueError, match="both need gpu"):
        goal_lines._validated([line("甲", resource="gpu", cpus=2), line("乙", resource="gpu")])


def test_a_check_is_an_argument_list_run_by_any_path_to_its_program(tmp_path):
    lines = goal_lines._validated([line("甲", ["results/甲"], check=["python", "check.py", "--name", "甲 乙"])])
    assert lines[0]["checkArgv"] == ["python", "check.py", "--name", "甲 乙"]
    ran = {"argv": [r"E:\ai run\.venv\Scripts\python.exe", "check.py", "--name", "甲 乙"], "returncode": 0}
    assert goal_lines._ran(lines[0]["checkArgv"], ran), "the line used the runtime's Python, not the one on PATH"
    assert not goal_lines._ran(lines[0]["checkArgv"], {"argv": ["python", "check.py"], "returncode": 0})
    wrapped = {"argv": ["cmd", "/c", "python check.py --name 甲 乙"], "returncode": 0}
    assert not goal_lines._ran(["python", "check.py"], wrapped), "extra arguments change what was checked"
    assert goal_lines._ran(["python", "check.py"], {"argv": ["cmd", "/c", "python check.py"]})
    (tmp_path / "check.sh").write_text("exit 0\n")
    assert goal_lines._validated([line("乙", ["x"], check=["check.sh"])], workspace=tmp_path)[0]["check"] == "check.sh"


@pytest.mark.parametrize("argv", [
    ["echo", "python check.py"],
    ["python", "check.py.bak"],
    ["python", "check.py", "--skip-tests"],
    ["cmd", "/c", "echo python check.py"],
    ["cmd", "/c", "python check.py & echo done"],
    ["bash", "-lc", "python check.py || true"],
    ["bash", "-c", "echo 'python check.py'"],
])
def test_a_mentioned_check_or_a_masked_exit_code_is_not_verification(argv):
    assert not goal_lines._ran(["python", "check.py"], {"argv": argv, "returncode": 0})


def test_a_single_wrapped_check_preserves_quoted_arguments():
    check = ["python", "check.py", "--name", "甲 乙"]
    assert goal_lines._ran(check, {"argv": ["cmd", "/d", "/s", "/c", 'python check.py --name "甲 乙"']})
    assert goal_lines._ran(check, {"argv": ["bash", "-lc", "python check.py --name '甲 乙'"]})
    assert goal_lines._ran(check, {"argv": [r"E:\ai run\.venv\Scripts\python.exe", *check[1:]]})


def test_a_sub_agent_woken_by_a_message_runs_on_the_model_it_was_started_on(tmp_path):
    # After a Host restart three lines resumed on the Host's default model (MiniMax-M3) instead of their own.
    from app.agent_runtime.agent_control import AgentControl
    calls = []
    runtime = type("R", (), {"run_queued": staticmethod(lambda sid, max_turns: calls.append(("run", sid)) or "ran")})()
    control = AgentControl.__new__(AgentControl)
    control.runtime = runtime
    assert control._run_queued("line-1") == "ran"
    assert calls == [("run", "line-1")], "restoration belongs to the queue turn's durable failure boundary"


def test_a_resumed_line_is_prepared_by_the_host_like_a_conversation_turn(tmp_path):
    service, runtime, platform, source, _ = conversation(tmp_path, [START, split(line("甲")), answer("开始"), answer("整合")],
                                                         {"甲": [answer("甲的报告")]})
    try:
        prepared = []
        original = service._prepare_turn
        service._prepare_turn = lambda session: (prepared.append(session.session_id), original(session))[1]
        item = record_of(service, source)["lines"][0]
        runtime.agent_control.send(source, item["agentId"], "carry on")
        wait_lines(service, source)
        assert item["agentId"] in prepared
    finally:
        runtime.close()
