from __future__ import annotations

import base64
import json

import pytest

from app.agent_runtime.contracts import PermissionMode
from app.agent_runtime.permissions import AdditionalPermissionProfile
from app.agent_runtime.sandbox import SandboxBackend, SandboxManager, SandboxPolicy
from app.agent_runtime.sandbox_additional_permissions import prepare_with_additional_permissions
from app.agent_runtime.sandbox_failure import SandboxExecutionError, SandboxFailureKind


def _linux_manager(policy=SandboxPolicy.AUTO) -> SandboxManager:
    return SandboxManager(
        policy=policy,
        bubblewrap_executable="/synthetic/bwrap",
        probe_backend=False,
        system_name="Linux",
    )


def _windows_manager(policy=SandboxPolicy.AUTO) -> SandboxManager:
    return SandboxManager(
        policy=policy,
        windows_mxc_executable=r"C:\synthetic\wxc-exec.exe",
        probe_backend=False,
        system_name="Windows",
    )


def _decode_mxc(argv):
    assert argv[1] == "--config-base64"
    return json.loads(base64.b64decode(argv[2]).decode("utf-8"))


def test_linux_additional_write_grant_stays_inside_bubblewrap(tmp_path):
    workspace = tmp_path / "project"
    workspace.mkdir()
    protected = workspace / ".git"
    protected.mkdir()
    extra = tmp_path / "extra-write"
    extra.mkdir()

    prepared = prepare_with_additional_permissions(
        _linux_manager(),
        argv=("python", "-V"),
        cwd=workspace,
        workspace=workspace,
        permission_mode=PermissionMode.WORKSPACE,
        profile=AdditionalPermissionProfile(file_system_write=(str(extra),)),
    )

    assert prepared.snapshot.enforced is True
    assert prepared.snapshot.backend is SandboxBackend.BUBBLEWRAP
    assert prepared.argv[0] == "/synthetic/bwrap"
    pairs = list(zip(prepared.argv, prepared.argv[1:]))
    assert ("--bind", str(extra.resolve())) in pairs

    protected_positions = [
        index
        for index, value in enumerate(prepared.argv)
        if value == str(protected.resolve())
        and index > 0
        and prepared.argv[index - 1] == "--ro-bind"
    ]
    write_position = max(
        index
        for index, value in enumerate(prepared.argv)
        if value == str(extra.resolve())
        and index > 0
        and prepared.argv[index - 1] == "--bind"
    )
    assert protected_positions
    assert max(protected_positions) > write_position


def test_required_policy_allows_scoped_grant_without_full_bypass(tmp_path):
    workspace = tmp_path / "project"
    workspace.mkdir()
    extra = tmp_path / "extra-write"
    extra.mkdir()

    prepared = prepare_with_additional_permissions(
        _linux_manager(SandboxPolicy.REQUIRED),
        argv=("python", "-V"),
        cwd=workspace,
        workspace=workspace,
        permission_mode=PermissionMode.WORKSPACE,
        profile=AdditionalPermissionProfile(file_system_write=(str(extra),)),
    )

    assert prepared.snapshot.enforced is True
    assert prepared.snapshot.policy is SandboxPolicy.REQUIRED
    assert prepared.snapshot.backend is SandboxBackend.BUBBLEWRAP


def test_windows_scoped_grants_extend_paths_and_network_without_bypassing_mxc(tmp_path):
    workspace = (tmp_path / "project").resolve()
    workspace.mkdir()
    protected = workspace / ".git"
    protected.mkdir()
    readable = (tmp_path / "readable").resolve()
    writable = (tmp_path / "writable").resolve()
    readable.mkdir()
    writable.mkdir()

    prepared = prepare_with_additional_permissions(
        _windows_manager(),
        argv=("python", "-V"),
        cwd=workspace,
        workspace=workspace,
        permission_mode=PermissionMode.WORKSPACE,
        environment={"PATH": r"C:\Python312"},
        profile=AdditionalPermissionProfile(
            network_enabled=True,
            file_system_read=(str(readable),),
            file_system_write=(str(writable),),
        ),
    )

    assert prepared.snapshot.enforced is True
    assert prepared.snapshot.backend is SandboxBackend.WINDOWS_MXC
    assert prepared.snapshot.network_isolated is False
    config = _decode_mxc(prepared.argv)
    filesystem = config["filesystem"]
    assert str(writable) in filesystem["readwritePaths"]
    assert str(readable) in filesystem["readonlyPaths"]
    assert str(protected.resolve()) in filesystem["readonlyPaths"]
    assert str(protected.resolve()) not in filesystem["readwritePaths"]
    assert config["network"]["egress"]["default"] == "allow"
    assert config["network"]["ingress"] == {
        "default": "deny",
        "hostLoopback": "deny",
    }


def test_missing_additional_write_path_fails_closed(tmp_path):
    workspace = tmp_path / "project"
    workspace.mkdir()
    missing = tmp_path / "does-not-exist"

    with pytest.raises(SandboxExecutionError) as raised:
        prepare_with_additional_permissions(
            _linux_manager(),
            argv=("python", "-V"),
            cwd=workspace,
            workspace=workspace,
            permission_mode=PermissionMode.WORKSPACE,
            profile=AdditionalPermissionProfile(file_system_write=(str(missing),)),
        )

    assert raised.value.kind is SandboxFailureKind.CONFIGURATION
    assert raised.value.escalatable is False
