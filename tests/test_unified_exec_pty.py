from __future__ import annotations

import os
import subprocess
import sys
import time

import pytest

from app.agent_runtime import (
    AgentEventKind,
    AgentRuntime,
    AgentStatus,
    FileAgentSessionStore,
    PermissionMode,
    ProcessStore,
)
from app.agent_runtime.process_runtime import ProcessState, safe_process_environment
from app.agent_runtime.sandbox import SandboxManager
from app.agent_runtime.workspace_tools import loom_default_tools
from app.ai import AGENT_FAST_ROLE, ModelResponse, ToolCall


def _wait_until(predicate, *, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.02)
    raise AssertionError("condition did not become true before timeout")


def _pid_alive(pid: int) -> bool:
    if os.name == "nt":
        result = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
            capture_output=True,
            text=True,
            check=False,
        )
        return str(pid) in result.stdout
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def test_exec_pipe_captures_stdout_stderr_exit_cwd_env_and_unicode(tmp_path, monkeypatch):
    project = tmp_path / "project"
    nested = project / "nested"
    nested.mkdir(parents=True)
    monkeypatch.setenv("LOOM_TEST_API_KEY", "must-not-leak")
    store = ProcessStore()
    script = (
        "import os,sys;"
        "print('cwd=' + os.path.basename(os.getcwd()));"
        "print('env=' + os.environ.get('LOOM_TEST_VALUE','missing'));"
        "print('secret=' + str('LOOM_TEST_API_KEY' in os.environ));"
        "print('你好 Loom');"
        "print('stderr-✓', file=sys.stderr)"
    )

    snapshot = store.run(
        session_id="session-a",
        argv=(sys.executable, "-u", "-c", script),
        cwd=nested,
        workspace=project,
        permission_mode="full-access",
        timeout_seconds=10,
        env={"LOOM_TEST_VALUE": "works"},
    )

    assert snapshot.state is ProcessState.EXITED
    assert snapshot.returncode == 0
    assert snapshot.backend == "pipe"
    assert snapshot.pty is False
    assert "cwd=nested" in snapshot.stdout
    assert "env=works" in snapshot.stdout
    assert "secret=False" in snapshot.stdout
    assert "你好 Loom" in snapshot.stdout
    assert "stderr-✓" in snapshot.stderr


def test_secret_like_env_override_is_rejected():
    with pytest.raises(ValueError, match="secret-like"):
        safe_process_environment({"SERVICE_API_KEY": "nope"})


def test_background_timeout_watchdog_terminates_without_poll(tmp_path):
    store = ProcessStore()
    managed = store.start(
        session_id="session-a",
        argv=(sys.executable, "-u", "-c", "import time; time.sleep(30)"),
        cwd=tmp_path,
        permission_mode="full-access",
        timeout_seconds=1,
    )

    _wait_until(lambda: not managed.running, timeout=5)
    snapshot = managed.wait()

    assert snapshot.state is ProcessState.TIMED_OUT
    assert snapshot.timed_out is True


def test_output_is_bounded_and_reports_dropped_bytes(tmp_path):
    store = ProcessStore()
    snapshot = store.run(
        session_id="session-a",
        argv=(sys.executable, "-u", "-c", "print('x' * 150_000)"),
        cwd=tmp_path,
        permission_mode="full-access",
        timeout_seconds=10,
    )

    assert len(snapshot.stdout) <= 100_000
    assert snapshot.output_truncated is True
    assert snapshot.dropped_bytes > 0
    assert snapshot.stdout.rstrip().endswith("x" * 100)


def test_pipe_interactive_write(tmp_path):
    store = ProcessStore()
    managed = store.start(
        session_id="session-a",
        argv=(
            sys.executable,
            "-u",
            "-c",
            "import sys; line=sys.stdin.readline(); print('echo:' + line.strip(), flush=True)",
        ),
        cwd=tmp_path,
        permission_mode="full-access",
        timeout_seconds=10,
    )

    managed.write_stdin("hello\n")
    snapshot = managed.wait()

    assert snapshot.returncode == 0
    assert "echo:hello" in snapshot.stdout


def test_real_pty_is_tty_interactive_unicode_and_resize(tmp_path):
    store = ProcessStore()
    script = (
        "import shutil,sys;"
        "print('TTY',sys.stdin.isatty(),sys.stdout.isatty(),sys.stderr.isatty(),"
        "shutil.get_terminal_size().columns,shutil.get_terminal_size().lines,flush=True);"
        "line=input();"
        "print('SIZE',shutil.get_terminal_size().columns,shutil.get_terminal_size().lines,flush=True);"
        "print('ECHO:' + line, flush=True)"
    )
    managed = store.start(
        session_id="session-a",
        argv=(sys.executable, "-u", "-c", script),
        cwd=tmp_path,
        permission_mode="full-access",
        timeout_seconds=15,
        pty=True,
        rows=24,
        cols=80,
    )

    _wait_until(lambda: "TTY" in managed.snapshot().stdout)
    first = managed.snapshot()
    assert first.pty is True
    assert "TTY True True True 80 24" in first.stdout.replace("\r", "")
    if os.name == "nt":
        assert first.backend == "windows-conpty"
    else:
        assert first.backend == "unix-pty"

    resized = managed.resize(rows=40, cols=100)
    assert resized.rows == 40
    assert resized.cols == 100
    managed.write_stdin("héllo 世界\r\n" if os.name == "nt" else "héllo 世界\n")
    snapshot = managed.wait()
    normalized = snapshot.stdout.replace("\r", "")

    assert snapshot.returncode == 0
    assert "SIZE 100 40" in normalized
    assert "ECHO:héllo 世界" in normalized


def test_resize_pipe_is_not_a_noop(tmp_path):
    store = ProcessStore()
    managed = store.start(
        session_id="session-a",
        argv=(sys.executable, "-u", "-c", "import time; time.sleep(2)"),
        cwd=tmp_path,
        permission_mode="full-access",
        timeout_seconds=10,
    )
    try:
        with pytest.raises(RuntimeError, match="PTY"):
            managed.resize(rows=40, cols=100)
    finally:
        managed.terminate_tree()


def test_pty_interrupt_is_distinct_from_terminate(tmp_path):
    store = ProcessStore()
    managed = store.start(
        session_id="session-a",
        argv=(
            sys.executable,
            "-u",
            "-c",
            "import time; print('ready', flush=True); time.sleep(30)",
        ),
        cwd=tmp_path,
        permission_mode="full-access",
        timeout_seconds=30,
        pty=True,
    )
    _wait_until(lambda: "ready" in managed.snapshot().stdout)

    managed.interrupt()
    _wait_until(lambda: not managed.running, timeout=5)
    snapshot = managed.wait()

    assert snapshot.state is ProcessState.INTERRUPTED
    assert snapshot.timed_out is False


def test_terminate_is_idempotent_and_cleans_process_tree(tmp_path):
    store = ProcessStore()
    child_script = "import time; time.sleep(30)"
    parent_script = (
        "import subprocess,sys,time;"
        f"p=subprocess.Popen([sys.executable,'-c',{child_script!r}]);"
        "print(p.pid, flush=True);"
        "time.sleep(30)"
    )
    managed = store.start(
        session_id="session-a",
        argv=(sys.executable, "-u", "-c", parent_script),
        cwd=tmp_path,
        permission_mode="full-access",
        timeout_seconds=30,
    )
    _wait_until(lambda: bool(managed.snapshot().stdout.strip()))
    child_pid = int(managed.snapshot().stdout.strip().splitlines()[0])
    assert _pid_alive(child_pid)

    managed.terminate_tree()
    managed.terminate_tree()
    snapshot = managed.wait()
    _wait_until(lambda: not _pid_alive(child_pid), timeout=5)

    assert snapshot.state is ProcessState.TERMINATED
    assert snapshot.running is False


def test_invalid_process_id_and_cross_session_control(tmp_path):
    store = ProcessStore()
    with pytest.raises(KeyError, match="unknown process"):
        store.get("proc-does-not-exist", session_id="session-a")

    managed = store.start(
        session_id="owner",
        argv=(sys.executable, "-u", "-c", "import time; time.sleep(30)"),
        cwd=tmp_path,
        permission_mode="full-access",
        timeout_seconds=30,
    )
    try:
        with pytest.raises(PermissionError):
            store.get(managed.process_id, session_id="other")
    finally:
        managed.terminate_tree()


def test_sandbox_prepare_happens_before_spawn(tmp_path):
    marker = tmp_path / "sandbox-prepared"

    class OrderedSandboxManager(SandboxManager):
        def prepare(self, **kwargs):
            marker.write_text("prepared", encoding="utf-8")
            return super().prepare(**kwargs)

    store = ProcessStore(sandbox_manager=OrderedSandboxManager())
    snapshot = store.run(
        session_id="session-a",
        argv=(
            sys.executable,
            "-u",
            "-c",
            "from pathlib import Path; print(Path('sandbox-prepared').exists())",
        ),
        cwd=tmp_path,
        workspace=tmp_path,
        permission_mode="full-access",
        timeout_seconds=10,
    )

    assert snapshot.returncode == 0
    assert "True" in snapshot.stdout


class _LargeOutputPlatform:
    def __init__(self) -> None:
        self.calls = 0

    def execute_chat(self, _profile_id, _request):
        self.calls += 1
        if self.calls == 1:
            return ModelResponse(
                tool_calls=(
                    ToolCall(
                        call_id="exec-large",
                        name="exec",
                        arguments={
                            "argv": [sys.executable, "-u", "-c", "print('z' * 180_000)"],
                            "timeout_seconds": 10,
                        },
                    ),
                )
            )
        return ModelResponse(text="done")


def test_durable_process_output_events_are_bounded(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    state = FileAgentSessionStore(tmp_path / "state")
    runtime = AgentRuntime(platform=_LargeOutputPlatform(), store=state, tools=loom_default_tools())
    session = runtime.create_session(
        AGENT_FAST_ROLE.role_id,
        workspace_dir=project,
        permission_mode=PermissionMode.FULL_ACCESS,
    )

    result = runtime.start_turn(session.session_id, "Produce large output.")

    assert result.status is AgentStatus.COMPLETED
    output_events = [
        event
        for event in state.events(session.session_id)
        if event.kind is AgentEventKind.PROCESS_OUTPUT
    ]
    assert output_events
    durable_chars = sum(
        len(str(event.data.get("stdout") or "")) + len(str(event.data.get("stderr") or ""))
        for event in output_events
    )
    assert durable_chars <= 101_000
    exit_events = [
        event
        for event in state.events(session.session_id)
        if event.kind is AgentEventKind.PROCESS_EXITED
    ]
    assert exit_events
    assert exit_events[-1].data.get("output_truncated") is True


class _PermissionBoundaryPlatform:
    def __init__(self) -> None:
        self.calls = 0

    def execute_chat(self, _profile_id, _request):
        self.calls += 1
        if self.calls == 1:
            return ModelResponse(
                tool_calls=(
                    ToolCall(
                        call_id="exec-denied",
                        name="exec",
                        arguments={"argv": [sys.executable, "-c", "print('must-not-run')"]},
                    ),
                )
            )
        return ModelResponse(text="permission handled")


def test_permission_engine_blocks_exec_before_spawn(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    state = FileAgentSessionStore(tmp_path / "state")
    runtime = AgentRuntime(
        platform=_PermissionBoundaryPlatform(),
        store=state,
        tools=loom_default_tools(),
    )
    session = runtime.create_session(
        AGENT_FAST_ROLE.role_id,
        workspace_dir=project,
        permission_mode=PermissionMode.READ_ONLY,
    )
    spawned = False
    original_start = runtime.process_store.start

    def tracked_start(**kwargs):
        nonlocal spawned
        spawned = True
        return original_start(**kwargs)

    runtime.process_store.start = tracked_start  # type: ignore[method-assign]
    result = runtime.start_turn(session.session_id, "Try the command.")

    assert result.status is AgentStatus.COMPLETED
    assert spawned is False
    events = state.events(session.session_id)
    assert any(event.kind is AgentEventKind.TOOL_DENIED for event in events)


def test_exec_tools_and_compatibility_aliases_are_registered():
    names = {tool.name for tool in loom_default_tools().all()}
    assert {
        "exec",
        "exec_wait",
        "exec_write",
        "exec_resize",
        "exec_interrupt",
        "exec_terminate",
        "run_workspace_command",
        "start_workspace_command",
        "poll_workspace_process",
        "write_workspace_process",
        "interrupt_workspace_process",
        "terminate_workspace_process",
    } <= names


@pytest.mark.skipif(os.name == "nt", reason="Unix-specific process-group/PTY backend contract")
def test_unix_pty_uses_own_process_group(tmp_path):
    store = ProcessStore()
    managed = store.start(
        session_id="session-a",
        argv=(sys.executable, "-u", "-c", "import os; print(os.getpid(), os.getpgrp(), flush=True)"),
        cwd=tmp_path,
        permission_mode="full-access",
        timeout_seconds=10,
        pty=True,
    )
    snapshot = managed.wait()
    numbers = [int(part) for part in snapshot.stdout.replace("\r", "").split() if part.isdigit()]

    assert snapshot.backend == "unix-pty"
    assert numbers[0] == numbers[1] == managed.backend.pid


@pytest.mark.skipif(os.name != "nt", reason="Windows ConPTY contract")
def test_windows_conpty_backend_is_real_and_unicode(tmp_path):
    store = ProcessStore()
    managed = store.start(
        session_id="session-a",
        argv=(
            sys.executable,
            "-u",
            "-c",
            "import sys; print(sys.stdout.isatty(), 'Windows-你好', flush=True)",
        ),
        cwd=tmp_path,
        permission_mode="full-access",
        timeout_seconds=10,
        pty=True,
    )
    snapshot = managed.wait()

    assert snapshot.backend == "windows-conpty"
    assert snapshot.pty is True
    assert "True Windows-你好" in snapshot.stdout.replace("\r", "")
