from __future__ import annotations

import os
import platform
import shutil
import subprocess
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from .contracts import PermissionMode
from .permissions import FileSystemAccess, PermissionSnapshot, permission_snapshot


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


@dataclass(frozen=True, slots=True)
class SandboxSnapshot:
    policy: SandboxPolicy
    mode: SandboxMode
    backend: SandboxBackend
    available: bool
    enforced: bool
    reason: str
    network_isolated: bool = False

    def to_dict(self) -> dict[str, object]:
        return {
            "policy": self.policy.value,
            "mode": self.mode.value,
            "backend": self.backend.value,
            "available": self.available,
            "enforced": self.enforced,
            "reason": self.reason,
            "network_isolated": self.network_isolated,
        }


@dataclass(frozen=True, slots=True)
class SandboxCommand:
    argv: tuple[str, ...]
    cwd: Path
    snapshot: SandboxSnapshot


class SandboxManager:
    """Plans OS-level command isolation without pretending unavailable backends exist.

    Authorization and containment no longer interpret PermissionMode separately:
    both resolve through the same immutable PermissionSnapshot. Loom currently
    supports Bubblewrap on Linux when it is installed and a minimal probe
    succeeds. Other platforms remain explicitly unavailable.
    """

    def __init__(
        self,
        *,
        policy: SandboxPolicy | str = SandboxPolicy.AUTO,
        bubblewrap_executable: str | None = None,
        probe_backend: bool = True,
        system_name: str | None = None,
    ) -> None:
        self.policy = SandboxPolicy(policy)
        self.system_name = str(system_name or platform.system()).strip().casefold()
        explicit = str(bubblewrap_executable or "").strip()
        discovered = explicit or (shutil.which("bwrap") if self.system_name == "linux" else "")
        self.bubblewrap_executable = str(discovered or "")
        self._backend_available = False
        self._backend_reason = "No supported OS sandbox backend is available."

        if self.system_name != "linux":
            self._backend_reason = f"No Loom OS sandbox backend is implemented for {self.system_name or 'this platform'}."
        elif not self.bubblewrap_executable:
            self._backend_reason = "Bubblewrap (bwrap) was not found on PATH."
        elif not probe_backend:
            self._backend_available = True
            self._backend_reason = "Bubblewrap availability was accepted without a runtime probe."
        else:
            ok, reason = self._probe_bubblewrap(self.bubblewrap_executable)
            self._backend_available = ok
            self._backend_reason = reason

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
            return SandboxSnapshot(
                policy=self.policy,
                mode=mode,
                backend=SandboxBackend.BUBBLEWRAP,
                available=True,
                enforced=True,
                reason=self._backend_reason,
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
    ) -> SandboxCommand:
        root = Path(workspace).expanduser().resolve()
        resolved_cwd = Path(cwd).expanduser().resolve()
        try:
            resolved_cwd.relative_to(root)
        except ValueError as exc:
            raise ValueError("command cwd escapes the Loom workspace") from exc

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
                ),
                cwd=Path("/"),
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
        command.extend(["--chdir", str(cwd), "--"])
        command.extend(argv)
        return tuple(command)


__all__ = [
    "SandboxBackend",
    "SandboxCommand",
    "SandboxManager",
    "SandboxMode",
    "SandboxPolicy",
    "SandboxSnapshot",
]
