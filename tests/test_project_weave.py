"""Weave mode: the conversation's own agent chats, proposes a project, and the person starts it."""
import json

from app.ai import ModelResponse, ModelUsage, ToolCall
from app.project_runner import program, weave
from test_app_server import _wait_until
from test_project_runner import PLAN, build, cycle


def answer(text):
    return ModelResponse(text=text, usage=ModelUsage(5, 0, 5))


def calling(name, arguments, call_id="call-1"):
    return ModelResponse(tool_calls=(ToolCall(call_id=call_id, name=name, arguments=arguments),), usage=ModelUsage(5, 0, 5))


def turn(service, source, text, *, weave_mode):
    params = {"threadId": source, "input": text}
    if weave_mode:
        params["weave"] = True
    service.turn_start(params)
    _wait_until(lambda: not service._is_active(source) and service._load(source).status.value != "running", timeout=10)


def tool_names(request):
    return {tool.name for tool in request.tools}


def context_text(request):
    return "\n".join(str(message.content) for message in request.messages if message.name == weave.CONTEXT_NAME)


def test_a_greeting_in_weave_mode_is_one_chat_answer_with_no_project(tmp_path):
    service, runtime, platform, _, source = build(tmp_path, [answer("你好！想做点什么？")])
    try:
        turn(service, source, "你好", weave_mode=True)
        assert len(platform.requests) == 1  # one model request, not a planner and a worker
        request = platform.requests[0]
        assert {"weave_status", "weave_propose", "weave_steer", "weave_control"} <= tool_names(request)
        assert "Weave mode" in context_text(request) and "No project is running" in context_text(request)
        overview = service.project_overview({"threadId": source})
        assert overview["run"] is None and overview["program"] is None and overview["proposal"] is None
    finally:
        runtime.close()


def test_chat_mode_turns_never_see_the_weave_tools_or_guide(tmp_path):
    service, runtime, platform, _, source = build(tmp_path, [answer("hi"), answer("hi again")])
    try:
        turn(service, source, "hello", weave_mode=True)
        turn(service, source, "hello", weave_mode=False)  # the chat composer leaves Weave
        chat = platform.requests[-1]
        assert not any(name.startswith("weave_") for name in tool_names(chat))
        assert service._load(source).weave is False
    finally:
        runtime.close()


def test_a_weave_tool_refuses_outside_weave_mode(tmp_path):
    service, runtime, platform, _, source = build(tmp_path, [])
    try:
        propose = runtime.tools.get("weave_propose")
        from app.agent_runtime.tools import ToolContext
        context = ToolContext(session_id=source, turn_id="t", workspace=tmp_path)
        try:
            propose.handler(context, {"objective": "Build it"})
        except ValueError as exc:
            assert "Weave mode" in str(exc)
        else:
            raise AssertionError("a chat-mode session proposed a project")
        assert service.weave_proposals.get(source) is None
    finally:
        runtime.close()


def test_the_agent_proposes_and_only_the_persons_start_runs_it(tmp_path):
    objective = "Add a CLI that prints the README; done when `python cli.py` prints it."
    service, runtime, platform, _, source = build(tmp_path, [
        calling("weave_propose", {"objective": objective, "title": "README CLI", "token_budget": 3_000_000}),
        answer("I proposed it in the hub; start it when you are ready."),
        answer(json.dumps(PLAN)),
    ])
    try:
        turn(service, source, "Build me a small CLI over several sessions", weave_mode=True)
        proposal = service.project_overview({"threadId": source})["proposal"]
        assert proposal["objective"] == objective and proposal["title"] == "README CLI"
        assert proposal["tokenBudget"] in weave.BUDGETS and proposal["maxWorkers"] == 1
        assert program.for_thread(service.project_store, source) is None  # a proposal starts nothing
        assert len(platform.requests) == 2

        started = weave.start(service, {"threadId": source, "proposalId": proposal["id"], "tokenBudget": 2_000_000,
                                        "maxWorkers": 1, "operationId": "op-1", "permissionMode": "full-access"})
        assert started["objective"] == objective and started["tokenBudget"] == 2_000_000
        assert started["permissionMode"] == "full-access"  # the person's choice in the hub reaches the run
        assert service.project_store.get(started["currentRunId"])["permissionMode"] == "full-access"
        assert service.weave_proposals.get(source) is None
        overview = service.project_overview({"threadId": source})
        assert overview["run"] is not None and overview["proposal"] is None
        # The next weave turn knows a project runs, and a second proposal is refused while it does.
        messages = weave.context_messages(service, service._load(source), None) if service._load(source).weave else ()
        assert messages and "Current project:" in messages[0].content
        cycle(service)  # the planner runs only now, after the person's start
        assert len(platform.requests) == 3
    finally:
        runtime.close()


def test_a_stale_proposal_cannot_start(tmp_path):
    service, runtime, platform, _, source = build(tmp_path, [])
    try:
        service.weave_proposals.put(source, {"id": "p1", "objective": "x", "tokenBudget": 2_000_000, "maxWorkers": 1})
        try:
            weave.start(service, {"threadId": source, "proposalId": "old"})
        except ValueError as exc:
            assert "no longer waiting" in str(exc)
        else:
            raise AssertionError("an old proposal started")
        weave.dismiss(service, {"threadId": source, "proposalId": "p1"})
        assert service.project_overview({"threadId": source})["proposal"] is None
    finally:
        runtime.close()


def test_the_weave_flag_survives_a_reload(tmp_path):
    service, runtime, platform, _, source = build(tmp_path, [answer("ok")])
    try:
        turn(service, source, "hi", weave_mode=True)
        assert runtime.store.load(source).weave is True
    finally:
        runtime.close()


def test_the_planner_sizes_its_plan_and_reads_the_repository_first():
    from app.project_runner.runner import ProjectRunner
    prompt = ProjectRunner._prompt(object.__new__(ProjectRunner), {"phase": "planning", "objective": "Say hello"}, [])
    assert "Size the plan to the objective" in prompt and "Read the repository first" in prompt
    assert prompt.endswith("Say hello")


LIST = {"plan": [{"step": "读代码", "status": "in_progress"}, {"step": "回答", "status": "pending"}]}


def batch(*calls):
    return ModelResponse(tool_calls=tuple(ToolCall(call_id=f"call-{name}-{index}", name=name, arguments=arguments)
                                          for index, (name, arguments) in enumerate(calls)), usage=ModelUsage(5, 0, 5))


def tool_events(runtime, source, kind):
    return [event for event in runtime.store.events(source) if event.kind.value == kind]


def probe_tool(ran):
    from app.agent_runtime import AgentTool, ToolEffect, ToolResult

    def handler(_context, _arguments):
        ran.append(1)
        return ToolResult(ok=True, content="read")
    return AgentTool(name="probe", description="Read something", input_schema={"type": "object", "properties": {}},
                     effect=ToolEffect.READ_ONLY, handler=handler)


def test_a_weave_turn_with_its_list_in_the_first_batch_loses_no_step(tmp_path):
    ran = []
    service, runtime, platform, _, source = build(tmp_path, [
        batch(("update_plan", LIST), ("probe", {})), answer("好了")], tools=(probe_tool(ran),))
    try:
        turn(service, source, "看看代码", weave_mode=True)
        assert ran == [1] and len(platform.requests) == 2
        assert not tool_events(runtime, source, "tool_failed")
    finally:
        runtime.close()


def test_work_without_a_list_is_held_and_says_how_to_go_on(tmp_path):
    ran = []
    service, runtime, platform, _, source = build(tmp_path, [
        batch(("probe", {})), batch(("update_plan", LIST), ("probe", {})), answer("好了")], tools=(probe_tool(ran),))
    try:
        turn(service, source, "看看代码", weave_mode=True)
        assert ran == [1]  # once, after the list; the first call never ran
        failed = tool_events(runtime, source, "tool_failed")
        assert [event.data["data"]["failure_kind"] for event in failed] == ["turn_plan_required"]
        assert "update_plan" in failed[0].data["content"]
        assert service._load(source).status.value == "completed"
    finally:
        runtime.close()


def test_a_model_that_never_writes_a_list_loses_the_list_not_the_turn(tmp_path):
    ran = []
    service, runtime, platform, _, source = build(tmp_path, [
        batch(("probe", {})), batch(("probe", {})), batch(("probe", {})), answer("好了")], tools=(probe_tool(ran),))
    try:
        turn(service, source, "看看代码", weave_mode=True)
        assert ran == [1] and len(tool_events(runtime, source, "tool_failed")) == 2
        assert service._load(source).status.value == "completed"
    finally:
        runtime.close()


def test_a_first_list_may_say_revision_zero(tmp_path):
    ran = []
    service, runtime, platform, _, source = build(tmp_path, [
        batch(("update_plan", {**LIST, "expected_revision": 0}), ("probe", {})), answer("好了")], tools=(probe_tool(ran),))
    try:
        turn(service, source, "看看代码", weave_mode=True)
        assert ran == [1] and not tool_events(runtime, source, "tool_failed")
        assert len(tool_events(runtime, source, "plan_updated")) == 1
    finally:
        runtime.close()


def test_each_turn_shows_its_own_list_and_is_told_of_the_active_one(tmp_path):
    ran = []
    service, runtime, platform, _, source = build(tmp_path, [
        batch(("update_plan", LIST), ("probe", {})), answer("一"),
        batch(("probe", {})), answer("二")], tools=(probe_tool(ran),))
    try:
        turn(service, source, "第一件事", weave_mode=True)
        turn(service, source, "第二件事", weave_mode=True)
        failed = tool_events(runtime, source, "tool_failed")
        assert len(failed) == 1 and "plan_id=" in failed[0].data["content"] and "replace=true" in failed[0].data["content"]
    finally:
        runtime.close()


def test_chat_mode_and_the_modes_own_tools_are_never_held(tmp_path):
    ran = []
    service, runtime, platform, _, source = build(tmp_path, [
        batch(("probe", {})), answer("chat"), batch(("weave_status", {})), answer("no project")], tools=(probe_tool(ran),))
    try:
        turn(service, source, "看看", weave_mode=False)
        assert ran == [1]
        turn(service, source, "项目怎样了", weave_mode=True)
        assert not tool_events(runtime, source, "tool_failed")
    finally:
        runtime.close()


def test_the_guide_asks_for_the_list_and_for_sub_agents():
    assert "update_plan first" in weave.GUIDE and "spawn_agent" in weave.GUIDE and "disjoint files" in weave.GUIDE


def test_the_guide_reaches_past_reading_a_repository():
    # A proposal waits for a click, so unattended work starts by itself.
    assert "weave_goal" in weave.GUIDE and "no repository" in weave.GUIDE
    # The long "delegate when ..." advice never moved a model to delegate; in a goal the Host's lines decide.
    assert "do the work yourself" in weave.GUIDE and "the Host asks whether to split it into lines" in weave.GUIDE
    assert "one GPU" not in weave.GUIDE and "not without sub-agents" not in weave.GUIDE
    assert "charter in that same call" in weave.GUIDE


def test_budget_suggestions_snap_to_the_hubs_choices():
    assert weave.snap_budget(3_000_000) in (2_000_000, 4_000_000)
    assert weave.snap_budget(50_000_000) == 16_000_000
    assert weave.snap_budget("lots") == 8_000_000


def test_a_custom_budget_from_the_hub_is_kept_as_set():
    service = type("S", (), {"weave_budgets": {"a": 40_000_000, "b": 500, "c": 2_000_000_000, "d": "40M"}})()
    assert weave.budget_for(service, "a", 2_000_000) == 40_000_000, "the person's amount, not a preset"
    assert weave.budget_for(service, "b", 3_000_000) in (2_000_000, 4_000_000), "too small: the suggestion"
    assert weave.budget_for(service, "c", 50_000_000) == 16_000_000, "too large: the suggestion, snapped"
    assert weave.budget_for(service, "d", None) == 8_000_000
    assert weave.budget_for(service, "none", 16_000_000) == 16_000_000
    assert weave.budget_for(object(), "a", 4_000_000) == 4_000_000


# A night's work asked for by someone going to bed only ever became a long goal: a proposal waits for a click no one
# makes. With start=true the agent starts it, with the authority a long goal has (the hub's budget, this
# conversation's permissions, never wider).

def _set_mode(runtime, source, mode):
    from app.agent_runtime.contracts import PermissionMode
    session = runtime.store.load(source)
    session.permission_mode = PermissionMode(mode)
    runtime.store.save(session)


def test_the_agent_starts_a_project_itself_when_the_person_is_away(tmp_path):
    objective = "Add a CLI that prints the README; done when `python cli.py` prints it."
    service, runtime, platform, _, source = build(tmp_path, [
        calling("weave_propose", {"objective": objective, "start": True, "max_workers": 2, "token_budget": 2_000_000}),
        answer("项目已经开始了，明早看结果。"), answer(json.dumps(PLAN))])
    try:
        service.weave_budgets = {source: 16_000_000}  # the hub's chip for this conversation wins
        service.weave_proposals.put(source, {"id": "old", "objective": "an earlier idea", "tokenBudget": 2_000_000, "maxWorkers": 1})
        turn(service, source, "我去睡了，你有一整晚，把这个 CLI 做出来", weave_mode=True)
        started = program.for_thread(service.project_store, source)
        assert started is not None and started["objective"] == objective
        assert started["tokenBudget"] == 16_000_000 and started["maxWorkers"] == 2
        assert started["permissionMode"] == "workspace", "this conversation's permissions, no wider"
        assert service.weave_proposals.get(source) is None
        said = json.dumps([str(m.content) for m in platform.requests[-1].messages], ensure_ascii=False)
        assert "Project started without a click" in said
        cycle(service)
        assert len(platform.requests) == 3, "the planner runs without anyone's click"
    finally:
        runtime.close()


def test_an_unattended_project_keeps_full_access_only_where_the_conversation_has_it(tmp_path):
    service, runtime, platform, _, source = build(tmp_path, [
        calling("weave_propose", {"objective": "Build the CLI.", "start": True}), answer("开始了")])
    try:
        _set_mode(runtime, source, "full-access")
        turn(service, source, "你有一整晚", weave_mode=True)
        assert program.for_thread(service.project_store, source)["permissionMode"] == "full-access"
    finally:
        runtime.close()


def test_a_conversation_that_asks_first_cannot_leave_a_project_running_alone(tmp_path):
    service, runtime, platform, _, source = build(tmp_path, [
        calling("weave_propose", {"objective": "Build the CLI.", "start": True}), answer("那我先提议")])
    try:
        _set_mode(runtime, source, "approval")
        turn(service, source, "你有一整晚", weave_mode=True)
        assert program.for_thread(service.project_store, source) is None
        said = json.dumps([str(m.content) for m in platform.requests[-1].messages], ensure_ascii=False)
        assert "would wait at every one" in said
    finally:
        runtime.close()


def test_the_guide_says_when_to_start_and_what_a_project_is_for():
    assert "start it yourself with start=true" in weave.GUIDE
    assert "research, experiments, writing" in weave.GUIDE
