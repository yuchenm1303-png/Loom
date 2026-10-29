from __future__ import annotations

import base64
import json
import subprocess
import sys
from pathlib import Path

import pytest

from app.agent_runtime import (
    AgentRuntime,
    AgentStatus,
    FileAgentSessionStore,
    PermissionMode,
    ProcessStore,
    SandboxBackend,
    SandboxManager,
    SandboxMode,
    SandboxPolicy,
)
from app.agent_runtime.shell_environment import ShellEnvironmentPolicy
from app.agent_runtime.workspace_tools import loom_default_tools
from app.ai import AGENT_FAST_ROLE, ModelResponse


class StaticPlatform:
    def __init__(self, text: str = "done") -> None:
        self.text = text
        self.requests = []

    def execute_chat(self, profile_id, request):
        self.requests.append((profile_id, request))
        return ModelResponse(text=self.text)


def _fake_bwrap_manager(policy=SandboxPolicy.AUTO) -> SandboxManager:
    return SandboxManager(
        policy=policy,
        bubblewrap_executable="/usr/bin/bwrap",
        probe_backend=False,
        system_name="Linux",
    )


def _fake_windows_mxc_manager(policy=SandboxPolicy.AUTO) -> SandboxManager:
    return SandboxManager(
        policy=policy,
        windows_mxc_executable=r"C:\loom-test\wxc-exec.exe",
        probe_backend=False,
        system_name="Windows",
    )


def _decode_mxc_config(argv: tuple[str, ...]) -> dict[str, object]:
    assert argv[1] == "--config-base64"
    return json.loads(base64.b64decode(argv[2]).decode("utf-8"))


def test_workspace_sandbox_plan_wraps_command_and_protects_metadata(tmp_path):
    workspace = tmp_path / "project"
    workspace.mkdir()
    (workspace / ".git").mkdir()
    nested = workspace / "src"
    nested.mkdir()
    manager = _fake_bwrap_manager()

    prepared = manager.prepare(
        argv=("python", "-V"),
        cwd=nested,
        workspace=workspace,
        permission_mode=PermissionMode.WORKSPACE,
    )

    assert prepared.snapshot.enforced is True
    assert prepared.snapshot.backend is SandboxBackend.BUBBLEWRAP
    assert prepared.snapshot.mode is SandboxMode.WORKSPACE
    assert prepared.cwd == Path("/")
    assert prepared.argv[0] == "/usr/bin/bwrap"
    assert "--bind" in prepared.argv
    assert str(workspace.resolve()) in prepared.argv
    assert str((workspace / ".git").resolve()) in prepared.argv
    assert prepared.argv[-2:] == ("python", "-V")


def test_windows_workspace_mxc_plan_uses_current_schema_and_blocks_network(tmp_path):
    workspace = (tmp_path / "project").resolve()
    workspace.mkdir()
    (workspace / ".git").mkdir()
    manager = _fake_windows_mxc_manager()

    prepared = manager.prepare(
        argv=("python", "-c", "print('ok')"),
        cwd=workspace,
        workspace=workspace,
        permission_mode=PermissionMode.WORKSPACE,
        environment={
            "PATH": r"C:\Python312;C:\Windows\System32",
            "SYSTEMROOT": r"C:\Windows",
            "LOOM_EXEC_VISIBLE": "works",
            "OPENAI_API_KEY": "synthetic-secret-must-not-cross",
        },
    )

    assert prepared.snapshot.enforced is True
    assert prepared.snapshot.backend is SandboxBackend.WINDOWS_MXC
    assert prepared.snapshot.mode is SandboxMode.WORKSPACE
    assert prepared.snapshot.network_isolated is True
    assert prepared.argv[0] == r"C:\loom-test\wxc-exec.exe"

    config = _decode_mxc_config(prepared.argv)
    assert config["version"] == "0.8.0-alpha"
    assert config["containment"] == "process"
    assert config["network"] == {
        "egress": {"default": "deny"},
        "ingress": {"default": "deny", "hostLoopback": "deny"},
    }
    # Win32k stays reachable (PowerShell/git/where.exe die with 0xC0000142
    # without it); clipboard and input injection do not.
    assert config["ui"] == {"disable": False, "clipboard": "none", "injection": False}
    # Tool folders the prep script opened are readable through this capability.
    assert config["processContainer"]["capabilities"] == ["loomSandboxTools"]
    filesystem = config["filesystem"]
    assert str(workspace) in filesystem["readwritePaths"]
    assert str((workspace / ".git").resolve()) in filesystem["readonlyPaths"]
    process = config["process"]
    assert process["cwd"] == str(workspace)
    assert "LOOM_EXEC_VISIBLE=works" in process["env"]
    assert "synthetic-secret-must-not-cross" not in repr(config)
    assert not any("API_KEY=" in value for value in process["env"])


def test_windows_read_only_mxc_plan_does_not_grant_workspace_write(tmp_path):
    workspace = (tmp_path / "project").resolve()
    workspace.mkdir()
    manager = _fake_windows_mxc_manager()

    prepared = manager.prepare(
        argv=("python", "-V"),
        cwd=workspace,
        workspace=workspace,
        permission_mode=PermissionMode.READ_ONLY,
        environment={"PATH": r"C:\Python312"},
    )

    config = _decode_mxc_config(prepared.argv)
    filesystem = config["filesystem"]
    assert filesystem["readwritePaths"] == []
    assert str(workspace) in filesystem["readonlyPaths"]
    assert prepared.snapshot.mode is SandboxMode.READ_ONLY


def test_windows_mxc_full_access_never_wraps_command(tmp_path):
    workspace = (tmp_path / "project").resolve()
    workspace.mkdir()
    manager = _fake_windows_mxc_manager()

    prepared = manager.prepare(
        argv=("python", "-V"),
        cwd=workspace,
        workspace=workspace,
        permission_mode=PermissionMode.FULL_ACCESS,
        environment={"PATH": r"C:\Python312"},
    )

    assert prepared.argv == ("python", "-V")
    assert prepared.snapshot.mode is SandboxMode.DISABLED
    assert prepared.snapshot.enforced is False
    assert prepared.snapshot.backend is SandboxBackend.NONE


def test_windows_mxc_probe_requires_a_real_isolation_tier(monkeypatch):
    def probe(output):
        return lambda args, **kwargs: _FakeProbe(args, output=output, **kwargs)

    monkeypatch.setattr(subprocess, "Popen", probe('{"tier":"base-container"}'))
    manager = SandboxManager(
        windows_mxc_executable=r"C:\mxc\wxc-exec.exe",
        system_name="Windows",
    )
    assert manager._backend_available is True
    assert "base-container" in manager._backend_reason

    monkeypatch.setattr(subprocess, "Popen", probe('{"tier":"unknown"}'))
    manager = SandboxManager(
        windows_mxc_executable=r"C:\mxc\wxc-exec.exe",
        system_name="Windows",
    )
    assert manager._backend_available is False
    assert "usable isolation tier" in manager._backend_reason


class _FakeProbe:
    def __init__(self, args, *, output="", returncode=0, hang=False, **kwargs):
        assert kwargs["stdout"] != subprocess.PIPE
        assert kwargs["stderr"] != subprocess.PIPE
        self.args, self.returncode, self.hang, self.killed = args, returncode, hang, False
        kwargs["stdout"].write(output)

    def wait(self, timeout=None):
        if self.hang:
            raise subprocess.TimeoutExpired(self.args, timeout)
        return self.returncode

    def kill(self):
        self.killed = True


def test_windows_probe_uses_files_so_inherited_pipes_cannot_block_startup(monkeypatch):
    seen = []
    def popen(args, **kwargs):
        seen.append(kwargs)
        return _FakeProbe(args, output='{"tier":"base-container"}', **kwargs)

    monkeypatch.setattr(subprocess, "Popen", popen)
    ok, reason = SandboxManager._probe_windows_mxc("wxc-exec")
    assert ok and "base-container" in reason and seen


def test_windows_probe_timeout_fails_closed_without_interrupting_acl_recovery(monkeypatch):
    probes = []
    def popen(args, **kwargs):
        probes.append(_FakeProbe(args, hang=True, **kwargs))
        return probes[-1]

    monkeypatch.setattr(subprocess, "Popen", popen)
    ok, reason = SandboxManager._probe_windows_mxc("wxc-exec")
    # A probe killed while it restores leftover ACEs leaves them for the next
    # probe, which then times out too. It fails closed and is left to finish.
    assert not ok and "did not finish" in reason
    assert probes and not probes[0].killed


def test_full_access_intentionally_bypasses_os_sandbox(tmp_path):
    workspace = tmp_path / "project"
    workspace.mkdir()
    manager = _fake_bwrap_manager()

    prepared = manager.prepare(
        argv=("python", "-V"),
        cwd=workspace,
        workspace=workspace,
        permission_mode=PermissionMode.FULL_ACCESS,
    )

    assert prepared.argv == ("python", "-V")
    assert prepared.cwd == workspace.resolve()
    assert prepared.snapshot.mode is SandboxMode.DISABLED
    assert prepared.snapshot.enforced is False


def test_required_sandbox_fails_closed_when_backend_unavailable(tmp_path, monkeypatch):
    monkeypatch.delenv("LOOM_WINDOWS_SANDBOX_EXECUTABLE", raising=False)
    workspace = tmp_path / "project"
    workspace.mkdir()
    manager = SandboxManager(
        policy=SandboxPolicy.REQUIRED,
        system_name="Windows",
        probe_backend=False,
    )

    with pytest.raises(RuntimeError, match="required but unavailable"):
        manager.prepare(
            argv=("python", "-V"),
            cwd=workspace,
            workspace=workspace,
            permission_mode=PermissionMode.WORKSPACE,
        )


def test_process_store_reports_honest_unsandboxed_fallback(tmp_path, monkeypatch):
    monkeypatch.delenv("LOOM_WINDOWS_SANDBOX_EXECUTABLE", raising=False)
    workspace = tmp_path / "project"
    workspace.mkdir()
    manager = SandboxManager(
        policy=SandboxPolicy.AUTO,
        system_name="Windows",
        probe_backend=False,
    )
    store = ProcessStore(sandbox_manager=manager)

    snapshot = store.run(
        session_id="session-1",
        argv=(sys.executable, "-c", "print('sandbox-fallback-ok')"),
        cwd=workspace,
        workspace=workspace,
        permission_mode=PermissionMode.WORKSPACE.value,
        timeout_seconds=30,
    )

    assert snapshot.returncode == 0
    assert "sandbox-fallback-ok" in snapshot.stdout
    assert snapshot.sandbox.enforced is False
    assert snapshot.sandbox.backend is SandboxBackend.NONE
    assert "wxc-exec" in snapshot.sandbox.reason.casefold()


def test_process_store_passes_the_exact_sanitized_child_environment_to_sandbox(
    tmp_path, monkeypatch
):
    workspace = tmp_path / "project"
    workspace.mkdir()

    class RecordingSandboxManager(SandboxManager):
        def __init__(self):
            super().__init__(policy=SandboxPolicy.OFF)
            self.environment = None

        def prepare(self, *args, environment=None, **kwargs):
            self.environment = dict(environment or {})
            return super().prepare(*args, environment=environment, **kwargs)

    manager = RecordingSandboxManager()
    store = ProcessStore(sandbox_manager=manager)
    monkeypatch.setenv("LOOM_TEST_API_KEY", "synthetic-inherited-secret")

    snapshot = store.run(
        session_id="session-env",
        argv=(sys.executable, "-c", "print('env-ok')"),
        cwd=workspace,
        workspace=workspace,
        permission_mode=PermissionMode.FULL_ACCESS.value,
        timeout_seconds=30,
        env={"LOOM_EXEC_VISIBLE": "yes"},
        # Inheriting secret-shaped names is the Codex-aligned default, so the
        # strip this test pins has to be requested explicitly.
        environment_policy=ShellEnvironmentPolicy(ignore_default_excludes=False),
    )

    assert snapshot.returncode == 0
    assert manager.environment is not None
    assert manager.environment["LOOM_EXEC_VISIBLE"] == "yes"
    assert "LOOM_TEST_API_KEY" not in manager.environment


def test_default_runtime_freezes_sandbox_state_and_registers_status_tool(tmp_path, monkeypatch):
    monkeypatch.delenv("LOOM_WINDOWS_SANDBOX_EXECUTABLE", raising=False)
    workspace = tmp_path / "project"
    workspace.mkdir()
    store = FileAgentSessionStore(tmp_path / "state")
    runtime = AgentRuntime(
        platform=StaticPlatform(),
        store=store,
        tools=loom_default_tools(),
        sandbox_manager=SandboxManager(
            policy=SandboxPolicy.AUTO,
            system_name="Windows",
            probe_backend=False,
        ),
    )
    session = runtime.create_session(
        AGENT_FAST_ROLE.role_id,
        workspace_dir=workspace,
        permission_mode=PermissionMode.WORKSPACE,
    )

    step = runtime._build_step_context(session, next_model_step=True)

    assert step.world_state.sandbox is not None
    assert step.world_state.sandbox.mode is SandboxMode.WORKSPACE
    assert step.world_state.sandbox.enforced is False
    assert runtime.tools.get("get_sandbox_status") is not None


def test_recovery_terminates_ephemeral_processes_before_history_repair(tmp_path):
    workspace = tmp_path / "project"
    workspace.mkdir()
    store = FileAgentSessionStore(tmp_path / "state")
    runtime = AgentRuntime(
        platform=StaticPlatform(),
        store=store,
        tools=loom_default_tools(),
        sandbox_manager=SandboxManager(policy=SandboxPolicy.OFF),
    )
    session = runtime.create_session(
        AGENT_FAST_ROLE.role_id,
        workspace_dir=workspace,
        permission_mode=PermissionMode.FULL_ACCESS,
    )
    managed = runtime.process_store.start(
        session_id=session.session_id,
        argv=(sys.executable, "-c", "import time; time.sleep(60)"),
        cwd=workspace,
        workspace=workspace,
        permission_mode=PermissionMode.FULL_ACCESS.value,
        timeout_seconds=120,
    )
    assert managed.running
    session.status = AgentStatus.RUNNING
    session.current_turn_id = "interrupted-turn"
    store.save(session)

    result = runtime.recover_interrupted(session.session_id)

    assert result.status is AgentStatus.INTERRUPTED
    assert managed.running is False
    runtime.close()


def test_turn_scratch_directory_is_reachable_from_sandboxed_commands(tmp_path):
    # The prompt sends probe scripts to the run scratch directory and the file
    # tools write there; a sandboxed command must be able to run them.
    workspace, scratch = tmp_path / "project", tmp_path / "state" / "run-artifacts" / "turn"
    workspace.mkdir()
    scratch.mkdir(parents=True)
    mxc = _fake_windows_mxc_manager()
    written = _decode_mxc_config(mxc.prepare(argv=("python", "probe.py"), cwd=workspace, workspace=workspace,
                                             permission_mode=PermissionMode.WORKSPACE, scratch=scratch).argv)
    assert str(scratch.resolve()) in written["filesystem"]["readwritePaths"]
    read_only = _decode_mxc_config(mxc.prepare(argv=("python", "probe.py"), cwd=workspace, workspace=workspace,
                                               permission_mode=PermissionMode.READ_ONLY, scratch=scratch).argv)
    assert str(scratch.resolve()) in read_only["filesystem"]["readonlyPaths"]
    assert read_only["filesystem"]["readwritePaths"] == []
    bwrap = _fake_bwrap_manager().prepare(argv=("python", "probe.py"), cwd=workspace, workspace=workspace,
                                          permission_mode=PermissionMode.WORKSPACE, scratch=scratch).argv
    assert ("--bind", str(scratch.resolve()), str(scratch.resolve())) in zip(bwrap, bwrap[1:], bwrap[2:])
    # Not yet created: nothing is granted (and nothing is created on its behalf).
    missing = _decode_mxc_config(mxc.prepare(argv=("python", "probe.py"), cwd=workspace, workspace=workspace,
                                             permission_mode=PermissionMode.WORKSPACE, scratch=tmp_path / "absent").argv)
    assert all("absent" not in path for path in missing["filesystem"]["readwritePaths"])


def test_exec_grants_the_sessions_run_artifacts_not_only_this_turns(tmp_path):
    from types import SimpleNamespace
    from app.agent_runtime.process_tools import _scratch_root
    turn = tmp_path / "session" / "run-artifacts" / "turn-2"
    context = SimpleNamespace(services={"run_scratch_dir": turn})
    # A review continued over episodes runs probes an earlier turn wrote.
    assert _scratch_root(context) == tmp_path / "session" / "run-artifacts"
    assert _scratch_root(SimpleNamespace(services={})) is None
