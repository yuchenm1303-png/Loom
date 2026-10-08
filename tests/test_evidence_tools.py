from __future__ import annotations

from pathlib import Path

from app.agent_runtime.contracts import AgentEvent, AgentEventKind
from app.agent_runtime.evidence_tools import durable_tool_result_tool, run_scratch_dir_tool
from app.agent_runtime.storage import FileAgentSessionStore, utc_now
from app.agent_runtime.tools import ToolContext


def _context(workspace: Path, session_id: str, turn_id: str = "turn-1") -> ToolContext:
    return ToolContext(session_id=session_id, turn_id=turn_id, workspace=workspace)


def test_durable_tool_result_can_list_and_recover_without_rerunning(tmp_path):
    store = FileAgentSessionStore(tmp_path)
    session_id = "11111111-1111-1111-1111-111111111111"
    store.append_event(
        AgentEvent(
            event_id="event-1",
            session_id=session_id,
            turn_id="turn-1",
            kind=AgentEventKind.TOOL_COMPLETED,
            created_at=utc_now(),
            data={
                "call_id": "call-1",
                "tool": "exec",
                "ok": True,
                "content": "authoritative output",
                "data": {"exit_code": 0},
            },
        )
    )
    tool = durable_tool_result_tool(store)

    recent = tool.handler(_context(tmp_path, session_id), {"recent": 10})
    exact = tool.handler(_context(tmp_path, session_id), {"call_id": "call-1"})

    assert recent.ok is True
    assert recent.data["results"][0]["call_id"] == "call-1"
    assert exact.ok is True
    assert exact.content == "authoritative output"
    assert exact.data["result_data"] == {"exit_code": 0}


def test_durable_tool_result_supports_exact_chunk_recovery(tmp_path):
    store = FileAgentSessionStore(tmp_path)
    session_id = "33333333-3333-3333-3333-333333333333"
    content = "A" * 1800 + "MIDDLE" + "Z" * 1800
    store.append_event(
        AgentEvent(
            event_id="event-chunk",
            session_id=session_id,
            turn_id="turn-1",
            kind=AgentEventKind.TOOL_COMPLETED,
            created_at=utc_now(),
            data={
                "call_id": "call-long",
                "tool": "exec",
                "ok": True,
                "content": content,
                "data": {"exit_code": 0},
            },
        )
    )
    tool = durable_tool_result_tool(store)

    first = tool.handler(
        _context(tmp_path, session_id),
        {"call_id": "call-long", "offset": 0, "max_chars": 1000},
    )
    second = tool.handler(
        _context(tmp_path, session_id),
        {
            "call_id": "call-long",
            "offset": first.data["next_offset"],
            "max_chars": 1000,
        },
    )

    assert first.content == content[:1000]
    assert first.data["has_more"] is True
    assert first.data["next_offset"] == 1000
    assert second.content == content[1000:2000]
    assert second.data["content_offset"] == 1000
    assert second.data["total_chars"] == len(content)


def test_run_scratch_directory_is_outside_workspace_and_scoped_to_turn(tmp_path):
    store = FileAgentSessionStore(tmp_path)
    session_id = "22222222-2222-2222-2222-222222222222"
    workspace = tmp_path / "project"
    workspace.mkdir()

    result = run_scratch_dir_tool(store).handler(
        _context(workspace, session_id, "turn-special"),
        {},
    )

    path = Path(result.content)
    assert path.is_dir()
    assert path.name == "turn-special"
    assert workspace.resolve() not in path.resolve().parents
    assert result.data["outside_workspace"] is True


def test_switching_back_to_archived_task_recovers_reports_and_actual_call(tmp_path):
    from app.agent_runtime.evidence_tools import task_history_tool
    store = FileAgentSessionStore(tmp_path)
    sid = "33333333-3333-3333-3333-333333333333"
    # Older target is followed by an unrelated long task. Latest-results-only cannot recover it.
    records = [(AgentEventKind.TOOL_COMPLETED, "deploy", {"call_id": "ssh-verify", "tool": "exec",
                "ok": True, "content": "/opt/loom/.env.auth mode 0600, checked on remote host"}),
               (AgentEventKind.TURN_COMPLETED, "deploy", {"text": "Loom .env.auth deployed and verified"})]
    records += [(AgentEventKind.TOOL_COMPLETED, "other", {"call_id": f"other-{i}", "tool": "exec",
                 "ok": True, "content": "Listing Studio observation"}) for i in range(70)]
    for i, (kind, turn, data) in enumerate(records):
        store.append_event(AgentEvent(f"e-{i}", sid, turn, kind, utc_now(), data))
    # Fresh store models a Host restart; recovery does not depend on in-memory summaries.
    reopened = FileAgentSessionStore(tmp_path)
    context = _context(tmp_path, sid, "return-to-loom")
    history = task_history_tool(reopened).handler(context, {"query": "Loom .env.auth"})
    assert history.data["records"][0]["turn_id"] == "deploy"
    assert history.data["records"][0]["assessment_source"] == "assistant_report_not_independent_verification"
    results = durable_tool_result_tool(reopened).handler(context, {"recent": 5, "query": "/opt/loom", "turn_id": "deploy", "tool": "exec"})
    assert [r["call_id"] for r in results.data["results"]] == ["ssh-verify"]
    exact = durable_tool_result_tool(reopened).handler(context, {"call_id": "ssh-verify"})
    assert "0600" in exact.content


def test_task_history_pagination_and_truncation_are_explicit(tmp_path):
    from app.agent_runtime.evidence_tools import task_history_tool
    store = FileAgentSessionStore(tmp_path)
    sid = "33333333-3333-3333-3333-333333333333"
    for i in range(3):
        store.append_event(AgentEvent(f"e-{i}", sid, f"turn-{i}", AgentEventKind.TURN_COMPLETED,
                                     utc_now(), {"text": str(i) * 1000}))
    tool = task_history_tool(store)
    first = tool.handler(_context(tmp_path, sid), {"limit": 1, "max_chars": 256})
    assert first.data["next_offset"] == 1
    assert first.data["records"][0]["text_truncated"]
    assert len(first.data["records"][0]["text"]) == 256
    second = tool.handler(_context(tmp_path, sid), {"offset": 1, "limit": 2})
    assert [r["turn_id"] for r in second.data["records"]] == ["turn-1", "turn-0"]
    assert second.data["next_offset"] is None
