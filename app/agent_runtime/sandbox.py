from __future__ import annotations

import base64
import json
import os
import platform
import shutil
import subprocess
import tempfile
import uuid
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Mapping

from .contracts import PermissionMode
from .sandbox_python_hook import hook_directory, python_hook_enabled
from .permissions import FileSystemAccess, PermissionSnapshot, permission_snapshot
from .windows_sandbox_prep import (
    LOOM_SANDBOX_TOOLS_CAPABILITY,
    WindowsSandboxPrepStatus,
    sandbox_can_read,
    windows_sandbox_prep_status,
)


_MXC_SCHEMA_VERSION = "0.8.0-alpha"
_MXC_CONFIG_LIMIT = 22_000
_MXC_TIERS = frozenset({"base-container", "appcontainer-bfs", "appcontainer-dacl"})
# In this tier MXC enforces nothing itself: the AppContainer access check is the
# boundary, and MXC edits DACLs to open each declared path.
_MXC_DACL_TIER = "appcontainer-dacl"
_MXC_PROBE_SECONDS = 60
_MXC_ENV_ALWAYS = frozenset(
    {
        "ALLUSERSPROFILE",
        "APPDATA",
        "COMSPEC",
        "HOME",
        "HOMEDRIVE",
        "HOMEPATH",
        "LOCALAPPDATA",
        "NUMBER_OF_PROCESSORS",
        "OS",
        "PATH",
        "PATHEXT",
        "PROCESSOR_ARCHITECTURE",
        "PROGRAMDATA",
        "PROGRAMFILES",
        "PROGRAMFILES(X86)",
        "PROGRAMW6432",
        "PSMODULEPATH",
        "PYTHONHOME",
        "PYTHONPATH",
        "SYSTEMDRIVE",
        "SYSTEMROOT",
        "TEMP",
        "TMP",
        "USERPROFILE",
        "VIRTUAL_ENV",
        "WINDIR",
    }
)
_SECRET_ENV_MARKERS = ("API_KEY", "TOKEN", "SECRET", "PASSWORD", "PASSWD", "PRIVATE_KEY")
# With core.symlinks=true (Git for Windows' default where symlinks work) git enters its work
# tree through GetFinalPathNameByHandleW, and an AppContainer is refused that call: `git diff`,
# `grep`, `blame` and `reset --hard` stop with "this operation must be run in a work tree".
# The sandbox cannot create symlinks either (WinError 1314), so a git that sees a file system
# without them loses nothing there. Measured with Git for Windows on Windows 11 23H2.
_WINDOWS_GIT_CONFIG = (("core.symlinks", "false"),)


class SandboxPolicy(str, Enum):
    AUTO = "auto"
    REQUIRED = "required"
    OFF = "off"


class SandboxMode(str, Enum):
    DISABLED = "disabled"
    READ_ONLY = "read-only"
    WORKSPACE = "workspace"


class SandboxBackend(str, Enum):
    NONE = "none"
    BUBBLEWRAP = "bubblewrap"
    WINDOWS_MXC = "windows-mxc"


@dataclass(frozen=True, slots=True)
class SandboxSnapshot:
    policy: SandboxPolicy
    mode: SandboxMode
    backend: SandboxBackend
    available: bool
    enforced: bool
    reason: str
    network_isolated: bool = False
    # True when the sandbox leaves the Windows display subsystem (Win32k)
    # reachable. Console developer tools (node, git, PowerShell, java) cannot
    # start without it, but a command can then read the screen. Clipboard,
    # input injection and other processes' windows stay blocked.
    display_access: bool = False

    def to_dict(self) -> dict[str, object]:
        return {
            "policy": self.policy.value,
            "mode": self.mode.value,
            "backend": self.backend.value,
            "available": self.available,
            "enforced": self.enforced,
            "reason": self.reason,
            "network_isolated": self.network_isolated,
            "display_access": self.display_access,
        }


@dataclass(frozen=True, slots=True)
class SandboxCommand:
    argv: tuple[str, ...]
    cwd: Path
    snapshot: SandboxSnapshot


class SandboxManager:
    """Plan OS-level command isolation from the resolved permission snapshot.

    Linux uses Bubblewrap when available. Windows can use Microsoft MXC's
    ``wxc-exec`` ProcessContainer launcher. AUTO records an honest unsandboxed
    fallback when no usable backend exists; REQUIRED fails closed; OFF skips
    OS sandboxing. Unrestricted filesystem access intentionally bypasses it.
    """

    def __init__(
        self,
        *,
        policy: SandboxPolicy | str = SandboxPolicy.AUTO,
        bubblewrap_executable: str | None = None,
        windows_mxc_executable: str | None = None,
        windows_ui_access: bool | None = None,
        windows_git_compat: bool | None = None,
        probe_backend: bool = True,
        system_name: str | None = None,
    ) -> None:
        self.policy = SandboxPolicy(policy)
        self.system_name = str(system_name or platform.system()).strip().casefold()
        self.bubblewrap_executable = ""
        self.windows_mxc_executable = ""
        self.windows_mxc_tier = ""
        self.windows_ui_access = (
            _windows_ui_access_from_environment()
            if windows_ui_access is None
            else bool(windows_ui_access)
        )
        self.windows_git_compat = (
            _windows_git_compat_from_environment()
            if windows_git_compat is None
            else bool(windows_git_compat)
        )
        self._backend_available = False
        self._backend_reason = "No supported OS sandbox backend is available."

        if self.system_name == "linux":
            explicit = str(bubblewrap_executable or "").strip()
            self.bubblewrap_executable = str(explicit or shutil.which("bwrap") or "")
            if not self.bubblewrap_executable:
                self._backend_reason = "Bubblewrap (bwrap) was not found on PATH."
            elif not probe_backend:
                self._backend_available = True
                self._backend_reason = "Bubblewrap availability was accepted without a runtime probe."
            else:
                ok, reason = self._probe_bubblewrap(self.bubblewrap_executable)
                self._backend_available = ok
                self._backend_reason = reason
            return

        if self.system_name == "windows":
            explicit = str(
                windows_mxc_executable
                or os.environ.get("LOOM_WINDOWS_SANDBOX_EXECUTABLE")
                or ""
            ).strip()
            self.windows_mxc_executable = str(
                explicit
                or shutil.which("wxc-exec.exe")
                or shutil.which("wxc-exec")
                or ""
            )
            if not self.windows_mxc_executable:
                self._backend_reason = (
                    "Microsoft MXC wxc-exec was not found. Install @microsoft/mxc-sdk "
                    "or set LOOM_WINDOWS_SANDBOX_EXECUTABLE to its wxc-exec.exe."
                )
            elif not probe_backend:
                self._backend_available = True
                self._backend_reason = "Microsoft MXC availability was accepted without a runtime probe."
            else:
                ok, reason = self._probe_windows_mxc(self.windows_mxc_executable)
                self._backend_available = ok
                self._backend_reason = reason
                if ok:
                    self.windows_mxc_tier = _probed_tier(reason)
                    if self.windows_mxc_tier == _MXC_DACL_TIER:
                        prep = self.windows_prep_status()
                        if not prep.complete:
                            self._backend_reason = (
                                f"{reason} Host preparation missing: {'; '.join(prep.missing())}."
                                + (f" Run once as administrator: {prep.command()}" if prep.command() else "")
                            )
            return

        self._backend_reason = (
            f"No Loom OS sandbox backend is implemented for {self.system_name or 'this platform'}."
        )

    @staticmethod
    def _probe_bubblewrap(executable: str) -> tuple[bool, str]:
        try:
            completed = subprocess.run(
                [executable, "--die-with-parent", "--ro-bind", "/", "/", "--", "/bin/true"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
                errors="replace",
                timeout=3,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return False, f"Bubblewrap probe failed: {type(exc).__name__}: {exc}"
        if completed.returncode == 0:
            return True, "Bubblewrap probe succeeded."
        detail = str(completed.stderr or "").strip().replace("\n", " ")[:240]
        return False, f"Bubblewrap probe exited {completed.returncode}: {detail or 'no stderr'}"

    @staticmethod
    def _probe_windows_mxc(executable: str) -> tuple[bool, str]:
        """Require MXC's read-only platform probe, not mere executable presence."""

        try:
            # On Windows a descendant can inherit a pipe after the probe exits.
            # A pipe read then waits forever for EOF, even after a timeout.
            # Regular files keep both process waiting and output reads bounded.
            with tempfile.TemporaryFile(mode="w+", encoding="utf-8", errors="replace") as stdout, tempfile.TemporaryFile(
                mode="w+", encoding="utf-8", errors="replace"
            ) as stderr:
                process = subprocess.Popen(
                    [executable, "--probe"],
                    stdin=subprocess.DEVNULL,
                    stdout=stdout,
                    stderr=stderr,
                    text=True,
                    errors="replace",
                )
                try:
                    returncode = process.wait(timeout=_MXC_PROBE_SECONDS)
                except subprocess.TimeoutExpired:
                    # --probe first restores ACEs an interrupted sandbox left
                    # behind (measured: 0.1 s normally, 38 s while restoring).
                    # Killing it mid-restore leaves the ACEs for the next probe,
                    # so every later probe times out the same way. Let it finish.
                    return False, (
                        f"Microsoft MXC probe did not finish within {_MXC_PROBE_SECONDS} s; it may be "
                        "restoring ACLs left by an interrupted sandbox and was left to finish"
                    )
                stdout.seek(0)
                stderr.seek(0)
                completed = subprocess.CompletedProcess(process.args, returncode, stdout.read(65536), stderr.read(65536))
        except OSError as exc:
            return False, f"Microsoft MXC probe failed: {type(exc).__name__}: {exc}"
        if completed.returncode != 0:
            detail = str(completed.stderr or completed.stdout or "").strip().replace("\n", " ")[:240]
            return False, f"Microsoft MXC probe exited {completed.returncode}: {detail or 'no output'}"
        try:
            payload = json.loads(str(completed.stdout or ""))
        except (TypeError, ValueError) as exc:
            return False, f"Microsoft MXC probe returned invalid JSON: {type(exc).__name__}: {exc}"
        if not isinstance(payload, dict):
            return False, "Microsoft MXC probe returned a non-object JSON payload."
        tier = str(payload.get("tier") or "").strip().casefold()
        if tier not in _MXC_TIERS:
            return False, f"Microsoft MXC probe did not report a usable isolation tier: {tier or 'missing'}"
        warnings = payload.get("warnings")
        warning_text = ""
        if isinstance(warnings, list):
            values = [str(item).strip().replace("\n", " ") for item in warnings if str(item).strip()]
            if values:
                warning_text = f" Warnings: {'; '.join(values)[:220]}"
        return True, f"Microsoft MXC probe succeeded with {tier}.{warning_text}"

    def windows_prep_status(
        self,
        *,
        tool_dirs: tuple[str, ...] = (),
        workspace: str | Path | None = None,
        force: bool = False,
    ) -> WindowsSandboxPrepStatus:
        """Read-only view of the elevated host preparation MXC's DACL tier needs."""

        return windows_sandbox_prep_status(
            sandbox_executable=self.windows_mxc_executable or None,
            tool_dirs=tool_dirs,
            workspace=workspace,
            force=force,
        )

    @staticmethod
    def mode_for_permissions(permissions: PermissionSnapshot) -> SandboxMode:
        access = permission_snapshot(permissions).file_system_access
        if access is FileSystemAccess.UNRESTRICTED:
            return SandboxMode.DISABLED
        if access is FileSystemAccess.READ_ONLY:
            return SandboxMode.READ_ONLY
        return SandboxMode.WORKSPACE

    @staticmethod
    def mode_for_permission(permission_mode: PermissionMode | str) -> SandboxMode:
        """Compatibility helper resolved through the canonical snapshot table."""

        return SandboxManager.mode_for_permissions(permission_snapshot(permission_mode))

    def snapshot(
        self,
        *,
        workspace: str | Path,
        permissions: PermissionSnapshot | None = None,
        permission_mode: PermissionMode | str | None = None,
    ) -> SandboxSnapshot:
        if permissions is None:
            if permission_mode is None:
                raise ValueError("sandbox snapshot requires permissions or permission_mode")
            permissions = permission_snapshot(permission_mode)
        else:
            permissions = permission_snapshot(permissions)
            if permission_mode is not None and permissions.mode is not PermissionMode(permission_mode):
                raise ValueError("sandbox permission_mode does not match permission snapshot")

        mode = self.mode_for_permissions(permissions)
        _ = Path(workspace).expanduser().resolve()

        if mode is SandboxMode.DISABLED:
            return SandboxSnapshot(
                policy=self.policy,
                mode=mode,
                backend=SandboxBackend.NONE,
                available=self._backend_available,
                enforced=False,
                reason="Permission snapshot intentionally selects unrestricted filesystem access.",
            )
        if self.policy is SandboxPolicy.OFF:
            return SandboxSnapshot(
                policy=self.policy,
                mode=mode,
                backend=SandboxBackend.NONE,
                available=self._backend_available,
                enforced=False,
                reason="OS sandboxing was explicitly disabled by runtime policy.",
            )
        if self._backend_available:
            if self.system_name == "linux":
                backend = SandboxBackend.BUBBLEWRAP
                network_isolated = False
            elif self.system_name == "windows":
                backend = SandboxBackend.WINDOWS_MXC
                network_isolated = True
            else:  # defensive: unsupported platforms never mark a backend available
                backend = SandboxBackend.NONE
                network_isolated = False
            return SandboxSnapshot(
                policy=self.policy,
                mode=mode,
                backend=backend,
                available=True,
                enforced=True,
                reason=self._backend_reason,
                network_isolated=network_isolated,
                display_access=backend is SandboxBackend.WINDOWS_MXC and self.windows_ui_access,
            )
        return SandboxSnapshot(
            policy=self.policy,
            mode=mode,
            backend=SandboxBackend.NONE,
            available=False,
            enforced=False,
            reason=self._backend_reason,
        )

    def prepare(
        self,
        *,
        argv: tuple[str, ...],
        cwd: Path,
        workspace: Path,
        permissions: PermissionSnapshot | None = None,
        permission_mode: PermissionMode | str | None = None,
        environment: Mapping[str, str] | None = None,
        scratch: Path | None = None,
    ) -> SandboxCommand:
        root = Path(workspace).expanduser().resolve()
        resolved_cwd = Path(cwd).expanduser().resolve()
        try:
            resolved_cwd.relative_to(root)
        except ValueError as exc:
            raise ValueError("command cwd escapes the Loom workspace") from exc
        # The turn's own scratch directory: Loom-owned, outside the project. The
        # prompt sends probes there and the file tools write there, so a command
        # must be able to run them; it gets the same access as the workspace.
        scratch = Path(scratch).expanduser().resolve() if scratch is not None and Path(scratch).is_dir() else None

        snapshot = self.snapshot(
            permissions=permissions,
            permission_mode=permission_mode,
            workspace=root,
        )
        if (
            snapshot.mode is not SandboxMode.DISABLED
            and self.policy is SandboxPolicy.REQUIRED
            and not snapshot.enforced
        ):
            raise RuntimeError(f"OS sandbox is required but unavailable. {snapshot.reason}")
        if not snapshot.enforced:
            return SandboxCommand(argv=tuple(argv), cwd=resolved_cwd, snapshot=snapshot)

        if snapshot.backend is SandboxBackend.BUBBLEWRAP:
            return SandboxCommand(
                argv=self._bubblewrap_argv(
                    argv=tuple(argv),
                    cwd=resolved_cwd,
                    workspace=root,
                    mode=snapshot.mode,
                    scratch=scratch,
                ),
                cwd=Path("/"),
                snapshot=snapshot,
            )
        if snapshot.backend is SandboxBackend.WINDOWS_MXC:
            return SandboxCommand(
                argv=self._windows_mxc_argv(
                    argv=tuple(argv),
                    cwd=resolved_cwd,
                    workspace=root,
                    mode=snapshot.mode,
                    environment=environment or {},
                    scratch=scratch,
                ),
                cwd=resolved_cwd,
                snapshot=snapshot,
            )
        raise RuntimeError(f"unsupported sandbox backend: {snapshot.backend.value}")

    def _bubblewrap_argv(
        self,
        *,
        argv: tuple[str, ...],
        cwd: Path,
        workspace: Path,
        mode: SandboxMode,
        scratch: Path | None = None,
    ) -> tuple[str, ...]:
        executable = self.bubblewrap_executable
        if not executable:
            raise RuntimeError("bubblewrap executable is unavailable")

        command: list[str] = [
            executable,
            "--die-with-parent",
            "--new-session",
            "--unshare-pid",
            "--ro-bind",
            "/",
            "/",
            "--dev",
            "/dev",
            "--proc",
            "/proc",
            "--tmpfs",
            "/tmp",
        ]
        if mode is SandboxMode.WORKSPACE:
            command.extend(["--bind", str(workspace), str(workspace)])
            # Protect control-plane metadata even when the project itself is writable.
            for name in (".git", ".loom", ".agents"):
                protected = workspace / name
                if protected.exists():
                    command.extend(["--ro-bind", str(protected), str(protected)])
            if scratch is not None:
                command.extend(["--bind", str(scratch), str(scratch)])
        command.extend(["--chdir", str(cwd), "--"])
        command.extend(argv)
        return tuple(command)

    def _windows_mxc_argv(
        self,
        *,
        argv: tuple[str, ...],
        cwd: Path,
        workspace: Path,
        mode: SandboxMode,
        environment: Mapping[str, str],
        scratch: Path | None = None,
    ) -> tuple[str, ...]:
        executable = self.windows_mxc_executable
        if not executable:
            raise RuntimeError("Microsoft MXC wxc-exec is unavailable")
        if mode is SandboxMode.DISABLED:
            return argv

        readonly_paths = self._windows_tool_read_paths(argv=argv, environment=environment)
        if self.windows_mxc_tier == _MXC_DACL_TIER:
            # MXC's own "already accessible" check ignores capability ACEs, so
            # a declared folder that the prep script opened to Loom's capability
            # (C:\Program Files\nodejs) would still make it try, and fail, to
            # rewrite a DACL this user cannot edit. Undeclared, the kernel's
            # AppContainer check grants it through the capability.
            readonly_paths = [path for path in readonly_paths if not sandbox_can_read(path)]
        # Python's 0o700 directories (every tempfile.mkdtemp) are otherwise
        # unwritable for the AppContainer that created them; see sandbox_python_hook.
        python_hook = hook_directory() if python_hook_enabled() else None
        if python_hook is not None:
            readonly_paths.append(str(python_hook))
        if mode is SandboxMode.READ_ONLY:
            readonly_paths.insert(0, str(workspace))
            readwrite_paths: list[str] = []
            if scratch is not None:
                readonly_paths.append(str(scratch))
        else:
            readwrite_paths = [str(workspace)] + ([str(scratch)] if scratch is not None else [])
            for name in (".git", ".loom", ".agents"):
                protected = workspace / name
                if protected.exists():
                    readonly_paths.append(str(protected))

        child_env = self._windows_mxc_environment(environment)
        if python_hook is not None:
            key = next((name for name in child_env if name.upper() == "PYTHONPATH"), "PYTHONPATH")
            child_env[key] = os.pathsep.join(filter(None, (str(python_hook), child_env.get(key, ""))))
        config: dict[str, object] = {
            "version": _MXC_SCHEMA_VERSION,
            "containerId": f"loom-{uuid.uuid4().hex[:12]}",
            "containment": "process",
            "lifecycle": {"destroyOnExit": True, "preservePolicy": False},
            "process": {
                "commandLine": subprocess.list2cmdline(list(argv)),
                "cwd": str(cwd),
                "timeout": 0,
                "env": [f"{key}={value}" for key, value in sorted(child_env.items())],
            },
            "filesystem": {
                "readwritePaths": _unique_paths(readwrite_paths),
                "readonlyPaths": _unique_paths(readonly_paths),
                "deniedPaths": [],
            },
            "network": {
                "egress": {"default": "deny"},
                "ingress": {"default": "deny", "hostLoopback": "deny"},
            },
            # With every Win32k call blocked, anything that links user32 dies
            # with STATUS_DLL_INIT_FAILED (0xC0000142): node, PowerShell, git,
            # java, curl, where. Measured on Windows 11 23H2 (MXC's AppContainer
            # +DACL tier) with Win32k allowed: the clipboard, keyboard/mouse
            # injection, other processes' windows and key observation stay
            # blocked, but the process can still read the screen, because
            # wxc-exec pins the child to winsta0\default and offers no private
            # desktop. LOOM_WINDOWS_SANDBOX_UI=locked restores the lockdown.
            "ui": {"disable": not self.windows_ui_access, "clipboard": "none", "injection": False},
            "processContainer": {
                "capabilities": [LOOM_SANDBOX_TOOLS_CAPABILITY],
                "ui": {
                    "isolation": "container",
                    "desktopSystemControl": False,
                    "systemSettings": "none",
                    "ime": False,
                },
            },
        }
        raw = json.dumps(config, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        encoded = base64.b64encode(raw).decode("ascii")
        if len(encoded) > _MXC_CONFIG_LIMIT:
            raise RuntimeError(
                "Windows MXC sandbox configuration is too large for safe inline launch; "
                "reduce the child environment or PATH"
            )
        return (executable, "--config-base64", encoded)

    def _windows_mxc_environment(self, environment: Mapping[str, str]) -> dict[str, str]:
        output: dict[str, str] = {}
        for raw_name, raw_value in environment.items():
            name = str(raw_name)
            upper = name.upper()
            if any(marker in upper for marker in _SECRET_ENV_MARKERS):
                continue
            if upper in _MXC_ENV_ALWAYS or upper.startswith("LOOM_EXEC_"):
                value = str(raw_value)
                if "\x00" not in value:
                    output[name] = value
        if self.windows_git_compat:
            # GIT_CONFIG_* is not forwarded from the caller, so there is no count to extend.
            # Every git the command starts, scripts included, reads these.
            for index, (key, value) in enumerate(_WINDOWS_GIT_CONFIG):
                output[f"GIT_CONFIG_KEY_{index}"] = key
                output[f"GIT_CONFIG_VALUE_{index}"] = value
            output["GIT_CONFIG_COUNT"] = str(len(_WINDOWS_GIT_CONFIG))
        return output

    @staticmethod
    def _windows_tool_read_paths(
        *,
        argv: tuple[str, ...],
        environment: Mapping[str, str],
    ) -> list[str]:
        """Grant only concrete runtime roots needed to start the target command.

        ProcessContainer's AppContainer+DACL fallback mutates ACLs on every
        declared path. Passing the host's entire PATH is both unnecessarily
        broad and can fail on stale/nonexistent entries. Resolve the actual
        executable and a small set of existing runtime roots instead.
        """

        output: list[str] = []
        path_value = str(environment.get("PATH") or environment.get("Path") or "")

        program = str(argv[0] if argv else "").strip().strip('"')
        candidate = Path(program)
        resolved_program = None
        if candidate.is_absolute():
            if candidate.exists():
                resolved_program = candidate.resolve()
                output.append(str(resolved_program.parent))
        else:
            located = shutil.which(program, path=path_value or None)
            if located:
                resolved_program = Path(located).resolve()
                output.append(str(resolved_program.parent))

        # A Windows venv launcher reads pyvenv.cfg beside/above Scripts and then
        # loads the base interpreter. Directly launching .venv/Scripts/python.exe
        # does not set VIRTUAL_ENV, so environment-only discovery misses both.
        if resolved_program is not None and resolved_program.name.casefold() in {"python.exe", "pythonw.exe"}:
            for root in (resolved_program.parent, resolved_program.parent.parent):
                config = root / "pyvenv.cfg"
                if not config.is_file():
                    continue
                output.append(str(root))
                with config.open(encoding="utf-8-sig") as handle:
                    text = handle.read(16_384)
                for line in text.splitlines():
                    key, separator, value = line.partition("=")
                    if separator and key.strip().casefold() == "home":
                        home = Path(value.strip())
                        if home.is_absolute() and (home / "python.exe").is_file():
                            output.append(str(home.resolve()))
                break

        for name in ("SYSTEMROOT", "WINDIR", "PYTHONHOME", "VIRTUAL_ENV"):
            value = str(environment.get(name) or "").strip().strip('"')
            if not value:
                continue
            path = Path(value)
            if path.is_absolute() and path.exists():
                output.append(str(path.resolve()))

        python_path = str(environment.get("PYTHONPATH") or "")
        for entry in python_path.split(os.pathsep):
            text = entry.strip().strip('"')
            if not text:
                continue
            path = Path(text)
            if path.is_absolute() and path.exists():
                output.append(str(path.resolve()))

        return _unique_paths(output)


def _windows_ui_access_from_environment() -> bool:
    value = str(os.environ.get("LOOM_WINDOWS_SANDBOX_UI") or "").strip().casefold()
    return value not in {"locked", "lockdown", "off", "disabled", "0", "false", "no"}


def _windows_git_compat_from_environment() -> bool:
    value = str(os.environ.get("LOOM_WINDOWS_SANDBOX_GIT_SYMLINKS") or "").strip().casefold()
    return value not in {"keep", "machine", "off", "disabled", "0", "false", "no"}


def _probed_tier(reason: str) -> str:
    """The isolation tier named by ``_probe_windows_mxc``'s success reason."""

    prefix = "Microsoft MXC probe succeeded with "
    text = str(reason or "")
    if not text.startswith(prefix):
        return ""
    return text[len(prefix):].split(".", 1)[0].strip().casefold()


def _unique_paths(values: list[str]) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        if not text:
            continue
        key = os.path.normcase(os.path.normpath(text))
        if key in seen:
            continue
        seen.add(key)
        output.append(text)
    return output


__all__ = [
    "SandboxBackend",
    "SandboxCommand",
    "SandboxManager",
    "SandboxMode",
    "SandboxPolicy",
    "SandboxSnapshot",
]
