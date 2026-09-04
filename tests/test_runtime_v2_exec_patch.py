from __future__ import annotations

import sys
import time

from app.agent_runtime import (
    AgentEventKind,
    AgentRuntime,
    AgentStatus,
    FileAgentSessionStore,
    PermissionMode,
    ProcessStore,
)
from app.agent_runtime.workspace_tools import loom_default_tools
from app.ai import AGENT_FAST_ROLE, ModelResponse, ToolCall


class ScriptedPlatform:
    def __init__(self, responses):
        self.responses = list(responses)

    def execute_chat(self, _profile_id, _request):
        if not self.responses:
            raise AssertionError("scripted platform ran out of responses")
        return self.responses.pop(0)


def test_process_store_supports_stdin_and_final_transcript(tmp_path):
    store = ProcessStore()
    script = "import sys; line=sys.stdin.readline(); print('echo:' + line.strip())"
    process = store.start(
        session_id="session-a",
        argv=(sys.executable, "-u", "-c", script),
        cwd=tmp_path,
        permission_mode="full-access",
        timeout_seconds=10,
    )

    process.write_stdin("hello\n")
    snapshot = process.wait()

    assert snapshot.running is False
    assert snapshot.returncode == 0
    assert "echo:hello" in snapshot.stdout


def test_process_store_timeout_terminates_process_tree(tmp_path):
    store = ProcessStore()
    started = time.monotonic()
    snapshot = store.run(
        session_id="session-a",
        argv=(sys.executable, "-u", "-c", "import time; time.sleep(30)"),
        cwd=tmp_path,
        permission_mode="full-access",
        timeout_seconds=1,
    )

    assert snapshot.running is False
    assert snapshot.timed_out is True
    assert time.monotonic() - started < 5


def test_process_store_cancellation_terminates_process(tmp_path):
    store = ProcessStore()
    started = time.monotonic()
    snapshot = store.run(
        session_id="session-a",
        argv=(sys.executable, "-u", "-c", "import time; time.sleep(30)"),
        cwd=tmp_path,
        permission_mode="full-access",
        timeout_seconds=30,
        cancel_check=lambda: time.monotonic() - started > 0.15,
    )

    assert snapshot.running is False
    assert time.monotonic() - started < 5


def test_runtime_emits_process_lifecycle_events(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    platform = ScriptedPlatform(
        [
            ModelResponse(
                tool_calls=(
                    ToolCall(
                        call_id="run-managed",
                        name="run_workspace_command",
                        arguments={
                            "argv": [sys.executable, "-u", "-c", "print('managed-ok')"],
                            "timeout_seconds": 10,
                        },
                    ),
                )
            ),
            ModelResponse(text="done"),
        ]
    )
    store = FileAgentSessionStore(tmp_path / "state")
    runtime = AgentRuntime(platform=platform, store=store, tools=loom_default_tools())
    session = runtime.create_session(
        AGENT_FAST_ROLE.role_id,
        workspace_dir=project,
        permission_mode=PermissionMode.FULL_ACCESS,
    )

    result = runtime.start_turn(session.session_id, "Run the command.")

    assert result.status is AgentStatus.COMPLETED
    events = store.events(session.session_id)
    kinds = [event.kind for event in events]
    assert AgentEventKind.PROCESS_STARTED in kinds
    assert AgentEventKind.PROCESS_OUTPUT in kinds
    assert AgentEventKind.PROCESS_EXITED in kinds
    completed = [
        event for event in events
        if event.kind is AgentEventKind.TOOL_COMPLETED
        and event.data.get("tool") == "run_workspace_command"
    ]
    assert "managed-ok" in completed[-1].data["data"]["stdout"]
    assert completed[-1].data["data"]["process_id"].startswith("proc-")


def test_permission_change_terminates_stale_background_process(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    runtime = AgentRuntime(
        platform=ScriptedPlatform([]),
        store=FileAgentSessionStore(tmp_path / "state"),
        tools=loom_default_tools(),
    )
    session = runtime.create_session(
        AGENT_FAST_ROLE.role_id,
        workspace_dir=project,
        permission_mode=PermissionMode.FULL_ACCESS,
    )
    process = runtime.process_store.start(
        session_id=session.session_id,
        argv=(sys.executable, "-u", "-c", "import time; time.sleep(30)"),
        cwd=project,
        permission_mode="full-access",
        timeout_seconds=30,
    )
    assert process.running

    runtime.set_permission_mode(session.session_id, PermissionMode.READ_ONLY)

    assert process.running is False


def test_apply_patch_updates_file_and_emits_turn_diff(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    target = project / "sample.txt"
    target.write_text("hello Loom\n", encoding="utf-8")
    platform = ScriptedPlatform(
        [
            ModelResponse(
                tool_calls=(
                    ToolCall(
                        call_id="patch-1",
                        name="apply_patch",
                        arguments={
                            "changes": [
                                {
                                    "action": "update",
                                    "path": "sample.txt",
                                    "old_text": "hello Loom",
                                    "new_text": "hello Runtime v2",
                                },
                                {
                                    "action": "add",
                                    "path": "new.txt",
                                    "content": "created\n",
                                },
                            ]
                        },
                    ),
                )
            ),
            ModelResponse(text="patched"),
        ]
    )
    store = FileAgentSessionStore(tmp_path / "state")
    runtime = AgentRuntime(platform=platform, store=store, tools=loom_default_tools())
    session = runtime.create_session(
        AGENT_FAST_ROLE.role_id,
        workspace_dir=project,
        permission_mode=PermissionMode.WORKSPACE,
    )

    result = runtime.start_turn(session.session_id, "Patch the files.")

    assert result.status is AgentStatus.COMPLETED
    assert target.read_text(encoding="utf-8") == "hello Runtime v2\n"
    assert (project / "new.txt").read_text(encoding="utf-8") == "created\n"
    events = store.events(session.session_id)
    diff_events = [event for event in events if event.kind is AgentEventKind.TURN_DIFF_UPDATED]
    assert diff_events
    assert "sample.txt" in diff_events[-1].data["diff"]
    assert "new.txt" in diff_events[-1].data["diff"]


def test_apply_patch_validation_failure_is_atomic(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    first = project / "first.txt"
    second = project / "second.txt"
    first.write_text("one\n", encoding="utf-8")
    second.write_text("two\n", encoding="utf-8")
    platform = ScriptedPlatform(
        [
            ModelResponse(
                tool_calls=(
                    ToolCall(
                        call_id="patch-bad",
                        name="apply_patch",
                        arguments={
                            "changes": [
                                {
                                    "action": "update",
                                    "path": "first.txt",
                                    "old_text": "one",
                                    "new_text": "changed",
                                },
                                {
                                    "action": "update",
                                    "path": "second.txt",
                                    "old_text": "missing",
                                    "new_text": "never",
                                },
                            ]
                        },
                    ),
                )
            ),
            ModelResponse(text="patch failed safely"),
        ]
    )
    store = FileAgentSessionStore(tmp_path / "state")
    runtime = AgentRuntime(platform=platform, store=store, tools=loom_default_tools())
    session = runtime.create_session(
        AGENT_FAST_ROLE.role_id,
        workspace_dir=project,
        permission_mode=PermissionMode.WORKSPACE,
    )

    result = runtime.start_turn(session.session_id, "Try the patch.")

    assert result.status is AgentStatus.COMPLETED
    assert first.read_text(encoding="utf-8") == "one\n"
    assert second.read_text(encoding="utf-8") == "two\n"
    failures = [
        event for event in store.events(session.session_id)
        if event.kind is AgentEventKind.TOOL_FAILED
        and event.data.get("tool") == "apply_patch"
    ]
    assert failures
    assert "old_text was not found" in failures[-1].data["content"]


def test_apply_patch_move_is_tracked_without_git(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    (project / "old.txt").write_text("payload\n", encoding="utf-8")
    platform = ScriptedPlatform(
        [
            ModelResponse(
                tool_calls=(
                    ToolCall(
                        call_id="patch-move",
                        name="apply_patch",
                        arguments={
                            "changes": [
                                {
                                    "action": "move",
                                    "path": "old.txt",
                                    "move_to": "nested/new.txt",
                                }
                            ]
                        },
                    ),
                )
            ),
            ModelResponse(text="moved"),
        ]
    )
    store = FileAgentSessionStore(tmp_path / "state")
    runtime = AgentRuntime(platform=platform, store=store, tools=loom_default_tools())
    session = runtime.create_session(
        AGENT_FAST_ROLE.role_id,
        workspace_dir=project,
        permission_mode=PermissionMode.WORKSPACE,
    )

    result = runtime.start_turn(session.session_id, "Move the file.")

    assert result.status is AgentStatus.COMPLETED
    assert not (project / "old.txt").exists()
    assert (project / "nested" / "new.txt").read_text(encoding="utf-8") == "payload\n"
    diff = runtime.diff_trackers.snapshot(session.session_id, result.turn_id)
    assert set(diff.paths) == {"old.txt", "nested/new.txt"}
    assert "--- a/old.txt" in diff.diff
    assert "+++ b/nested/new.txt" in diff.diff


def test_legacy_write_tool_also_updates_turn_diff(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    platform = ScriptedPlatform(
        [
            ModelResponse(
                tool_calls=(
                    ToolCall(
                        call_id="legacy-write",
                        name="write_workspace_text",
                        arguments={"path": "tracked.txt", "text": "tracked\n"},
                    ),
                )
            ),
            ModelResponse(text="done"),
        ]
    )
    store = FileAgentSessionStore(tmp_path / "state")
    runtime = AgentRuntime(platform=platform, store=store, tools=loom_default_tools())
    session = runtime.create_session(
        AGENT_FAST_ROLE.role_id,
        workspace_dir=project,
        permission_mode=PermissionMode.WORKSPACE,
    )

    result = runtime.start_turn(session.session_id, "Write it.")

    diff = runtime.diff_trackers.snapshot(session.session_id, result.turn_id)
    assert diff.paths == ("tracked.txt",)
    assert "+tracked" in diff.diff
