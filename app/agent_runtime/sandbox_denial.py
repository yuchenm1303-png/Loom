from __future__ import annotations

import json
import ntpath
import os
import re
from dataclasses import dataclass
from typing import Callable

from .sandbox_failure import SandboxFailureKind
from .tools import ToolResult


_SANDBOX_DENIED_KEYWORDS = (
    "operation not permitted",
    "permission denied",
    "read-only file system",
    "seccomp",
    "sandbox",
    "landlock",
    "failed to write file",
)

# NTSTATUS exit codes of a Windows process that died while starting.
_STATUS_DLL_INIT_FAILED = 0xC0000142
_STATUS_ACCESS_DENIED = 0xC0000022
# Localized "Access is denied" (en, zh-CN, zh-TW) plus the Node/libuv codes.
_WINDOWS_ACCESS_MARKERS = (
    "access is denied",
    "access denied",
    "拒绝访问",
    "存取被拒",
    "eperm",
    "eacces",
)
_MXC_ERROR_RE = re.compile(r'\{"error":\s*\{"code":\s*"(?P<code>[^"]+)".*\}\}')
_MXC_DACL_RE = re.compile(r"DACL fallback requires write-DAC permission on '(?P<path>[^']+)'")
_ROOT_LSTAT_RE = re.compile(r"(?:EPERM|EACCES)[^\n]*lstat '[A-Za-z]:\\{1,2}'")
_NULL_DEVICE_RE = re.compile(r"could not open '/dev/null'|\\\\\.\\nul", re.IGNORECASE)
# git resolves its working directory with GetLongPathNameW, which lists every folder
# above it; the sandbox only gets the workspace itself.
_CWD_RE = re.compile(r"current working directory|当前工作目录", re.IGNORECASE)
# node lstat()s each folder above a script to find its real path.
_ANCESTOR_STAT_RE = re.compile(
    r"(?:EPERM|EACCES)[^\n]*?(?:lstat|stat|scandir) '(?P<path>[A-Za-z]:\\{1,2}[^'\n]*)'"
)
_NODE_MODULE_LOADER_RE = re.compile(r"node:internal/modules|Module\._findPath|toRealPath")

# Startup denials: the process never got to do its work. Retrying the same
# action outside the sandbox cannot repeat a side effect it never had.
_STARTUP_KINDS = frozenset(
    {"win32k", "dll_init", "dacl", "launch", "null_device", "system_drive", "cwd", "ancestor"}
)
# Kinds the one-time host preparation fixes.
_HOST_PREP_KINDS = frozenset({"dll_init", "dacl", "null_device", "system_drive", "cwd", "ancestor"})
# Kinds that name a workspace whose parent folders the sandbox cannot list.
_WORKSPACE_KINDS = frozenset({"cwd", "ancestor"})


@dataclass(frozen=True, slots=True)
class SandboxDenial:
    kind: str
    detail: str
    program: str = ""
    path: str = ""
    remediation: str = ""
    # None: decided by the kind. A denial the kind alone cannot place sets it.
    startup_override: bool | None = None

    @property
    def startup(self) -> bool:
        if self.startup_override is not None:
            return self.startup_override
        return self.kind in _STARTUP_KINDS

    def explanation(self) -> str:
        text = f"Sandbox denied: {self.detail}"
        if self.remediation:
            text += f" One-time fix (administrator): {self.remediation}"
        return text

    def to_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "startup": self.startup,
            "detail": self.detail,
            "program": self.program,
            "path": self.path,
            "remediation": self.remediation,
        }


def is_likely_sandbox_denied_result(result: ToolResult) -> bool:
    """Port Codex's centralized sandbox-denial heuristic to Loom exec output.

    A non-zero exit or stderr alone is never enough. The result must carry an
    enforced sandbox snapshot and one of Codex's well-known denial markers in
    the command's stdout/stderr. Loom's human-readable ``content`` deliberately
    does not participate because it contains harness metadata such as
    ``sandbox=bubblewrap`` that would make every failed sandboxed command look
    like a denial.
    """

    if result.ok:
        return False
    data = result.data
    sandbox = data.get("sandbox")
    if not isinstance(sandbox, dict) or sandbox.get("enforced") is not True:
        return False
    exit_code = data.get("returncode")
    if exit_code in {None, 0}:
        return False

    sections = (
        str(data.get("stderr") or ""),
        str(data.get("stdout") or ""),
        str(data.get("aggregated_output") or ""),
    )
    return any(
        needle in section.casefold()
        for section in sections
        for needle in _SANDBOX_DENIED_KEYWORDS
    )


def _program_name(data: dict) -> str:
    argv = data.get("argv")
    if isinstance(argv, list) and argv:
        return ntpath.basename(str(argv[0]).replace("/", "\\")) or str(argv[0])
    return "the command"


def _mxc_error_message(text: str) -> tuple[str, str] | None:
    """The ``{"error": {...}}`` record wxc-exec appends when it fails itself."""

    for line in reversed(text.splitlines()):
        match = _MXC_ERROR_RE.search(line)
        if match is None:
            continue
        try:
            payload = json.loads(line[match.start():match.end()])
        except ValueError:
            continue
        error = payload.get("error") if isinstance(payload, dict) else None
        if isinstance(error, dict):
            return str(error.get("code") or ""), str(error.get("message") or "")
    return None


def _has_access_marker(text: str) -> bool:
    folded = text.casefold()
    return any(marker in folded for marker in _WINDOWS_ACCESS_MARKERS) or any(
        keyword in folded for keyword in ("permission denied", "operation not permitted")
    )


def windows_mxc_denial(data: dict, *, workspace: str | os.PathLike[str] | None = None) -> SandboxDenial | None:
    """Recognize a command the Windows MXC sandbox stopped, from its exit and output.

    Only enforced ``windows-mxc`` runs that failed qualify. wxc-exec's own
    ``backend_error`` records count only when they describe an access failure:
    "file not found" is the command's problem, not the sandbox's.

    ``workspace`` is the folder the sandbox opened. The command's own cwd may be
    deeper, but only the folders above the workspace are closed to it.
    """

    sandbox = data.get("sandbox")
    if not isinstance(sandbox, dict) or sandbox.get("enforced") is not True:
        return None
    if str(sandbox.get("backend") or "") != "windows-mxc":
        return None
    returncode = data.get("returncode")
    if returncode in {None, 0} or isinstance(returncode, bool):
        return None
    program = _program_name(data)
    text = "\n".join(
        str(data.get(name) or "") for name in ("stderr", "stdout", "aggregated_output")
    )

    mxc_error = _mxc_error_message(text)
    if mxc_error is not None:
        _, message = mxc_error
        folded = message.casefold()
        if "status_dll_init_failed" in folded or "win32k" in folded:
            return SandboxDenial(
                "win32k",
                f"{program} could not start because the sandbox blocked its Win32k (UI subsystem) "
                "initialization (STATUS_DLL_INIT_FAILED).",
                program=program,
            )
        dacl = _MXC_DACL_RE.search(message)
        if dacl is not None:
            return _dacl_denial(program, dacl.group("path"))
        if "0x80070005" in folded or _has_access_marker(message):
            return SandboxDenial(
                "launch",
                f"the Windows sandbox could not launch {program}: access denied ({message.strip()[:200]}).",
                program=program,
            )
        return None

    dacl = _MXC_DACL_RE.search(text)
    if dacl is not None:
        return _dacl_denial(program, dacl.group("path"))

    code = int(returncode) & 0xFFFFFFFF
    if code == _STATUS_DLL_INIT_FAILED:
        return SandboxDenial(
            "dll_init",
            f"{program} could not start inside the Windows sandbox: a DLL failed to initialize "
            "(STATUS_DLL_INIT_FAILED, 0xC0000142), usually because the AppContainer cannot open "
            "something it needs at startup.",
            program=program,
        )
    if code == _STATUS_ACCESS_DENIED:
        return SandboxDenial(
            "launch",
            f"the Windows sandbox could not start {program}: access denied (STATUS_ACCESS_DENIED).",
            program=program,
        )
    if _NULL_DEVICE_RE.search(text) and _has_access_marker(text):
        return SandboxDenial(
            "null_device",
            f"{program} could not open the NUL device. Windows sandboxes (AppContainers) cannot use "
            "NUL until an administrator prepares this machine; the kernel resets it at every boot.",
            program=program,
        )
    if _ROOT_LSTAT_RE.search(text):
        return SandboxDenial(
            "system_drive",
            f"{program} could not read the system drive root's metadata. Windows sandboxes "
            "(AppContainers) need a one-time host preparation for that.",
            program=program,
        )
    root = str(workspace or "").strip() or str(data.get("cwd") or "").strip()
    if root and _CWD_RE.search(text) and _has_access_marker(text):
        return SandboxDenial(
            "cwd",
            f"{program} could not read its working directory: the sandbox cannot list the folders "
            f"above {root}.",
            program=program,
            path=root,
        )
    stat_match = _ANCESTOR_STAT_RE.search(text)
    if stat_match is not None and root and _is_ancestor(stat_match.group("path"), root):
        return SandboxDenial(
            "ancestor",
            f"{program} could not look at {stat_match.group('path')}, a folder above its workspace "
            f"{root}: the sandbox is only given the workspace itself.",
            program=program,
            path=root,
            # Node resolves a script's real path before running any of it; anywhere
            # else the command may already have done part of its work.
            startup_override=bool(_NODE_MODULE_LOADER_RE.search(text)),
        )
    if _has_access_marker(text):
        return SandboxDenial(
            "access",
            f"{program} reported an access denial while running in the Windows sandbox, which "
            "blocks files outside the workspace, the user profile and the network.",
            program=program,
        )
    return None


def _is_ancestor(candidate: str, descendant: str) -> bool:
    """Whether ``candidate`` is a folder above ``descendant`` (Windows spelling, case-insensitive)."""

    above = ntpath.normcase(ntpath.normpath(candidate)).rstrip("\\")
    below = ntpath.normcase(ntpath.normpath(descendant))
    return bool(above) and below.startswith(above + "\\") and below != above


def _dacl_denial(program: str, path: str) -> SandboxDenial:
    return SandboxDenial(
        "dacl",
        f"the Windows sandbox could not open {path} to {program}: the folder's permissions are "
        "protected, so only an administrator can grant the sandbox read access.",
        program=program,
        path=path,
    )


def _default_prep_command(denial: SandboxDenial) -> str:
    from .windows_sandbox_prep import windows_sandbox_prep_status

    workspace_kind = denial.kind in _WORKSPACE_KINDS
    status = windows_sandbox_prep_status(
        sandbox_executable=os.environ.get("LOOM_WINDOWS_SANDBOX_EXECUTABLE") or None,
        tool_dirs=(denial.path,) if denial.path and not workspace_kind else (),
        workspace=denial.path if workspace_kind else None,
    )
    if workspace_kind:
        # Nothing to fix when the folders above it are already listable: the
        # failure then has another cause and a script run would not help.
        return "" if status.workspace_ready else status.command()
    if status.complete:
        return ""
    return status.command()


def classify_exec_sandbox_denial(
    result: ToolResult,
    *,
    prep_command: Callable[[SandboxDenial], str] | None = None,
    workspace: str | os.PathLike[str] | None = None,
) -> ToolResult:
    """Promote one proven/likely sandbox failure into Loom's typed result shape.

    Windows MXC failures get a structured ``sandbox_denial`` and a plain reason
    in front of the output, so neither the model nor the user is left with a
    bare exit code such as 3221225794.
    """

    if result.data.get("failure_kind") == "sandbox":
        return result
    denial = windows_mxc_denial(result.data, workspace=workspace)
    if denial is not None:
        if denial.kind in _HOST_PREP_KINDS:
            remediation = (prep_command or _default_prep_command)(denial)
            if remediation:
                denial = SandboxDenial(
                    denial.kind, denial.detail, program=denial.program,
                    path=denial.path, remediation=remediation,
                    startup_override=denial.startup_override,
                )
        data = dict(result.data)
        data.update(
            {
                "failure_kind": "sandbox",
                "sandbox_failure": SandboxFailureKind.DENIED.value,
                "sandbox_detection": "windows_mxc",
                "sandbox_denial": denial.to_dict(),
            }
        )
        return ToolResult(ok=False, content=f"{denial.explanation()}\n{result.content}", data=data)
    if not is_likely_sandbox_denied_result(result):
        return result
    data = dict(result.data)
    data.update(
        {
            "failure_kind": "sandbox",
            "sandbox_failure": SandboxFailureKind.DENIED.value,
            "sandbox_detection": "codex_heuristic",
        }
    )
    return ToolResult(ok=False, content=result.content, data=data)


def sandbox_denial_of(result: ToolResult) -> dict | None:
    """The structured denial a typed result carries, if any."""

    if (
        result.data.get("failure_kind") != "sandbox"
        or result.data.get("sandbox_failure") != SandboxFailureKind.DENIED.value
    ):
        return None
    denial = result.data.get("sandbox_denial")
    return dict(denial) if isinstance(denial, dict) else {}


__all__ = [
    "SandboxDenial",
    "classify_exec_sandbox_denial",
    "is_likely_sandbox_denied_result",
    "sandbox_denial_of",
    "windows_mxc_denial",
]
