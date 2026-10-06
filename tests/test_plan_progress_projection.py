import json
from app.agent_runtime.contracts import AgentEvent, AgentEventKind as E
from app.agent_runtime.task_plan import plan_context


def event(kind, data, turn="current"):
    return AgentEvent(event_id="id", session_id="session", turn_id=turn, kind=kind,
                      created_at="2026-10-05T00:00:00Z", data=data)


def test_plan_projection_changes_only_with_the_plan_not_with_each_tool_result():
    # The projection is appended to the context whenever its text changes, so
    # per-result counters would repeat the whole plan once per step.
    plan = {"plan": [{"step": "Setup", "status": "in_progress"},
                     {"step": "Tests", "status": "pending"}]}
    events = [event(E.TOOL_COMPLETED, {"tool": "exec", "call_id": "before"}),
              event(E.PLAN_UPDATED, plan)]
    first = plan_context(events, "current").content
    state = json.loads(first.split("\n", 1)[1])
    assert state["plan"] == plan["plan"]
    assert "execution_since_plan_update" not in state
    events += [event(E.TOOL_COMPLETED, {"tool": "exec", "call_id": "server-started"}),
               event(E.TOOL_FAILED, {"tool": "browser_type", "call_id": "rejected"})]
    assert plan_context(events, "current").content == first
    events.append(event(E.PLAN_UPDATED, {"plan": [{"step": "Setup", "status": "completed", "evidence": "server-started"}, {"step": "Tests", "status": "in_progress"}]}))
    updated = json.loads(plan_context(events, "current").content.split("\n", 1)[1])
    assert updated["plan"][1]["status"] == "in_progress"


def test_execution_completion_does_not_promote_interrupted_acceptance_to_pass(tmp_path):
    from types import SimpleNamespace
    from app.agent_runtime.task_plan import update_plan_tool
    from app.agent_runtime.tools import ToolContext, validate_tool_arguments
    events = [event(E.TOOL_STARTED, {"call_id": "tool-failed-receipt"}),
              event(E.TOOL_FAILED, {"call_id": "tool-failed-receipt", "data": {}})]
    context = ToolContext("session", "current", tmp_path, emit_event=lambda kind, data: events.append(event(kind, data)))
    tool = update_plan_tool(SimpleNamespace(events=lambda _: events))
    arguments = {"plan": [{"step": "Pressure test", "status": "completed", "outcome": "interrupted", "evidence": "tool-failed-receipt", "evidence_refs": [{"call_id": "tool-failed-receipt"}]}, {"step": "Report", "status": "in_progress"}]}
    validate_tool_arguments(tool.input_schema, arguments)
    result = tool.handler(context, arguments)
    assert result.ok
    state = json.loads(plan_context(events, "current").content.split("\n", 1)[1])
    assert state["plan"][0]["outcome"] == "interrupted"


def test_schema_advertises_status_dependent_requirements():
    import pytest
    from app.agent_runtime.task_plan import update_plan_tool
    from app.agent_runtime.tools import validate_tool_arguments
    tool = update_plan_tool(None)
    for item in ({"step": "Test", "status": "completed"},
                 {"step": "Test", "status": "blocked"}):
        with pytest.raises(ValueError):
            validate_tool_arguments(tool.input_schema, {"plan": [item, {"step": "Report", "status": "pending"}]})
    validate_tool_arguments(tool.input_schema, {"plan": [
        {"step": f"Stage {index}", "status": "pending"} for index in range(9)]})


def test_invalid_plan_reports_all_missing_evidence_in_one_atomic_response(tmp_path):
    from types import SimpleNamespace
    from app.agent_runtime.task_plan import update_plan_tool
    from app.agent_runtime.tools import ToolContext
    emitted = []
    result = update_plan_tool(SimpleNamespace(events=lambda _: [])).handler(
        ToolContext("session", "turn", tmp_path, emit_event=lambda *args: emitted.append(args)),
        {"plan": [{"step": name, "status": "completed"} for name in ("Check", "Report")]})
    assert not result.ok
    invalid = result.data["invalid_references"]
    assert {item["step"] for item in invalid} == {"Check", "Report"}
    assert len(invalid) == 4
    assert not emitted
