"""A model turn must not parse the whole session log or session.json for bookkeeping.

On a long session both are megabytes. Preparing each request used to read the full event
log for the rollover check and parse session.json four times just to confirm the thread
exists, which was seconds per step.
"""
from __future__ import annotations

import pytest

from app.agent_runtime import storage
from app.agent_runtime.contracts import AgentEvent, AgentEventKind as Event, AgentStatus
from app.agent_runtime.context_runtime import ContextAgentRuntime
from app.agent_runtime.durable_runtime import DurableAgentRuntime
from app.agent_runtime.sandbox import SandboxManager, SandboxPolicy
from app.agent_runtime.storage import FileAgentSessionStore
from app.agent_runtime.tools import AgentTool, ToolRegistry, ToolResult
from app.ai import ModelResponse, ToolCall
from scripted_agent_platform import Scripted


def _runtime(tmp_path, responses):
    probe = AgentTool("probe", "check", {"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]},
                      lambda context, arguments: ToolResult(True, "ok " + arguments["id"]))
    return ContextAgentRuntime(platform=Scripted(responses), store=FileAgentSessionStore(tmp_path / "state"),
                               tools=ToolRegistry((probe,)), sandbox_manager=SandboxManager(policy=SandboxPolicy.OFF))


def test_a_multi_step_turn_never_reads_the_whole_event_log(tmp_path, monkeypatch):
    rt = _runtime(tmp_path, [
        ModelResponse(tool_calls=(ToolCall("a", "probe", {"id": "a"}),)),
        ModelResponse(tool_calls=(ToolCall("b", "probe", {"id": "b"}),)),
        ModelResponse(text="done"),
    ])
    session = rt.create_session("test", workspace_dir=tmp_path, permission_mode="full-access")
    full_reads = []
    real_view = FileAgentSessionStore._view

    def spy(self, session_id, *, limit=None, project=None, parse=None):
        if limit is None and project is None and parse is None:
            full_reads.append(session_id)
        return real_view(self, session_id, limit=limit, project=project, parse=parse)

    monkeypatch.setattr(FileAgentSessionStore, "_view", spy)
    try:
        result = rt.start_turn(session.session_id, "Run two probes")
        assert result.status is AgentStatus.COMPLETED
        kinds = [event.kind for event in rt.store.events(session.session_id)]
        assert kinds.count(Event.TOOL_STARTED) == 2 and kinds.count(Event.MODEL_REQUESTED) == 3
    finally:
        rt.close()
    # Only the assertion above, made after the turn, reads the log in full.
    assert len(full_reads) == 1


def test_goal_and_queue_lookups_do_not_parse_the_session(tmp_path, monkeypatch):
    rt = DurableAgentRuntime(platform=Scripted([]), store=FileAgentSessionStore(tmp_path / "state"))
    session = rt.create_session("test", workspace_dir=tmp_path)
    parsed = []
    real = storage.session_from_dict
    monkeypatch.setattr(storage, "session_from_dict", lambda payload: parsed.append(1) or real(payload))
    try:
        assert rt.get_goal(session.session_id) is None
        assert rt.list_queued_turns(session.session_id) == ()
        assert parsed == []
        # They still refuse a thread that does not exist, as loading it did.
        with pytest.raises(FileNotFoundError):
            rt.get_goal("00000000-0000-0000-0000-000000000000")
        with pytest.raises(FileNotFoundError):
            rt.list_queued_turns("00000000-0000-0000-0000-000000000000")
    finally:
        rt.close()


def test_require_session_recovers_a_pending_commit_before_deciding(tmp_path):
    store = FileAgentSessionStore(tmp_path / "state")
    rt = DurableAgentRuntime(platform=Scripted([]), store=store)
    session = rt.create_session("test", workspace_dir=tmp_path)
    directory = store.session_dir(session.session_id)
    snapshot = (directory / "session.json").read_text(encoding="utf-8")
    event = AgentEvent("e1", session.session_id, "t", Event.TURN_STARTED, "2026-10-08T00:00:00Z", {})
    (directory / "session.json").unlink()
    (directory / ".pending-commit.json").write_text(
        '{"session":' + snapshot + ',"event":{"event_id":"e1","session_id":"' + session.session_id
        + '","turn_id":"t","kind":"turn_started","created_at":"2026-10-08T00:00:00Z","data":{}}}', encoding="utf-8")
    try:
        store.require_session(session.session_id)  # the journal restores the missing snapshot first
        assert (directory / "session.json").is_file() and not (directory / ".pending-commit.json").exists()
        assert [item.event_id for item in store.events(session.session_id)][-1] == event.event_id
    finally:
        rt.close()


def test_the_restart_backlog_scan_reads_the_end_of_each_log(tmp_path, monkeypatch):
    from test_memory_pipeline import ScriptedPlatform as MemoryPlatform, _runtime as memory_runtime
    from app.ai import AGENT_FAST_ROLE

    workspace = tmp_path / "project"
    workspace.mkdir()
    first, _ = memory_runtime(tmp_path, MemoryPlatform([ModelResponse(text="done")]), auto_extract=False)
    session = first.create_session(AGENT_FAST_ROLE.role_id, workspace_dir=workspace)
    assert first.start_turn(session.session_id, "Remember that this project uses pytest.").status is AgentStatus.COMPLETED
    first.close()

    second, _ = memory_runtime(tmp_path, MemoryPlatform([]), auto_extract=True)
    scheduled, full_reads = [], []
    real_view = FileAgentSessionStore._view

    def spy(self, session_id, *, limit=None, project=None, parse=None):
        if limit is None and project is None and parse is None:
            full_reads.append(session_id)
        return real_view(self, session_id, limit=limit, project=project, parse=parse)

    try:
        second._memory_pipeline.schedule = lambda session_id, delay=0.0: scheduled.append(session_id)
        second._memory_backlog_scheduled = False
        monkeypatch.setattr(FileAgentSessionStore, "_view", spy)
        second._ensure_memory_backlog_scheduled()
    finally:
        second.close()
    assert scheduled == [session.session_id]
    assert full_reads == []


def test_browser_lease_lookup_uses_the_lifecycle_projection(tmp_path):
    from types import SimpleNamespace

    from app.agent_runtime.browser_runtime import BrowserRuntime

    store = FileAgentSessionStore(tmp_path / "state")
    owner = "abcdef"
    for index, (kind, data) in enumerate([
        (Event.BROWSER_SESSION_OPENED, {"browser_id": "b1"}),
        (Event.TOOL_COMPLETED, {"browser_id": "b1", "content": "x" * 5000}),
        (Event.BROWSER_SESSION_OPENED, {"browser_id": "b2"}),
        (Event.BROWSER_SESSION_RELEASED, {"browser_id": "b2", "reason": "closed"}),
    ]):
        store.append_event(AgentEvent(f"e{index}", owner, "", kind, f"2026-10-08T00:00:0{index}Z", data))
    recorded = []
    runtime = SimpleNamespace(
        _lifecycle_events=lambda session_id: store.context_events(session_id),
        _record_browser_lifecycle=lambda *args: recorded.append(args),
    )

    released = BrowserRuntime._lookup_browser_lifecycle(runtime, owner, "b2")
    abandoned = BrowserRuntime._lookup_browser_lifecycle(runtime, owner, "b1")

    assert released == {"browser_id": "b2", "reason": "closed"} and recorded[0][1] == "released"
    assert abandoned["reason"] == "host_restart" and [args[2]["browser_id"] for args in recorded] == ["b1"]
    assert BrowserRuntime._lookup_browser_lifecycle(runtime, owner, "unknown") is None
