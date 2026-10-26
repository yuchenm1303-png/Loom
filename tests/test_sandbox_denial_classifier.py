from __future__ import annotations

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


def _mxc_result(*, returncode, stdout="", stderr="", argv=None, enforced=True, backend="windows-mxc"):
    return ToolResult(
        ok=False,
        content=f"process=proc-1 status=exited backend=pipe\nsandbox={backend}:workspace\nexit={returncode}",
        data={
            "argv": list(argv or [r"C:\Program Files\Git\cmd\git.EXE", "status"]),
            "returncode": returncode,
            "stdout": stdout,
            "stderr": stderr,
            "sandbox": {"enforced": enforced, "backend": backend},
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
