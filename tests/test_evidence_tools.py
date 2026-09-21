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
