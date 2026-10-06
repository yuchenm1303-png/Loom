from types import SimpleNamespace
from app.agent_runtime.contracts import AgentEvent, AgentEventKind as E
from app.agent_runtime.tools import ToolContext
from app.agent_runtime.task_plan import update_plan_tool
import pytest
from app.agent_runtime.evidence import record_check_tool, read_check_ledger_tool


def event(kind, data, timestamp="2026-10-05T00:00:00Z"):
    return AgentEvent("id", "abcdef", "turn", kind, timestamp, data)


def test_completed_plan_rejects_unbacked_prose(tmp_path):
    events = []
    tool = update_plan_tool(SimpleNamespace(events=lambda _: events))
    result = tool.handler(ToolContext("abcdef", "turn", tmp_path, emit_event=lambda k, d: events.append(event(k, d))),
        {"plan": [{"step": "Checks", "status": "completed", "outcome": "passed", "evidence": "all good"},
                  {"step": "Report", "status": "pending"}]})
    assert not result.ok
    assert result.data["execution_status"] == "not_executed"
    assert not events


@pytest.mark.parametrize("refs,events", [
    ([{"call_id": "missing"}], []),
    ([{"call_id": "denied"}], [event(E.TOOL_STARTED, {"call_id": "denied"}),
        event(E.TOOL_FAILED, {"call_id": "denied", "data": {"execution_status": "not_executed"}})]),
    ([{"call_id": "unstarted"}], [event(E.TOOL_COMPLETED, {"call_id": "unstarted"})]),
    ([{"path": "../outside.txt"}], []),
    ([{"path": "missing.txt"}], []),
])
def test_invalid_references_preserve_plan(tmp_path, refs, events):
    original = list(events)
    tool = update_plan_tool(SimpleNamespace(events=lambda _: events))
    result = tool.handler(ToolContext("abcdef", "turn", tmp_path, emit_event=lambda k, d: events.append(event(k, d))),
        {"plan": [{"step": "Checks", "status": "completed", "outcome": "failed", "evidence_refs": refs},
                  {"step": "Report", "status": "pending"}]})
    assert not result.ok
    assert result.data["invalid_references"]
    assert events == original


def test_executed_failure_and_workspace_file_support_completed_stage(tmp_path):
    (tmp_path / "result.txt").write_text("failure observed", encoding="utf-8")
    events = [event(E.TOOL_STARTED, {"call_id": "check"}),
              event(E.TOOL_FAILED, {"call_id": "check", "ok": False, "data": {}})]
    context = ToolContext("abcdef", "turn", tmp_path, emit_event=lambda k, d: events.append(event(k, d)))
    result = update_plan_tool(SimpleNamespace(events=lambda _: events)).handler(context,
        {"plan": [{"step": "Checks", "status": "completed", "outcome": "failed",
                   "evidence_refs": [{"call_id": "check"}, {"path": "result.txt"}]},
                  {"step": "Report", "status": "pending"}]})
    assert result.ok
    assert events[-1].data["plan"][0]["outcome"] == "failed"


def test_check_elapsed_time_comes_from_events_and_ledger_survives_reload(tmp_path):
    from app.agent_runtime.storage import FileAgentSessionStore
    events = [event(E.TOOL_STARTED, {"call_id": "first"}),
        event(E.TOOL_COMPLETED, {"call_id": "first", "data": {}}, "2026-10-05T00:00:01Z"),
        event(E.TOOL_STARTED, {"call_id": "last"}),
        event(E.TOOL_COMPLETED, {"call_id": "last", "data": {}}, "2026-10-05T00:05:16Z")]
    store = FileAgentSessionStore(tmp_path / "store")
    for item in events:
        store.append_event(item)
    context = ToolContext("abcdef", "turn", tmp_path, emit_event=lambda k, d: store.append_event(event(k, d)))
    args = {"case_id": "idle", "expectation": "180 seconds", "observed": "model says 180",
            "verdict": "passed", "evidence_refs": [{"call_id": "first"}, {"call_id": "last"}]}
    result = record_check_tool(store).handler(context, args)
    assert result.ok and result.data["elapsed_seconds"] == 315
    assert result.data["verdict"] == "passed"  # Runtime does not infer the verdict.
    reloaded = FileAgentSessionStore(tmp_path / "store")
    ledger = read_check_ledger_tool(reloaded).handler(context, {})
    assert ledger.data["entries"][0]["elapsed_seconds"] == 315


def test_completed_plan_requires_outcome_even_with_valid_file(tmp_path):
    (tmp_path / "proof.txt").write_text("proof", encoding="utf-8")
    events = []
    result = update_plan_tool(SimpleNamespace(events=lambda _: events)).handler(
        ToolContext("abcdef", "turn", tmp_path, emit_event=lambda k, d: events.append(event(k, d))),
        {"plan": [{"step": "Checks", "status": "completed", "evidence_refs": [{"path": "proof.txt"}]},
                  {"step": "Report", "status": "pending"}]})
    assert not result.ok
    assert "outcome" in result.data["invalid_references"][0]["reason"]
    assert not events
