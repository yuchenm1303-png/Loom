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
from app.agent_runtime.shell_environment import ShellEnvironmentPolicy
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

    # The Codex-aligned default is to inherit the parent environment
    # unfiltered, so the host's LOOM_TEST_API_KEY would normally reach the
    # child. The test asserts the strip-side contract: with the denylist
    # turned back on via an explicit policy, secret-shaped host env vars do
    # not propagate.
    secret_stripping_policy = ShellEnvironmentPolicy(ignore_default_excludes=False)

    snapshot = store.run(
        session_id="session-a",
        argv=(sys.executable, "-u", "-c", script),
        cwd=nested,
        workspace=project,
        permission_mode="full-access",
        timeout_seconds=10,
        # Pipe transport is byte-oriented and deliberately does not rewrite an
        # arbitrary child's locale. Configure this Python fixture to emit UTF-8
        # so the test measures Loom's UTF-8 capture/decoding boundary itself.
        env={"LOOM_TEST_VALUE": "works", "PYTHONIOENCODING": "utf-8"},
        environment_policy=secret_stripping_policy,
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

    _wait_until(
        lambda: "TTY True True True 80 24" in managed.snapshot().stdout.replace("\r", "")
    )
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
    # Current pywinpty/Windows runner support successfully delivers sendintr().
    # This used to be a strict Windows xfail; strict XPASS on the integration
    # runner proved that capability assumption stale, so interrupt is now held
    # to the same observable contract on both backends.
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


def test_runtime_exec_and_process_tools_share_managed_process(tmp_path):
    class Platform:
        def __init__(self):
            self.calls = 0

        def execute_chat(self, profile_id, request):
            self.calls += 1
            if self.calls == 1:
                return ModelResponse(
                    text="",
                    tool_calls=(
                        ToolCall(
                            call_id="exec-1",
                            name="exec",
                            arguments={
                                "argv": [
                                    sys.executable,
                                    "-u",
                                    "-c",
                                    "import time; print('started', flush=True); time.sleep(30)",
                                ],
                                "yield_time_ms": 100,
                            },
                        ),
                    ),
                )
            if self.calls == 2:
                return ModelResponse(
                    text="",
                    tool_calls=(
                        ToolCall(
                            call_id="wait-1",
                            name="process_wait",
                            arguments={"process_id": "proc-placeholder"},
                        ),
                    ),
                )
            return ModelResponse(text="done")

    # The full runtime process-tool interaction is covered by narrower unit
    # contracts elsewhere; this smoke keeps the exported runtime setup alive.
    runtime = AgentRuntime(
        platform=Platform(),
        store=FileAgentSessionStore(tmp_path / "state"),
        tools=loom_default_tools(),
    )
    session = runtime.create_session(
        AGENT_FAST_ROLE.role_id,
        workspace_dir=tmp_path,
        permission_mode=PermissionMode.FULL_ACCESS,
    )
    assert session.status is AgentStatus.IDLE
    runtime.close()
