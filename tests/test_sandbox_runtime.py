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
    assert config["ui"] == {"disable": True, "clipboard": "none", "injection": False}
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
    def completed(stdout: str, returncode: int = 0):
        return subprocess.CompletedProcess(["wxc-exec", "--probe"], returncode, stdout, "")

    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: completed('{"tier":"base-container"}'))
    manager = SandboxManager(
        windows_mxc_executable=r"C:\mxc\wxc-exec.exe",
        system_name="Windows",
    )
    assert manager._backend_available is True
    assert "base-container" in manager._backend_reason

    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: completed('{"tier":"unknown"}'))
    manager = SandboxManager(
        windows_mxc_executable=r"C:\mxc\wxc-exec.exe",
        system_name="Windows",
    )
    assert manager._backend_available is False
    assert "usable isolation tier" in manager._backend_reason


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
