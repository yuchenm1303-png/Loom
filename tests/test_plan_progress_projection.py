import json
from app.agent_runtime.contracts import AgentEvent, AgentEventKind as E
from app.agent_runtime.task_plan import plan_context


def event(kind, data, turn="current"):
    return AgentEvent(event_id="id", session_id="session", turn_id=turn, kind=kind,
                      created_at="2026-10-05T00:00:00Z", data=data)


def test_plan_projection_exposes_new_results_without_automatically_completing_setup():
    plan = {"plan": [{"step": "Setup", "status": "in_progress"},
                     {"step": "Tests", "status": "pending"}]}
    events = [event(E.TOOL_COMPLETED, {"tool": "exec", "call_id": "before"}),
              event(E.PLAN_UPDATED, plan),
              event(E.TOOL_COMPLETED, {"tool": "exec", "call_id": "server-started"}),
              event(E.TOOL_FAILED, {"tool": "browser_type", "call_id": "rejected"}),
              event(E.TOOL_COMPLETED, {"tool": "exec", "call_id": "other"}, "other-turn")]
    message = plan_context(events, "current")
    state = json.loads(message.content.split("\n", 1)[1])
    assert state["plan"] == plan["plan"]
    assert state["execution_since_plan_update"]["result_count"] == 2
    assert [r["call_id"] for r in state["execution_since_plan_update"]["recent_results"]] == ["server-started", "rejected"]
    events.append(event(E.PLAN_UPDATED, {"plan": [{"step": "Setup", "status": "completed", "evidence": "server-started"}, {"step": "Tests", "status": "in_progress"}]}))
    events.append(event(E.TOOL_COMPLETED, {"tool": "update_plan", "call_id": "plan-call"}))
    updated = json.loads(plan_context(events, "current").content.split("\n", 1)[1])
    assert updated["execution_since_plan_update"]["result_count"] == 0
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
