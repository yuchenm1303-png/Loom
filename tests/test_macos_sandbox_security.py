"""macOS isolation gate: never mistake Loom's approval policy for an OS sandbox."""
from __future__ import annotations

from pathlib import Path

import pytest

from app.agent_runtime.contracts import PermissionMode
from app.agent_runtime.sandbox import SandboxBackend, SandboxManager, SandboxMode, SandboxPolicy


@pytest.mark.parametrize("mode", [PermissionMode.READ_ONLY, PermissionMode.WORKSPACE])
def test_macos_auto_reports_unsandboxed_without_claiming_enforcement(tmp_path: Path, mode):
    manager = SandboxManager(system_name="Darwin", policy=SandboxPolicy.AUTO)
    snapshot = manager.snapshot(workspace=tmp_path, permission_mode=mode)
    assert snapshot.mode is not SandboxMode.DISABLED
    assert snapshot.backend is SandboxBackend.NONE
    assert not snapshot.enforced
    assert not snapshot.available
    assert "No Loom OS sandbox backend" in snapshot.reason


@pytest.mark.parametrize("mode", [PermissionMode.READ_ONLY, PermissionMode.WORKSPACE])
def test_macos_required_sandbox_refuses_to_run_without_real_isolation(tmp_path: Path, mode):
    manager = SandboxManager(system_name="Darwin", policy=SandboxPolicy.REQUIRED)
    with pytest.raises(RuntimeError, match="OS sandbox is required but unavailable"):
        manager.prepare(
            argv=("/usr/bin/true",), cwd=tmp_path, workspace=tmp_path,
            permission_mode=mode,
        )
