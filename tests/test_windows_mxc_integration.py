from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

import pytest

from app.agent_runtime import PermissionMode, ProcessStore, SandboxBackend, SandboxManager, SandboxPolicy


pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows MXC integration requires Windows")


def _required_mxc_manager() -> SandboxManager:
    executable = str(os.environ.get("LOOM_WINDOWS_SANDBOX_EXECUTABLE") or "").strip()
    if not executable:
        pytest.skip("LOOM_WINDOWS_SANDBOX_EXECUTABLE is not configured")
    manager = SandboxManager(
        policy=SandboxPolicy.REQUIRED,
        windows_mxc_executable=executable,
        system_name="Windows",
    )
    return manager


def test_windows_mxc_enforces_workspace_boundary_and_sanitized_environment(tmp_path, monkeypatch):
    manager = _required_mxc_manager()
    workspace = (tmp_path / "workspace").resolve()
    workspace.mkdir()
    outside = (tmp_path / "outside.txt").resolve()
    inside = (workspace / "inside.txt").resolve()
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
        f"inside=Path({str(inside)!r}); outside=Path({str(outside)!r}); "
        "inside.write_text('inside-ok', encoding='utf-8'); "
        "print('INSIDE_WRITTEN'); "
        "print('VISIBLE=' + os.environ.get('LOOM_EXEC_VISIBLE','missing')); "
        "print('SECRET_PRESENT=' + str('LOOM_SANDBOX_TEST_API_KEY' in os.environ)); "
        "\ntry:\n outside.write_text('escape', encoding='utf-8')\n"
        "except OSError as exc:\n print('OUTSIDE_BLOCKED=' + type(exc).__name__)\n"
        "else:\n print('OUTSIDE_WRITTEN')\n"
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
    assert inside.read_text(encoding="utf-8") == "inside-ok"
    assert not outside.exists()


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


def _run_boxed(manager: SandboxManager, workspace: Path, argv: tuple[str, ...], *, session: str):
    store = ProcessStore(sandbox_manager=manager)
    return store.run(
        session_id=session,
        argv=argv,
        cwd=workspace,
        workspace=workspace,
        permission_mode=PermissionMode.WORKSPACE.value,
        timeout_seconds=60,
    )


def _sandbox_has_a_desktop(manager: SandboxManager, workspace: Path) -> bool:
    """Whether user32 can initialize inside the sandbox on this host.

    It needs Win32k to be reachable (our config) and a window station and desktop
    that grant ALL APPLICATION PACKAGES access (the host). A service or server
    session may not have the latter, which no sandbox setting can change.
    """

    script = "import ctypes; print('DESKTOP', bool(ctypes.windll.user32.GetDesktopWindow()))"
    result = _run_boxed(manager, workspace, (sys.executable, "-I", "-S", "-c", script), session="mxc-desktop")
    return "DESKTOP True" in result.stdout


def test_windows_mxc_starts_powershell_now_that_win32k_is_reachable(tmp_path):
    manager = _required_mxc_manager()
    workspace = (tmp_path / "workspace").resolve()
    workspace.mkdir()
    powershell = shutil.which("powershell.exe")
    if not powershell:
        pytest.skip("Windows PowerShell is not installed")
    if not _sandbox_has_a_desktop(manager, workspace):
        pytest.skip("this host gives the sandbox no desktop for user32 to initialize on")

    result = _run_boxed(
        manager,
        workspace,
        (powershell, "-NoProfile", "-NonInteractive", "-Command", "Write-Output (6*7)"),
        session="mxc-powershell",
    )

    assert result.returncode == 0, result.stderr or result.stdout
    assert result.sandbox.enforced is True
    assert "42" in result.stdout


def test_windows_mxc_still_denies_reading_the_user_profile(tmp_path):
    manager = _required_mxc_manager()
    workspace = (tmp_path / "workspace").resolve()
    workspace.mkdir()
    secret = Path.home() / f".loom-sandbox-probe-{uuid.uuid4().hex}.txt"
    secret.write_text("profile-secret", encoding="utf-8")
    try:
        script = (
            "from pathlib import Path\n"
            f"target = Path({str(secret)!r})\n"
            "try:\n target.read_text(encoding='utf-8')\n"
            "except OSError as exc:\n print('PROFILE_BLOCKED=' + type(exc).__name__)\n"
            "else:\n print('PROFILE_READ')\n"
        )
        result = _run_boxed(manager, workspace, (sys.executable, "-I", "-S", "-c", script), session="mxc-profile")
        assert result.returncode == 0, result.stderr or result.stdout
        assert "PROFILE_BLOCKED=" in result.stdout
        assert "PROFILE_READ" not in result.stdout
    finally:
        secret.unlink(missing_ok=True)


def _prepared_host(manager: SandboxManager, workspace: Path | None = None):
    status = manager.windows_prep_status(force=True, workspace=workspace)
    if manager.windows_mxc_tier == "appcontainer-dacl":
        if not status.complete:
            pytest.skip("Windows sandbox host preparation has not run: " + "; ".join(status.missing()))
        if not status.workspace_ready:
            pytest.skip(
                "the folders above the test workspace are closed to the sandbox ("
                + "; ".join(status.missing())
                + "). Set LOOM_SANDBOX_TEST_ROOT to a folder whose parents the prep script opened "
                "(run it with -Workspace <folder>\\x), or run: "
                + (status.command() or "scripts/windows-sandbox-prep.ps1")
            )
    return status


@pytest.fixture()
def developer_workspace(tmp_path):
    """A fresh workspace; LOOM_SANDBOX_TEST_ROOT picks a parent the sandbox may list."""

    root = str(os.environ.get("LOOM_SANDBOX_TEST_ROOT") or "").strip()
    if not root:
        workspace = (tmp_path / "workspace").resolve()
        workspace.mkdir()
        yield workspace
        return
    workspace = Path(tempfile.mkdtemp(prefix="loom-sbx-", dir=root)).resolve()
    try:
        yield workspace
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


@pytest.mark.parametrize(
    ("tool", "arguments", "expected"),
    [
        ("git", ("--version",), "git version"),
        ("node", ("-e", "process.stdout.write(String(require('fs').realpathSync('.').length > 0))"), "true"),
        ("npm", ("--version",), "."),
    ],
)
def test_windows_mxc_runs_developer_tools_after_host_preparation(developer_workspace, tool, arguments, expected):
    manager = _required_mxc_manager()
    executable = shutil.which(tool)
    if not executable:
        pytest.skip(f"{tool} is not installed")
    workspace = developer_workspace
    _prepared_host(manager, workspace)
    if tool == "git":
        # git status also opens NUL and reads the repository it runs in.
        init = subprocess.run([executable, "init", "-q", str(workspace)], capture_output=True, text=True)
        assert init.returncode == 0, init.stderr
        arguments = ("status", "--short", "--branch")
        expected = "##"

    result = _run_boxed(manager, workspace, (executable, *arguments), session=f"mxc-{tool}")

    assert result.returncode == 0, (result.stderr or result.stdout)[-2000:]
    assert result.sandbox.enforced is True
    assert expected in result.stdout


def test_windows_mxc_lists_the_folders_above_a_workspace_but_reads_and_writes_nothing_in_them(developer_workspace):
    manager = _required_mxc_manager()
    workspace = developer_workspace
    _prepared_host(manager, workspace)
    parent = workspace.parent
    name = f".loom-ancestor-probe-{uuid.uuid4().hex}.txt"
    probe = parent / name
    probe.write_text("parent-secret", encoding="utf-8")
    try:
        script = (
            "import os\n"
            f"parent = {str(parent)!r}\n"
            f"name = {name!r}\n"
            "print('LISTED=' + str(name in os.listdir(parent)))\n"
            "try:\n open(os.path.join(parent, name), 'rb').read()\n"
            "except OSError as exc:\n print('CONTENT_BLOCKED=' + type(exc).__name__)\n"
            "else:\n print('CONTENT_READ')\n"
            "try:\n open(os.path.join(parent, name + '.new'), 'wb').write(b'x')\n"
            "except OSError as exc:\n print('PARENT_WRITE_BLOCKED=' + type(exc).__name__)\n"
            "else:\n print('PARENT_WRITTEN')\n"
        )
        result = _run_boxed(manager, workspace, (sys.executable, "-I", "-S", "-c", script), session="mxc-ancestors")
        assert result.returncode == 0, (result.stderr or result.stdout)[-2000:]
        assert "LISTED=True" in result.stdout
        assert "CONTENT_BLOCKED=" in result.stdout
        assert "CONTENT_READ" not in result.stdout
        assert "PARENT_WRITE_BLOCKED=" in result.stdout
        assert "PARENT_WRITTEN" not in result.stdout
        assert not (parent / (name + ".new")).exists()
    finally:
        probe.unlink(missing_ok=True)


def test_windows_mxc_git_can_diff_grep_and_reset_the_work_tree(developer_workspace):
    manager = _required_mxc_manager()
    git = shutil.which("git")
    if not git:
        pytest.skip("git is not installed")
    workspace = developer_workspace
    _prepared_host(manager, workspace)

    def host_git(*arguments: str) -> None:
        done = subprocess.run(
            [git, "-c", "user.name=loom", "-c", "user.email=loom@example.invalid", *arguments],
            cwd=workspace,
            capture_output=True,
            text=True,
        )
        assert done.returncode == 0, done.stderr

    host_git("init", "-q")
    (workspace / "a.txt").write_text("hello\n", encoding="utf-8")
    host_git("add", "a.txt")
    host_git("commit", "-q", "-m", "init")
    (workspace / "a.txt").write_text("hello\nmore\n", encoding="utf-8")

    # These entered the work tree through GetFinalPathNameByHandleW, which an AppContainer is
    # refused while core.symlinks is true; the sandbox's git config now says false.
    for argv, expected in (
        ((git, "diff", "--stat"), "a.txt"),
        ((git, "grep", "more"), "a.txt:"),
        ((git, "config", "--get", "core.symlinks"), "false"),
    ):
        result = _run_boxed(manager, workspace, argv, session="mxc-git-" + argv[1])
        assert result.returncode == 0, (result.stderr or result.stdout)[-2000:]
        assert expected in result.stdout

    reset = _run_boxed(manager, workspace, (git, "reset", "--hard", "HEAD"), session="mxc-git-reset")
    assert reset.returncode == 0, (reset.stderr or reset.stdout)[-2000:]
    assert (workspace / "a.txt").read_text(encoding="utf-8").replace("\r\n", "\n") == "hello\n"
