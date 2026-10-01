from __future__ import annotations

import os
import socket
import sys
from pathlib import Path

import pytest

from app.agent_runtime import PermissionMode, ProcessStore, SandboxBackend, SandboxManager, SandboxPolicy


pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows MXC integration requires Windows")


def _required_mxc_manager() -> SandboxManager:
    executable = str(os.environ.get("LOOM_WINDOWS_SANDBOX_EXECUTABLE") or "").strip()
    if not executable:
        pytest.skip("LOOM_WINDOWS_SANDBOX_EXECUTABLE is not configured")
    return SandboxManager(
        policy=SandboxPolicy.REQUIRED,
        windows_mxc_executable=executable,
        system_name="Windows",
    )


def test_windows_mxc_enforces_workspace_boundary_metadata_and_sanitized_environment(
    tmp_path, monkeypatch
):
    manager = _required_mxc_manager()
    workspace = (tmp_path / "workspace").resolve()
    workspace.mkdir()
    metadata = (workspace / ".git").resolve()
    metadata.mkdir()
    outside = (tmp_path / "outside.txt").resolve()
    inside = (workspace / "inside.txt").resolve()
    metadata_target = (metadata / "must-stay-readonly.txt").resolve()
    monkeypatch.setenv("LOOM_SANDBOX_TEST_API_KEY", "synthetic-secret")

    sandbox = manager.snapshot(
        workspace=workspace,
        permission_mode=PermissionMode.WORKSPACE,
    )
    assert sandbox.enforced is True
    assert sandbox.backend is SandboxBackend.WINDOWS_MXC
    assert sandbox.network_isolated is True

    script = (
        "import os; from pathlib import Path; "
        f"inside=Path({str(inside)!r}); outside=Path({str(outside)!r}); metadata=Path({str(metadata_target)!r}); "
        "inside.write_text('inside-ok', encoding='utf-8'); "
        "print('INSIDE_WRITTEN'); "
        "print('VISIBLE=' + os.environ.get('LOOM_EXEC_VISIBLE','missing')); "
        "print('SECRET_PRESENT=' + str('LOOM_SANDBOX_TEST_API_KEY' in os.environ)); "
        "\ntry:\n outside.write_text('escape', encoding='utf-8')\n"
        "except OSError as exc:\n print('OUTSIDE_BLOCKED=' + type(exc).__name__)\n"
        "else:\n print('OUTSIDE_WRITTEN')\n"
        "try:\n metadata.write_text('mutated', encoding='utf-8')\n"
        "except OSError as exc:\n print('METADATA_BLOCKED=' + type(exc).__name__)\n"
        "else:\n print('METADATA_WRITTEN')\n"
    )
    store = ProcessStore(sandbox_manager=manager)
    result = store.run(
        session_id="mxc-workspace",
        argv=(sys.executable, "-I", "-S", "-c", script),
        cwd=workspace,
        workspace=workspace,
        permission_mode=PermissionMode.WORKSPACE.value,
        timeout_seconds=30,
        env={"LOOM_EXEC_VISIBLE": "yes"},
    )

    assert result.returncode == 0, result.stderr or result.stdout
    assert result.sandbox.enforced is True
    assert "INSIDE_WRITTEN" in result.stdout
    assert "VISIBLE=yes" in result.stdout
    assert "SECRET_PRESENT=False" in result.stdout
    assert "OUTSIDE_BLOCKED=" in result.stdout
    assert "OUTSIDE_WRITTEN" not in result.stdout
    assert "METADATA_BLOCKED=" in result.stdout
    assert "METADATA_WRITTEN" not in result.stdout
    assert inside.read_text(encoding="utf-8") == "inside-ok"
    assert not outside.exists()
    assert not metadata_target.exists()


def test_windows_mxc_read_only_blocks_workspace_writes(tmp_path):
    manager = _required_mxc_manager()
    workspace = (tmp_path / "workspace").resolve()
    workspace.mkdir()
    target = (workspace / "should-not-exist.txt").resolve()

    script = (
        "from pathlib import Path; target=Path(" + repr(str(target)) + "); "
        "\ntry:\n target.write_text('nope', encoding='utf-8')\n"
        "except OSError as exc:\n print('READONLY_BLOCKED=' + type(exc).__name__)\n"
        "else:\n print('READONLY_WRITTEN')\n"
    )
    store = ProcessStore(sandbox_manager=manager)
    result = store.run(
        session_id="mxc-readonly",
        argv=(sys.executable, "-I", "-S", "-c", script),
        cwd=workspace,
        workspace=workspace,
        permission_mode=PermissionMode.READ_ONLY.value,
        timeout_seconds=30,
    )

    assert result.returncode == 0, result.stderr or result.stdout
    assert result.sandbox.enforced is True
    assert "READONLY_BLOCKED=" in result.stdout
    assert "READONLY_WRITTEN" not in result.stdout
    assert not target.exists()


def test_windows_mxc_blocks_outbound_network(tmp_path):
    manager = _required_mxc_manager()
    workspace = (tmp_path / "workspace").resolve()
    workspace.mkdir()

    script = (
        "import socket; "
        "\ntry:\n s=socket.create_connection(('1.1.1.1',443), timeout=2); s.close()\n"
        "except OSError as exc:\n print('NETWORK_BLOCKED=' + type(exc).__name__)\n"
        "else:\n print('NETWORK_CONNECTED')\n"
    )
    store = ProcessStore(sandbox_manager=manager)
    result = store.run(
        session_id="mxc-network",
        argv=(sys.executable, "-I", "-S", "-c", script),
        cwd=workspace,
        workspace=workspace,
        permission_mode=PermissionMode.WORKSPACE.value,
        timeout_seconds=30,
    )

    assert result.returncode == 0, result.stderr or result.stdout
    assert result.sandbox.network_isolated is True
    assert "NETWORK_BLOCKED=" in result.stdout
    assert "NETWORK_CONNECTED" not in result.stdout


def test_windows_mxc_blocks_host_loopback_network(tmp_path):
    manager = _required_mxc_manager()
    workspace = (tmp_path / "workspace").resolve()
    workspace.mkdir()

    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]
    try:
        script = (
            "import socket; "
            f"port={port}; "
            "\ntry:\n s=socket.create_connection(('127.0.0.1',port), timeout=2); s.close()\n"
            "except OSError as exc:\n print('LOOPBACK_BLOCKED=' + type(exc).__name__)\n"
            "else:\n print('LOOPBACK_CONNECTED')\n"
        )
        store = ProcessStore(sandbox_manager=manager)
        result = store.run(
            session_id="mxc-loopback",
            argv=(sys.executable, "-I", "-S", "-c", script),
            cwd=workspace,
            workspace=workspace,
            permission_mode=PermissionMode.WORKSPACE.value,
            timeout_seconds=30,
        )
    finally:
        listener.close()

    assert result.returncode == 0, result.stderr or result.stdout
    assert result.sandbox.network_isolated is True
    assert "LOOPBACK_BLOCKED=" in result.stdout
    assert "LOOPBACK_CONNECTED" not in result.stdout
