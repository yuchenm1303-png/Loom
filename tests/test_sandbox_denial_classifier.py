from __future__ import annotations

import pytest

from app.agent_runtime.sandbox_denial import (
    classify_exec_sandbox_denial,
    is_likely_sandbox_denied_result,
)
from app.agent_runtime.tools import ToolResult


def _result(*, enforced=True, returncode=1, stdout="", stderr="") -> ToolResult:
    return ToolResult(
        ok=returncode == 0,
        content=(
            f"process=proc-1 status=exited backend=pipe\n"
            f"sandbox=bubblewrap:workspace\n"
            f"exit={returncode}\nstdout:\n{stdout}\nstderr:\n{stderr}"
        ),
        data={
            "returncode": returncode,
            "stdout": stdout,
            "stderr": stderr,
            "sandbox": {"enforced": enforced},
        },
    )


def test_sandbox_keyword_under_enforced_sandbox_is_promoted_to_typed_denial():
    raw = _result(stderr="Operation not permitted")

    assert is_likely_sandbox_denied_result(raw) is True
    typed = classify_exec_sandbox_denial(raw)
    assert typed.data["failure_kind"] == "sandbox"
    assert typed.data["sandbox_failure"] == "denied"
    assert typed.data["sandbox_detection"] == "codex_heuristic"


def test_plain_nonzero_exit_is_not_sandbox_denial_even_when_harness_content_names_sandbox():
    raw = _result(stderr="application-level validation failed")
    assert "sandbox=bubblewrap" in raw.content
    assert is_likely_sandbox_denied_result(raw) is False
    assert classify_exec_sandbox_denial(raw) is raw


def test_permission_text_without_enforced_sandbox_is_not_sandbox_denial():
    raw = _result(enforced=False, stderr="Permission denied")
    assert is_likely_sandbox_denied_result(raw) is False


def test_success_is_never_sandbox_denial_even_if_output_mentions_sandbox():
    raw = _result(returncode=0, stdout="sandbox setup complete")
    assert is_likely_sandbox_denied_result(raw) is False


def test_preclassified_platform_denial_is_preserved():
    raw = ToolResult(
        ok=False,
        content="platform denied",
        data={
            "failure_kind": "sandbox",
            "sandbox_failure": "denied",
            "sandbox_detection": "platform",
        },
    )
    assert classify_exec_sandbox_denial(raw) is raw


def _mxc_result(*, returncode, stdout="", stderr="", argv=None, enforced=True, backend="windows-mxc", cwd=""):
    return ToolResult(
        ok=False,
        content=f"process=proc-1 status=exited backend=pipe\nsandbox={backend}:workspace\nexit={returncode}",
        data={
            "argv": list(argv or [r"C:\Program Files\Git\cmd\git.EXE", "status"]),
            "returncode": returncode,
            "stdout": stdout,
            "stderr": stderr,
            "sandbox": {"enforced": enforced, "backend": backend},
            "cwd": cwd,
        },
    )


def _no_prep(_denial):
    return ""


def test_windows_dll_init_crash_with_no_output_is_a_typed_startup_denial():
    typed = classify_exec_sandbox_denial(_mxc_result(returncode=3221225794), prep_command=_no_prep)

    assert typed.data["failure_kind"] == "sandbox"
    assert typed.data["sandbox_failure"] == "denied"
    assert typed.data["sandbox_detection"] == "windows_mxc"
    denial = typed.data["sandbox_denial"]
    assert denial["kind"] == "dll_init"
    assert denial["startup"] is True
    assert denial["program"] == "git.EXE"
    # The model reads a reason, not only "exit=3221225794".
    assert typed.content.startswith("Sandbox denied: git.EXE could not start inside the Windows sandbox")
    assert "0xC0000142" in typed.content


def test_mxc_backend_error_json_for_win32k_is_a_startup_denial():
    message = (
        "PowerShell exited with STATUS_DLL_INIT_FAILED (0xC0000142). This typically means the sandbox is "
        "blocking Win32k system calls (UI subsystem access)."
    )
    stderr = message + '{"error":{"code":"backend_error","message":"' + message + '"}}\n'
    typed = classify_exec_sandbox_denial(
        _mxc_result(returncode=3221225794, stderr=stderr, argv=["powershell.exe", "-Command", "1+1"]),
        prep_command=_no_prep,
    )
    assert typed.data["sandbox_denial"]["kind"] == "win32k"
    assert typed.data["sandbox_denial"]["startup"] is True


def test_mxc_write_dac_refusal_names_the_folder_and_the_one_time_fix():
    stderr = (
        "error: BaseContainer is unavailable; DACL fallback requires write-DAC permission on "
        "'C:\\Program Files\\nodejs', which the current user lacks (ERROR_ACCESS_DENIED (WRITE_DAC not granted)).\n"
        "SECTION: Request simplified\n"
    )
    seen = []

    def prep(denial):
        seen.append(denial.path)
        return r'powershell -File "C:\Loom\windows-sandbox-prep.ps1"'

    typed = classify_exec_sandbox_denial(
        _mxc_result(returncode=1, stderr=stderr, argv=[r"C:\Program Files\nodejs\node.EXE", "--version"]),
        prep_command=prep,
    )
    denial = typed.data["sandbox_denial"]
    assert denial["kind"] == "dacl"
    assert denial["path"] == r"C:\Program Files\nodejs"
    assert denial["startup"] is True
    assert seen == [r"C:\Program Files\nodejs"]
    assert "windows-sandbox-prep.ps1" in denial["remediation"]
    assert "One-time fix (administrator)" in typed.content


def test_mxc_backend_error_for_a_missing_program_is_not_a_sandbox_denial():
    stderr = (
        'Process error: CreateProcessW failed: (0x80070002){"error":{"code":"backend_error",'
        '"message":"Process error: CreateProcessW failed: The system cannot find the file specified. (0x80070002)"}}\n'
    )
    raw = _mxc_result(returncode=4294967295, stderr=stderr, argv=[r"C:\nope\missing.exe"])
    assert classify_exec_sandbox_denial(raw, prep_command=_no_prep) is raw


def test_git_null_device_and_node_root_lstat_are_host_prep_denials():
    git = classify_exec_sandbox_denial(
        _mxc_result(returncode=128, stderr="fatal: could not open '/dev/null' for reading and writing: Permission denied\n"),
        prep_command=_no_prep,
    )
    assert git.data["sandbox_denial"]["kind"] == "null_device"
    assert git.data["sandbox_denial"]["startup"] is True

    node = classify_exec_sandbox_denial(
        _mxc_result(
            returncode=1,
            stderr="Error: EPERM: operation not permitted, lstat 'C:\\'\n    at Object.realpathSync (node:fs:2734:25)\n",
            argv=["node.exe", "npm-cli.js"],
        ),
        prep_command=_no_prep,
    )
    assert node.data["sandbox_denial"]["kind"] == "system_drive"


def test_localized_access_denied_is_a_denial_but_not_a_startup_one():
    typed = classify_exec_sandbox_denial(
        _mxc_result(returncode=1, stderr="拒绝访问。\n", argv=["cmd.exe", "/c", "dir"]),
        prep_command=_no_prep,
    )
    assert typed.data["sandbox_denial"]["kind"] == "access"
    assert typed.data["sandbox_denial"]["startup"] is False


def test_windows_rules_need_an_enforced_mxc_sandbox():
    unenforced = _mxc_result(returncode=3221225794, enforced=False)
    assert classify_exec_sandbox_denial(unenforced, prep_command=_no_prep) is unenforced

    # Bubblewrap results keep the Codex keyword heuristic and its detection label.
    linux = classify_exec_sandbox_denial(
        _mxc_result(returncode=1, stderr="Permission denied", backend="bubblewrap"),
        prep_command=_no_prep,
    )
    assert linux.data["sandbox_detection"] == "codex_heuristic"
    assert "sandbox_denial" not in linux.data


# Captured from git 2.x and Node 20 running in an AppContainer that was only given the workspace.
GIT_CWD_STDERR = "fatal: Unable to read current working directory: Permission denied\n"
NODE_ANCESTOR_STDERR = (
    "node:fs:2734\n"
    "    const out = binding.lstat(base, false, undefined, true /* throwIfNoEntry */);\n"
    "                        ^\n"
    "\n"
    "Error: EPERM: operation not permitted, lstat 'C:\\tmp'\n"
    "    at Object.realpathSync (node:fs:2734:25)\n"
    "    at toRealPath (node:internal/modules/helpers:63:13)\n"
    "    at Module._findPath (node:internal/modules/cjs/loader:781:22)\n"
)
WORKSPACE = r"C:\tmp\ws"


def test_git_that_cannot_read_its_working_directory_names_the_folders_above_the_workspace():
    asked = []

    def prep(denial):
        asked.append((denial.kind, denial.path))
        return r'powershell -File "C:\Loom\windows-sandbox-prep.ps1" -Workspace "C:\tmp\ws"'

    typed = classify_exec_sandbox_denial(
        _mxc_result(returncode=128, stderr=GIT_CWD_STDERR, cwd=WORKSPACE + r"\src"),
        prep_command=prep,
        workspace=WORKSPACE,
    )

    denial = typed.data["sandbox_denial"]
    assert denial["kind"] == "cwd"
    assert denial["startup"] is True
    # The closed folders sit above the workspace, not above the command's deeper cwd.
    assert denial["path"] == WORKSPACE
    assert asked == [("cwd", WORKSPACE)]
    assert "-Workspace" in denial["remediation"]
    assert "One-time fix (administrator)" in typed.content


def test_workspace_denials_fall_back_to_the_command_cwd_when_the_workspace_is_unknown():
    typed = classify_exec_sandbox_denial(
        _mxc_result(returncode=128, stderr=GIT_CWD_STDERR, cwd=WORKSPACE),
        prep_command=_no_prep,
    )
    assert typed.data["sandbox_denial"]["kind"] == "cwd"
    assert typed.data["sandbox_denial"]["path"] == WORKSPACE


def test_node_that_cannot_stat_a_folder_above_the_workspace_never_ran_the_script():
    typed = classify_exec_sandbox_denial(
        _mxc_result(returncode=1, stderr=NODE_ANCESTOR_STDERR, argv=["node.exe", "build.js"], cwd=WORKSPACE),
        prep_command=_no_prep,
        workspace=WORKSPACE,
    )

    denial = typed.data["sandbox_denial"]
    assert denial["kind"] == "ancestor"
    # Node resolves a script's real path before it runs any of it, so a retry repeats nothing.
    assert denial["startup"] is True
    assert denial["path"] == WORKSPACE
    assert r"C:\tmp, a folder above its workspace" in typed.content


def test_a_script_that_lists_a_folder_above_the_workspace_has_already_started():
    stderr = (
        "Error: EPERM: operation not permitted, scandir 'C:\\Users'\n"
        "    at Object.readdirSync (node:fs:1507:3)\n"
        "    at scan (C:\\Users\\me\\ws\\scan.js:3:6)\n"
    )
    typed = classify_exec_sandbox_denial(
        _mxc_result(returncode=1, stderr=stderr, argv=["node.exe", "scan.js"], cwd=r"C:\Users\me\ws"),
        # With a fix command to print, the denial is rebuilt: it must keep its own verdict.
        prep_command=lambda _denial: r'powershell -File "C:\Loom\windows-sandbox-prep.ps1" -Workspace "C:\Users\me\ws"',
        workspace=r"C:\Users\me\ws",
    )

    denial = typed.data["sandbox_denial"]
    assert denial["kind"] == "ancestor"
    assert "-Workspace" in denial["remediation"]
    # Output may exist and side effects may have happened: this is not a startup failure.
    assert denial["startup"] is False


@pytest.mark.parametrize("stat_path", [r"C:\tmp", r"c:\TMP", "C:\\\\tmp"])
def test_folders_above_the_workspace_match_in_any_spelling(stat_path):
    stderr = f"Error: EPERM: operation not permitted, lstat '{stat_path}'\n"
    typed = classify_exec_sandbox_denial(
        _mxc_result(returncode=1, stderr=stderr, argv=["node.exe", "x.js"], cwd=WORKSPACE),
        prep_command=_no_prep,
        workspace=WORKSPACE,
    )
    assert typed.data["sandbox_denial"]["kind"] == "ancestor"


@pytest.mark.parametrize(
    "stat_path",
    [
        WORKSPACE,
        WORKSPACE + "2",
        WORKSPACE + r"\src",
        # Text that begins the workspace path without ending on a folder boundary.
        r"C:\tm",
        WORKSPACE[:-1],
        r"C:\other",
        r"D:\tmp",
    ],
)
def test_only_folders_above_the_workspace_count_as_ancestors(stat_path):
    stderr = f"Error: EPERM: operation not permitted, lstat '{stat_path}'\n"
    typed = classify_exec_sandbox_denial(
        _mxc_result(returncode=1, stderr=stderr, argv=["node.exe", "x.js"], cwd=WORKSPACE),
        prep_command=_no_prep,
        workspace=WORKSPACE,
    )
    # The workspace itself, a sibling that shares its prefix, something below it, a partial
    # folder name and other folders are ordinary access denials: the one-time script cannot fix
    # them.
    assert typed.data["sandbox_denial"]["kind"] == "access"


def test_default_remediation_asks_for_the_workspace_only_while_its_parents_are_closed(monkeypatch):
    from app.agent_runtime import windows_sandbox_prep as prep_module

    state = {"missing": ("C:\\", r"C:\tmp")}
    calls = []

    def fake_status(**kwargs):
        calls.append(kwargs)
        return prep_module.WindowsSandboxPrepStatus(
            True,
            True,
            script=r"C:\Loom\windows-sandbox-prep.ps1",
            workspace=str(kwargs.get("workspace") or ""),
            missing_ancestors=state["missing"],
        )

    monkeypatch.setattr(prep_module, "windows_sandbox_prep_status", fake_status)
    raw = _mxc_result(returncode=128, stderr=GIT_CWD_STDERR, cwd=WORKSPACE)

    closed = classify_exec_sandbox_denial(raw, workspace=WORKSPACE)
    assert closed.data["sandbox_denial"]["remediation"].endswith(r'-Workspace "C:\tmp\ws"')
    assert calls[0]["workspace"] == WORKSPACE
    assert not calls[0]["tool_dirs"]

    state["missing"] = ()
    opened = classify_exec_sandbox_denial(raw, workspace=WORKSPACE)
    # Something else stopped git; running the script would change nothing.
    assert opened.data["sandbox_denial"]["remediation"] == ""
    assert "One-time fix" not in opened.content
