"""Host time between model steps: what a command costs besides itself, and who owns a change."""
from __future__ import annotations

import subprocess
import sys

import pytest

from app.ai import ModelResponse, ToolCall
from app.agent_runtime import development_verification as dv
from app.agent_runtime.contracts import AgentEventKind as E, AgentStatus
from test_automatic_verification import Responses, write
from test_development_feedback import make_runtime


def git(workspace, *args):
    subprocess.run(["git", "-C", str(workspace), *args], check=True, capture_output=True)


def init_repo(workspace):
    git(workspace, "init", "-q")
    git(workspace, "config", "user.name", "Overhead Test")
    git(workspace, "config", "user.email", "overhead@example.test")


@pytest.mark.parametrize("argv,expected", [
    (["git", "grep", "-n", "Panel", "--", "src"], True),
    (["git", "-C", "frontend", "log", "--oneline", "-5"], True),
    (["git", "--no-pager", "diff", "--stat"], True),
    (["cmd", "/c", "git log --oneline -8 --stat | more"], True),
    (["cmd", "/c", "dir /b src && type package.json"], True),
    (["cmd", "/c", "findstr /s /n Panel src\\*.tsx"], True),
    (["rg", "-n", "Panel", "src"], True),
    (["git", "commit", "-m", "change"], False),
    (["git", "checkout", "main"], False),
    (["git", "stash"], False),
    (["cmd", "/c", "type a.txt > b.txt"], False),
    (["cmd", "/c", "dir && npm run build"], False),
    (["cmd", "/c", "dir /b \"%USERPROFILE%\""], False),
    (["node", "-e", "require('fs').readFileSync('a')"], False),
    ([sys.executable, "-c", "print(1)"], False),
    (["powershell", "-Command", "Get-ChildItem"], False),
    ([], False),
    (None, False),
])
def test_only_commands_that_cannot_write_skip_attribution(argv, expected):
    assert dv.read_only_command(argv) is expected


def test_one_status_run_gives_head_renames_and_untracked_sources(tmp_path):
    init_repo(tmp_path)
    (tmp_path / "Old.tsx").write_text("export const a = 1")
    (tmp_path / "keep.css").write_text("a {}")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-qm", "base")
    git(tmp_path, "mv", "Old.tsx", "New.tsx")
    (tmp_path / "keep.css").write_text("a { color: red }")
    (tmp_path / "fresh.py").write_text("x = 1")
    (tmp_path / "notes.md").write_text("not source")
    head = subprocess.run(["git", "-C", str(tmp_path), "rev-parse", "HEAD"], capture_output=True,
                          text=True, check=True).stdout.strip()

    files, discovery, observed_head = dv.git_state(tmp_path)

    assert discovery == "available"
    assert observed_head == head
    # A rename reports both sides: the new file and the source that disappeared.
    assert set(files) == {"New.tsx", "Old.tsx", "keep.css", "fresh.py"}


def test_unborn_branch_has_no_head_but_still_lists_sources(tmp_path):
    init_repo(tmp_path)
    (tmp_path / "Panel.tsx").write_text("x")
    files, discovery, head = dv.git_state(tmp_path)
    assert (set(files), discovery, head) == ({"Panel.tsx"}, "available", "")


def test_read_only_commands_spawn_no_scans_and_writers_spawn_one_per_side(tmp_path, monkeypatch):
    calls = []
    real = dv.git_state

    def counting(workspace):
        calls.append(workspace)
        return real(workspace)

    monkeypatch.setattr(dv, "git_state", counting)
    platform = Responses([
        ModelResponse(tool_calls=(ToolCall("look", "exec", {"argv": ["git", "log", "--oneline"]}),)),
        ModelResponse(tool_calls=(ToolCall("edit", "exec", {
            "argv": [sys.executable, "-c", "open('note.txt', 'w').write('x')"]}),)),
        ModelResponse(text="Done"),
    ])
    runtime, session, workspace = make_runtime(tmp_path, platform)
    init_repo(workspace)
    try:
        result = runtime.start_turn(session.session_id, "Look, then write a note")
        assert result.status is AgentStatus.COMPLETED
        # One baseline at turn start, none for the read, one before and one after the writer.
        assert len(calls) == 3
    finally:
        runtime.close()


def test_every_model_request_reports_host_overhead_by_cause(tmp_path):
    platform = Responses([
        ModelResponse(tool_calls=(ToolCall("edit", "exec", {
            "argv": [sys.executable, "-c", "open('note.txt', 'w').write('x')"]}),)),
        ModelResponse(text="Done"),
    ])
    runtime, session, workspace = make_runtime(tmp_path, platform)
    init_repo(workspace)
    try:
        runtime.start_turn(session.session_id, "Write a note")
        requests = [e.data for e in runtime.store.events(session.session_id) if e.kind is E.MODEL_REQUESTED]
        assert "attribution_scan_ms" in requests[0]["host_overhead_ms"]  # the turn's baseline scan
        after_tool = requests[1]["host_overhead_ms"]
        assert {"attribution_scan_ms", "observe_ms", "result_bookkeeping_ms", "after_tools_ms"} <= set(after_tool)
        assert all(value >= 0 for value in after_tool.values())
    finally:
        runtime.close()


def test_change_by_someone_else_to_an_earlier_turns_file_does_not_block_this_turn(tmp_path):
    platform = Responses([write(), ModelResponse(text="Done"), ModelResponse(text="Done"), ModelResponse(text="Done"),
                          ModelResponse(text="Here is the status.")])
    runtime, session, workspace = make_runtime(tmp_path, platform)
    try:
        runtime.start_turn(session.session_id, "Fix the UI")
        first_turn = runtime.store.load(session.session_id).current_turn_id
        # The user (or another session sharing the checkout) edits the file between turns.
        (workspace / "src" / "Panel.tsx").write_text("export const Panel = () => <button>Edited by hand</button>")
        result = runtime.start_turn(session.session_id, "What is the status?")
        assert result.status is AgentStatus.COMPLETED
        assert result.final_text.startswith("Here is the status.")
        second = [e for e in runtime.store.events(session.session_id) if e.turn_id == result.turn_id]
        assert not any(e.kind is E.VERIFICATION_FINISH_REQUESTED for e in second)
        summary = runtime.development_verification.summary(runtime.store.load(session.session_id))
        assert [s["origin_turn_id"] for s in summary["subjects"]] == [first_turn]
    finally:
        runtime.close()


def test_this_turns_own_edit_of_an_earlier_file_is_still_its_obligation(tmp_path):
    platform = Responses([write(), ModelResponse(text="Done"), ModelResponse(text="Done"), ModelResponse(text="Done"),
                          write(call="again", text="export const Panel = () => <button>Again</button>"),
                          ModelResponse(text="Edited again.")])
    runtime, session, _ = make_runtime(tmp_path, platform)
    try:
        runtime.start_turn(session.session_id, "Fix the UI")
        result = runtime.start_turn(session.session_id, "Change the label")
        second = [e for e in runtime.store.events(session.session_id) if e.turn_id == result.turn_id]
        assert any(e.kind is E.VERIFICATION_FINISH_REQUESTED for e in second)
    finally:
        runtime.close()
