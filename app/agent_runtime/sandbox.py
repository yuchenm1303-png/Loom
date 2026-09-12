from __future__ import annotations

import base64
import json
import os
import platform
import shutil
import subprocess
import uuid
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Mapping

from .contracts import PermissionMode, ToolEffect
from .exec_policy import current_exec_effect
from .network_policy import current_network_access_granted
from .permissions import FileSystemAccess, PermissionSnapshot, permission_snapshot


_MXC_SCHEMA_VERSION = "0.8.0-alpha"
_MXC_CONFIG_LIMIT = 22_000
_MXC_TIERS = frozenset({"base-container", "appcontainer-bfs", "appcontainer-dacl"})
_MXC_ENV_ALWAYS = frozenset({"ALLUSERSPROFILE", "APPDATA", "COMSPEC", "HOME", "HOMEDRIVE", "HOMEPATH", "LOCALAPPDATA", "NUMBER_OF_PROCESSORS", "OS", "PATH", "PATHEXT", "PROCESSOR_ARCHITECTURE", "PROGRAMDATA", "PROGRAMFILES", "PROGRAMFILES(X86)", "PROGRAMW6432", "PSMODULEPATH", "PYTHONHOME", "PYTHONPATH", "SYSTEMDRIVE", "SYSTEMROOT", "TEMP", "TMP", "USERPROFILE", "VIRTUAL_ENV", "WINDIR"})
_SECRET_ENV_MARKERS = ("API_KEY", "TOKEN", "SECRET", "PASSWORD", "PASSWD", "PRIVATE_KEY")


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
    network_access_granted: bool = False
    effective_exec_effect: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "policy": self.policy.value,
            "mode": self.mode.value,
            "backend": self.backend.value,
            "available": self.available,
            "enforced": self.enforced,
            "reason": self.reason,
            "network_isolated": self.network_isolated,
            "network_access_granted": self.network_access_granted,
            "effective_exec_effect": self.effective_exec_effect or None,
        }


@dataclass(frozen=True, slots=True)
class SandboxCommand:
    argv: tuple[str, ...]
    cwd: Path
    snapshot: SandboxSnapshot


class SandboxManager:
    """Plan OS-level isolation from resolved permission plus call-specific policy."""

    def __init__(self, *, policy: SandboxPolicy | str = SandboxPolicy.AUTO, bubblewrap_executable: str | None = None, windows_mxc_executable: str | None = None, probe_backend: bool = True, system_name: str | None = None) -> None:
        self.policy = SandboxPolicy(policy)
        self.system_name = str(system_name or platform.system()).strip().casefold()
        self.bubblewrap_executable = ""
        self.windows_mxc_executable = ""
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
                self._backend_available, self._backend_reason = self._probe_bubblewrap(self.bubblewrap_executable)
            return
        if self.system_name == "windows":
            explicit = str(windows_mxc_executable or os.environ.get("LOOM_WINDOWS_SANDBOX_EXECUTABLE") or "").strip()
            self.windows_mxc_executable = str(explicit or shutil.which("wxc-exec.exe") or shutil.which("wxc-exec") or "")
            if not self.windows_mxc_executable:
                self._backend_reason = "Microsoft MXC wxc-exec was not found. Install @microsoft/mxc-sdk or set LOOM_WINDOWS_SANDBOX_EXECUTABLE to its wxc-exec.exe."
            elif not probe_backend:
                self._backend_available = True
                self._backend_reason = "Microsoft MXC availability was accepted without a runtime probe."
            else:
                self._backend_available, self._backend_reason = self._probe_windows_mxc(self.windows_mxc_executable)
            return
        self._backend_reason = f"No Loom OS sandbox backend is implemented for {self.system_name or 'this platform'}."

    @staticmethod
    def _probe_bubblewrap(executable: str) -> tuple[bool, str]:
        try:
            completed = subprocess.run([executable, "--die-with-parent", "--ro-bind", "/", "/", "--", "/bin/true"], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, errors="replace", timeout=3, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return False, f"Bubblewrap probe failed: {type(exc).__name__}: {exc}"
        if completed.returncode == 0:
            return True, "Bubblewrap probe succeeded."
        detail = str(completed.stderr or "").strip().replace("\n", " ")[:240]
        return False, f"Bubblewrap probe exited {completed.returncode}: {detail or 'no stderr'}"

    @staticmethod
    def _probe_windows_mxc(executable: str) -> tuple[bool, str]:
        try:
            completed = subprocess.run([executable, "--probe"], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, errors="replace", timeout=8, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
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
        return SandboxManager.mode_for_permissions(permission_snapshot(permission_mode))

    def snapshot(self, *, workspace: str | Path, permissions: PermissionSnapshot | None = None, permission_mode: PermissionMode | str | None = None, network_access_granted: bool | None = None) -> SandboxSnapshot:
        if permissions is None:
            if permission_mode is None:
                raise ValueError("sandbox snapshot requires permissions or permission_mode")
            permissions = permission_snapshot(permission_mode)
        else:
            permissions = permission_snapshot(permissions)
            if permission_mode is not None and permissions.mode is not PermissionMode(permission_mode):
                raise ValueError("sandbox permission_mode does not match permission snapshot")

        mode = self.mode_for_permissions(permissions)
        effect = current_exec_effect()
        if effect is ToolEffect.READ_ONLY and mode is SandboxMode.WORKSPACE:
            mode = SandboxMode.READ_ONLY
        _ = Path(workspace).expanduser().resolve()
        network_granted = current_network_access_granted() if network_access_granted is None else bool(network_access_granted)
        effect_value = effect.value if effect is not None else ""

        if mode is SandboxMode.DISABLED:
            return SandboxSnapshot(self.policy, mode, SandboxBackend.NONE, self._backend_available, False, "Permission snapshot intentionally selects unrestricted filesystem access.", network_access_granted=network_granted, effective_exec_effect=effect_value)
        if self.policy is SandboxPolicy.OFF:
            return SandboxSnapshot(self.policy, mode, SandboxBackend.NONE, self._backend_available, False, "OS sandboxing was explicitly disabled by runtime policy.", network_access_granted=network_granted, effective_exec_effect=effect_value)
        if self._backend_available:
            backend = SandboxBackend.BUBBLEWRAP if self.system_name == "linux" else SandboxBackend.WINDOWS_MXC if self.system_name == "windows" else SandboxBackend.NONE
            return SandboxSnapshot(self.policy, mode, backend, True, True, self._backend_reason, network_isolated=not network_granted, network_access_granted=network_granted, effective_exec_effect=effect_value)
        return SandboxSnapshot(self.policy, mode, SandboxBackend.NONE, False, False, self._backend_reason, network_access_granted=network_granted, effective_exec_effect=effect_value)

    def prepare(self, *, argv: tuple[str, ...], cwd: Path, workspace: Path, permissions: PermissionSnapshot | None = None, permission_mode: PermissionMode | str | None = None, environment: Mapping[str, str] | None = None, network_access_granted: bool | None = None) -> SandboxCommand:
        root = Path(workspace).expanduser().resolve()
        resolved_cwd = Path(cwd).expanduser().resolve()
        try:
            resolved_cwd.relative_to(root)
        except ValueError as exc:
            raise ValueError("command cwd escapes the Loom workspace") from exc
        network_granted = current_network_access_granted() if network_access_granted is None else bool(network_access_granted)
        snapshot = self.snapshot(permissions=permissions, permission_mode=permission_mode, workspace=root, network_access_granted=network_granted)
        if snapshot.mode is not SandboxMode.DISABLED and self.policy is SandboxPolicy.REQUIRED and not snapshot.enforced:
            raise RuntimeError(f"OS sandbox is required but unavailable. {snapshot.reason}")
        if not snapshot.enforced:
            return SandboxCommand(tuple(argv), resolved_cwd, snapshot)
        if snapshot.backend is SandboxBackend.BUBBLEWRAP:
            return SandboxCommand(self._bubblewrap_argv(argv=tuple(argv), cwd=resolved_cwd, workspace=root, mode=snapshot.mode, network_access_granted=network_granted), Path("/"), snapshot)
        if snapshot.backend is SandboxBackend.WINDOWS_MXC:
            return SandboxCommand(self._windows_mxc_argv(argv=tuple(argv), cwd=resolved_cwd, workspace=root, mode=snapshot.mode, environment=environment or {}, network_access_granted=network_granted), resolved_cwd, snapshot)
        raise RuntimeError(f"unsupported sandbox backend: {snapshot.backend.value}")

    def _bubblewrap_argv(self, *, argv: tuple[str, ...], cwd: Path, workspace: Path, mode: SandboxMode, network_access_granted: bool) -> tuple[str, ...]:
        executable = self.bubblewrap_executable
        if not executable:
            raise RuntimeError("bubblewrap executable is unavailable")
        command: list[str] = [executable, "--die-with-parent", "--new-session", "--unshare-pid"]
        if not network_access_granted:
            command.append("--unshare-net")
        command.extend(["--ro-bind", "/", "/", "--dev", "/dev", "--proc", "/proc", "--tmpfs", "/tmp"])
        if mode is SandboxMode.WORKSPACE:
            command.extend(["--bind", str(workspace), str(workspace)])
            for name in (".git", ".loom", ".agents"):
                protected = workspace / name
                if protected.exists():
                    command.extend(["--ro-bind", str(protected), str(protected)])
        command.extend(["--chdir", str(cwd), "--"])
        command.extend(argv)
        return tuple(command)

    def _windows_mxc_argv(self, *, argv: tuple[str, ...], cwd: Path, workspace: Path, mode: SandboxMode, environment: Mapping[str, str], network_access_granted: bool) -> tuple[str, ...]:
        executable = self.windows_mxc_executable
        if not executable:
            raise RuntimeError("Microsoft MXC wxc-exec is unavailable")
        if mode is SandboxMode.DISABLED:
            return argv
        readonly_paths = self._windows_tool_read_paths(argv=argv, environment=environment)
        if mode is SandboxMode.READ_ONLY:
            readonly_paths.insert(0, str(workspace))
            readwrite_paths: list[str] = []
        else:
            readwrite_paths = [str(workspace)]
            for name in (".git", ".loom", ".agents"):
                protected = workspace / name
                if protected.exists():
                    readonly_paths.append(str(protected))
        child_env = self._windows_mxc_environment(environment)
        config: dict[str, object] = {
            "version": _MXC_SCHEMA_VERSION,
            "containerId": f"loom-{uuid.uuid4().hex[:12]}",
            "containment": "process",
            "lifecycle": {"destroyOnExit": True, "preservePolicy": False},
            "process": {"commandLine": subprocess.list2cmdline(list(argv)), "cwd": str(cwd), "timeout": 0, "env": [f"{key}={value}" for key, value in sorted(child_env.items())]},
            "filesystem": {"readwritePaths": _unique_paths(readwrite_paths), "readonlyPaths": _unique_paths(readonly_paths), "deniedPaths": []},
            "network": {"egress": {"default": "allow" if network_access_granted else "deny"}, "ingress": {"default": "deny", "hostLoopback": "deny"}},
            "ui": {"disable": True, "clipboard": "none", "injection": False},
            "processContainer": {"capabilities": [], "ui": {"isolation": "container", "desktopSystemControl": False, "systemSettings": "none", "ime": False}},
        }
        raw = json.dumps(config, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        encoded = base64.b64encode(raw).decode("ascii")
        if len(encoded) > _MXC_CONFIG_LIMIT:
            raise RuntimeError("Windows MXC sandbox configuration is too large for safe inline launch; reduce the child environment or PATH")
        return (executable, "--config-base64", encoded)

    @staticmethod
    def _windows_mxc_environment(environment: Mapping[str, str]) -> dict[str, str]:
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
        return output

    @staticmethod
    def _windows_tool_read_paths(*, argv: tuple[str, ...], environment: Mapping[str, str]) -> list[str]:
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


__all__ = ["SandboxBackend", "SandboxCommand", "SandboxManager", "SandboxMode", "SandboxPolicy", "SandboxSnapshot"]
