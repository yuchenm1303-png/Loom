"""What persistent-project mode reads (projectRun/overview) and the guidance it sends (projectRun/steer)."""
import json
import uuid

from app.ai import ModelResponse, ToolCall
from app.project_runner import program
from app.project_runner.service import _describe_tool
from test_project_review import setup_review, writing, checking, PLAN, CANDIDATE, PASSED
from test_project_runner import build, control, create, cycle, reply


def test_a_conversation_without_a_project_reads_as_empty(tmp_path):
    service, runtime, platform, _, source = build(tmp_path, [])
    try:
        assert service.project_overview({"threadId": source}) == {
            "run": None, "program": None, "children": [], "agents": [], "events": []}
    finally:
        runtime.close()


def test_an_agent_reads_as_its_role_task_steps_and_latest_action(tmp_path):
    service, runtime, platform, workspace, source = setup_review(tmp_path, [reply(PLAN), writing("fixed"), reply(CANDIDATE)])
    try:
        run = control(service, create(service, source, reviewPolicy="independent"), "start")
        for _ in range(3):
            cycle(service)
        with service.project_store.lock:
            episodes = [json.loads(row[0]) for row in service.project_store.db.execute(
                "SELECT data FROM project_episodes WHERE run_id=? ORDER BY rowid", (run["id"],))]
        worker = next(item for item in episodes if item["phase"] == "working")
        view = service._agent_view(service.project_store.get(run["id"]), worker)
        assert view["role"] == "worker" and view["taskId"] == "task-1"
        assert view["step"] == 2 and view["activity"] == "review_test_write" and view["startedAt"]
        overview = service.project_overview({"threadId": source})
        assert overview["run"]["id"] == run["id"] and overview["run"]["knowledge"]["acceptedChecks"] == 0
        assert "episode_settled" in [event["kind"] for event in overview["events"]]
        assert [event["sequence"] for event in overview["events"]] == sorted(
            (event["sequence"] for event in overview["events"]), reverse=True)  # newest first
        # The milestone's episodes on a clock: the planner's closed, the worker's in order after it.
        spans = overview["timeline"]
        assert [span["phase"] for span in spans][:2] == ["planning", "working"]
        assert spans[0]["end"] is not None and spans[0]["start"] <= spans[0]["end"] <= spans[1]["start"]
        assert spans[1]["taskId"] == "task-1" and spans[0]["taskId"] is None
        assert spans[0]["outcome"] and all(span["tokens"] >= 0 for span in spans)
    finally:
        runtime.close()


def test_tool_calls_read_as_a_short_line():
    assert _describe_tool("read_workspace_text", {"path": "ledger/cli.py"}) == "read_workspace_text ledger/cli.py"
    assert _describe_tool("write_workspace_text", {"path": "scratch:probe.py"}) == "write_workspace_text probe.py"
    assert _describe_tool("exec", {"argv": ["C:\\Python\\python.exe", "-m", "unittest"]}) == "python.exe -m unittest"
    assert _describe_tool("exec", {}) == "exec"
    assert _describe_tool("exec", {"argv": ["cmd", "/c", "git status"]}) == "cmd /c git status"


def test_guidance_reaches_the_next_prompt_and_later_milestones(tmp_path):
    responses = [reply(PLAN), writing("fixed"), reply(CANDIDATE), checking(), reply(PASSED),
                 reply({"complete": True, "summary": "app.txt is fixed"})]
    service, runtime, platform, workspace, source = setup_review(tmp_path, responses)
    try:
        with service.project_runner.guard:
            created = program.create(service, {"operationId": str(uuid.uuid4()), "threadId": source,
                "objective": "Implement a working project", "tokenBudget": 10000, "maxEpisodes": 40})
        cycle(service)  # the planner's episode
        first = program.get(service.project_store, created["id"])["currentRunId"]
        service.project_steer({"runId": first, "text": "  Keep   the public API stable.  "})
        for _ in range(30):
            cycle(service)
            if program.get(service.project_store, created["id"])["state"] != "running":
                break
        later = [str(request.messages) for request in platform.requests[1:]]
        assert all("Keep the public API stable." in text for text in later)
        assert "Keep the public API stable." not in str(platform.requests[0].messages)
        assert "The person directing this project added guidance" in later[0]
        assert "Guidance the person added while the program ran" in later[-1]  # milestone 2's planner
        assert service.project_store.get(first)["guidance"] == ["Keep the public API stable."]
    finally:
        runtime.close()


def test_budget_added_to_a_milestone_is_added_to_its_program(tmp_path):
    service, runtime, platform, workspace, source = setup_review(tmp_path, [reply(PLAN)])
    try:
        with service.project_runner.guard:
            created = program.create(service, {"operationId": str(uuid.uuid4()), "threadId": source,
                "objective": "Implement a working project", "tokenBudget": 10000, "maxEpisodes": 40})
        run = service.project_store.get(program.get(service.project_store, created["id"])["currentRunId"])
        run = control(service, run, "pause")
        service.project_control({"operationId": str(uuid.uuid4()), "runId": run["id"], "expectedRevision": run["revision"],
                                 "tokenBudget": 15000, "maxEpisodes": 60}, "resume")
        after = program.get(service.project_store, created["id"])
        assert (after["tokenBudget"], after["maxEpisodes"]) == (15000, 60)
    finally:
        runtime.close()


def test_an_agent_waiting_for_approval_asks_the_person_in_the_hub(tmp_path):
    import pytest
    from app.agent_runtime import AgentTool, ToolEffect, ToolResult
    from test_app_server import _wait_until
    calls = []
    tool = AgentTool(name="sensitive_project_test", description="Sensitive operation",
        input_schema={"type": "object", "properties": {}}, effect=ToolEffect.SENSITIVE,
        handler=lambda context, arguments: calls.append(True) or ToolResult(ok=True, content="ran"))
    service, runtime, platform, _, source = build(tmp_path, [reply(PLAN),
        ModelResponse(tool_calls=(ToolCall(call_id="sensitive", name=tool.name, arguments={}),)),
        ModelResponse(text="done")], tools=(tool,))
    try:
        run = control(service, create(service, source), "start")
        for _ in range(4):
            cycle(service)
        [agent] = service.project_overview({"threadId": source})["agents"]
        assert agent["approval"]["command"] == "sensitive_project_test" and agent["approval"]["requestId"]
        with pytest.raises(ValueError, match="accept or decline"):
            service.project_approval({"runId": run["id"], "sessionId": agent["id"], "decision": "acceptForSession"})
        with pytest.raises(ValueError, match="no longer waiting in this project"):
            service.project_approval({"runId": run["id"], "sessionId": "someone-else", "decision": "accept"})
        assert service.project_approval({"runId": run["id"], "sessionId": agent["id"], "decision": "accept"})["accepted"]
        _wait_until(lambda: calls)  # the one call ran, and only after the person allowed it
        _wait_until(lambda: runtime.get_session(agent["id"]).status.value != "waiting_approval")
        assert service.project_overview({"threadId": source})["agents"][0]["approval"] is None
    finally:
        runtime.close()


def test_an_approval_asked_before_a_restart_ends_the_episode_and_work_continues(tmp_path):
    from app.agent_runtime import AgentTool, ToolEffect, ToolResult
    from test_app_server import _wait_until
    calls = []
    tool = AgentTool(name="sensitive_project_test", description="Sensitive operation",
        input_schema={"type": "object", "properties": {}}, effect=ToolEffect.SENSITIVE,
        handler=lambda context, arguments: calls.append(True) or ToolResult(ok=True, content="ran"))
    service, runtime, platform, _, source = build(tmp_path, [reply(PLAN),
        ModelResponse(tool_calls=(ToolCall(call_id="sensitive", name=tool.name, arguments={}),)),
        reply({"action": "continue", "summary": "checked the app without that call"})], tools=(tool,))
    try:
        run = control(service, create(service, source), "start")
        for _ in range(4):
            cycle(service)
        [agent] = service.project_overview({"threadId": source})["agents"]
        asked = service.project_store.get(run["id"])["activeEpisode"]["turnId"]
        with runtime._captured_steps_guard:
            runtime._captured_steps.clear()  # what a Host restart leaves: the step is gone
        assert not runtime.can_resume_approval(agent["id"])
        service.project_approval({"runId": run["id"], "sessionId": agent["id"], "decision": "decline"})
        _wait_until(lambda: runtime.get_session(agent["id"]).status.value == "interrupted" and not service._is_active(agent["id"]))
        cycle(service)
        current = service.project_store.get(run["id"])
        assert current["activeEpisode"]["turnId"] != asked  # the asking episode settled; a new one carries on
        assert current["state"] == "running" and not calls  # not stopped for inspection; the call never ran
        assert current.get("interruptionStreak") == 1  # continued as an interruption, not counted as a failure
        [closed] = [event for event in service.store.events(agent["id"]) if event.turn_id == asked and event.kind.value == "turn_interrupted"]
        assert "did not run" in closed.data["error"] and "declined" in closed.data["error"]
    finally:
        runtime.close()
