from __future__ import annotations

from pathlib import Path

import pytest

import app.agent_runtime.process_runtime as process_runtime
from app.agent_runtime.contracts import PermissionMode
from app.agent_runtime.permissions import permission_snapshot
from app.agent_runtime.process_runtime import ProcessStore
from app.agent_runtime.sandbox import SandboxBackend, SandboxManager, SandboxPolicy
from app.agent_runtime.sandbox_attempt import (
    SandboxAttempt,
    current_sandbox_attempt,
    ensure_attempt_aware_sandbox_manager,
    sandbox_attempt_scope,
)
from app.agent_runtime.sandbox_failure import SandboxExecutionError, SandboxFailureKind
from app.agent_runtime.shell_environment import ShellEnvironmentPolicy


def _manager(*, policy: SandboxPolicy = SandboxPolicy.AUTO):
    base = SandboxManager(
        policy=policy,
        bubblewrap_executable="/synthetic/bwrap",
        probe_backend=False,
        system_name="Linux",
    )
    return ensure_attempt_aware_sandbox_manager(base)


def _prepare(manager, tmp_path: Path):
    return manager.prepare(
        argv=("synthetic-program", "arg"),
        cwd=tmp_path,
        workspace=tmp_path,
        permissions=permission_snapshot(PermissionMode.WORKSPACE),
        environment={},
    )


def test_escalated_attempt_changes_only_current_planning_scope(tmp_path: Path):
    manager = _manager()

    initial = _prepare(manager, tmp_path)
    assert initial.snapshot.enforced is True
    assert initial.snapshot.backend is SandboxBackend.BUBBLEWRAP
    assert initial.argv[0] == "/synthetic/bwrap"
    assert current_sandbox_attempt() == SandboxAttempt.initial()

    with sandbox_attempt_scope(SandboxAttempt.escalated("proven containment rejection")) as attempt:
        escalated = _prepare(manager, tmp_path)
        assert current_sandbox_attempt() == attempt
        assert escalated.argv == ("synthetic-program", "arg")
        assert escalated.cwd == tmp_path.resolve()
        assert escalated.snapshot.enforced is False
        assert escalated.snapshot.backend is SandboxBackend.NONE
        assert escalated.snapshot.network_isolated is False
        assert "escalation attempt 1" in escalated.snapshot.reason
        assert "proven containment rejection" in escalated.snapshot.reason

    restored = _prepare(manager, tmp_path)
    assert current_sandbox_attempt() == SandboxAttempt.initial()
    assert restored.snapshot.enforced is True
    assert restored.snapshot.backend is SandboxBackend.BUBBLEWRAP
    assert restored.argv[0] == "/synthetic/bwrap"


def test_required_policy_cannot_be_bypassed_by_escalated_attempt(tmp_path: Path):
    manager = _manager(policy=SandboxPolicy.REQUIRED)

    with sandbox_attempt_scope(SandboxAttempt.escalated("retry request")):
        with pytest.raises(SandboxExecutionError) as caught:
            _prepare(manager, tmp_path)

    assert caught.value.kind is SandboxFailureKind.CONFIGURATION
    assert caught.value.escalatable is False


class _FakeBackend:
    name = "fake"
    pty = False

    @property
    def pid(self) -> int:
        return 1

    def poll(self):
        return 0

    def read_stdout(self) -> str:
        return ""

    def read_stderr(self) -> str:
        return ""

    def write(self, _text: str) -> None:
        return None

    def close_stdin(self) -> None:
        return None

    def send_control(self, _control: str) -> None:
        return None

    def resize(self, _rows: int, _cols: int) -> None:
        return None

    def interrupt(self) -> None:
        return None

    def terminate_tree(self, *, grace_seconds: float = 1.0) -> None:
        del grace_seconds
        return None

    def close(self) -> None:
        return None


def test_process_store_consumes_active_attempt_without_global_policy_mutation(monkeypatch, tmp_path: Path):
    manager = _manager()
    store = ProcessStore(
        sandbox_manager=manager,
        environment_policy=ShellEnvironmentPolicy(inherit="none"),
    )
    launches: list[tuple[str, ...]] = []

    def fake_spawn(**kwargs):
        launches.append(tuple(kwargs["argv"]))
        return _FakeBackend()

    monkeypatch.setattr(process_runtime, "_spawn_backend", fake_spawn)
    permissions = permission_snapshot(PermissionMode.WORKSPACE)

    with sandbox_attempt_scope(SandboxAttempt.escalated("typed retry")):
        escalated = store.start(
            session_id="session-1",
            argv=("synthetic-program", "arg"),
            cwd=tmp_path,
            workspace=tmp_path,
            permission_mode=PermissionMode.WORKSPACE.value,
            permissions=permissions,
            timeout_seconds=30,
        )

    initial = store.start(
        session_id="session-1",
        argv=("synthetic-program", "arg"),
        cwd=tmp_path,
        workspace=tmp_path,
        permission_mode=PermissionMode.WORKSPACE.value,
        permissions=permissions,
        timeout_seconds=30,
    )

    assert launches[0] == ("synthetic-program", "arg")
    assert escalated.sandbox.enforced is False
    assert launches[1][0] == "/synthetic/bwrap"
    assert initial.sandbox.enforced is True
