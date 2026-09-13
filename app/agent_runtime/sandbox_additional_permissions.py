from __future__ import annotations

import base64
import json
import os
from dataclasses import replace
from pathlib import Path
from typing import Mapping

from .permissions import AdditionalPermissionProfile
from .sandbox import SandboxBackend, SandboxCommand, SandboxManager, SandboxMode
from .sandbox_failure import SandboxExecutionError, SandboxFailureKind


_PROTECTED_WORKSPACE_NAMES = (".git", ".loom", ".agents")


def _norm(path: str | Path) -> str:
    return os.path.normcase(os.path.normpath(str(Path(path).expanduser().resolve())))


def _protected_workspace_paths(workspace: Path) -> tuple[Path, ...]:
    return tuple(
        path.resolve()
        for name in _PROTECTED_WORKSPACE_NAMES
        if (path := workspace / name).exists()
    )


def _overlaps(left: Path, right: Path) -> bool:
    left_value = left.resolve()
    right_value = right.resolve()
    try:
        left_value.relative_to(right_value)
        return True
    except ValueError:
        pass
    try:
        right_value.relative_to(left_value)
        return True
    except ValueError:
        return False


def _validate_write_grants(workspace: Path, profile: AdditionalPermissionProfile) -> None:
    protected = _protected_workspace_paths(workspace)
    for raw in profile.file_system_write:
        path = Path(raw).expanduser().resolve()
        if any(_overlaps(path, control_path) and path == control_path for control_path in protected):
            raise SandboxExecutionError(
                SandboxFailureKind.CONFIGURATION,
                f"additional write permission cannot override Loom control-plane protection: {path}",
                escalatable=False,
            )
        if not path.exists():
            raise SandboxExecutionError(
                SandboxFailureKind.CONFIGURATION,
                f"additional write permission path does not exist: {path}",
                escalatable=False,
            )


def _augment_bubblewrap(
    prepared: SandboxCommand,
    *,
    workspace: Path,
    profile: AdditionalPermissionProfile,
) -> SandboxCommand:
    argv = list(prepared.argv)
    try:
        chdir_index = argv.index("--chdir")
    except ValueError as exc:  # pragma: no cover - defensive against backend drift
        raise SandboxExecutionError(
            SandboxFailureKind.CONFIGURATION,
            "bubblewrap command is missing the expected --chdir boundary",
            escalatable=False,
        ) from exc

    grants: list[str] = []
    for raw in profile.file_system_write:
        path = str(Path(raw).expanduser().resolve())
        grants.extend(["--bind", path, path])

    # Bubblewrap starts from a read-only bind of /. Read grants therefore need
    # no widening; only explicit write grants change authority. Put those before
    # the protected control-plane mounts, then re-assert the protected mounts so
    # an approved broad workspace grant cannot accidentally make Loom metadata
    # writable.
    argv[chdir_index:chdir_index] = grants
    chdir_index += len(grants)
    protected_args: list[str] = []
    for path in _protected_workspace_paths(workspace):
        protected_args.extend(["--ro-bind", str(path), str(path)])
    argv[chdir_index:chdir_index] = protected_args

    reason = prepared.snapshot.reason
    if profile.file_system_write:
        reason = f"{reason} Applied approved scoped filesystem write grants."
    return SandboxCommand(
        argv=tuple(argv),
        cwd=prepared.cwd,
        snapshot=replace(prepared.snapshot, reason=reason),
    )


def _decode_mxc(argv: tuple[str, ...]) -> tuple[str, dict[str, object]]:
    if len(argv) != 3 or argv[1] != "--config-base64":
        raise SandboxExecutionError(
            SandboxFailureKind.CONFIGURATION,
            "MXC sandbox command does not use the expected inline config format",
            escalatable=False,
        )
    try:
        config = json.loads(base64.b64decode(argv[2]).decode("utf-8"))
    except (TypeError, ValueError, UnicodeDecodeError) as exc:
        raise SandboxExecutionError(
            SandboxFailureKind.CONFIGURATION,
            "MXC sandbox command contains an invalid inline config",
            escalatable=False,
        ) from exc
    if not isinstance(config, dict):
        raise SandboxExecutionError(
            SandboxFailureKind.CONFIGURATION,
            "MXC sandbox command contains a non-object config",
            escalatable=False,
        )
    return argv[0], config


def _unique_paths(values: list[str]) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for value in values:
        key = _norm(value)
        if key in seen:
            continue
        seen.add(key)
        output.append(str(Path(value).expanduser().resolve()))
    return output


def _augment_windows_mxc(
    prepared: SandboxCommand,
    *,
    workspace: Path,
    profile: AdditionalPermissionProfile,
) -> SandboxCommand:
    executable, config = _decode_mxc(prepared.argv)
    filesystem = config.get("filesystem")
    if not isinstance(filesystem, dict):
        raise SandboxExecutionError(
            SandboxFailureKind.CONFIGURATION,
            "MXC sandbox config is missing filesystem policy",
            escalatable=False,
        )

    readwrite = [str(value) for value in filesystem.get("readwritePaths", [])]
    readonly = [str(value) for value in filesystem.get("readonlyPaths", [])]
    readwrite.extend(str(Path(value).expanduser().resolve()) for value in profile.file_system_write)
    readonly.extend(str(Path(value).expanduser().resolve()) for value in profile.file_system_read)

    protected = _protected_workspace_paths(workspace)
    protected_keys = {_norm(path) for path in protected}
    readwrite = [value for value in _unique_paths(readwrite) if _norm(value) not in protected_keys]
    readonly.extend(str(path) for path in protected)
    readonly = _unique_paths(readonly)

    filesystem["readwritePaths"] = readwrite
    filesystem["readonlyPaths"] = readonly

    network = config.get("network")
    if not isinstance(network, dict):
        raise SandboxExecutionError(
            SandboxFailureKind.CONFIGURATION,
            "MXC sandbox config is missing network policy",
            escalatable=False,
        )
    if profile.network_enabled is True:
        egress = network.setdefault("egress", {})
        if not isinstance(egress, dict):
            raise SandboxExecutionError(
                SandboxFailureKind.CONFIGURATION,
                "MXC sandbox config has an invalid egress policy",
                escalatable=False,
            )
        egress["default"] = "allow"

    raw = json.dumps(config, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    encoded = base64.b64encode(raw).decode("ascii")
    # Keep the same conservative size bound as SandboxManager without importing
    # its private constant.
    if len(encoded) > 22_000:
        raise SandboxExecutionError(
            SandboxFailureKind.CONFIGURATION,
            "MXC sandbox configuration is too large after applying approved permissions",
            escalatable=False,
        )

    reason = f"{prepared.snapshot.reason} Applied approved scoped additional permissions."
    snapshot = replace(
        prepared.snapshot,
        reason=reason,
        network_isolated=(
            prepared.snapshot.network_isolated and profile.network_enabled is not True
        ),
    )
    return SandboxCommand(
        argv=(executable, "--config-base64", encoded),
        cwd=prepared.cwd,
        snapshot=snapshot,
    )


def prepare_with_additional_permissions(
    manager: SandboxManager,
    *,
    argv: tuple[str, ...],
    cwd: Path,
    workspace: Path,
    permissions=None,
    permission_mode=None,
    environment: Mapping[str, str] | None = None,
    profile: AdditionalPermissionProfile,
) -> SandboxCommand:
    """Apply one approved AdditionalPermissionProfile without leaving the sandbox.

    This is the Loom platform adapter for Codex's effective-permission-profile
    merge. Only authority that the active backend can prove is widened. Unknown
    backends and unavailable containment fail closed.
    """

    root = Path(workspace).expanduser().resolve()
    resolved_cwd = Path(cwd).expanduser().resolve()
    resolved_profile = profile.resolved(cwd=resolved_cwd)
    if resolved_profile.empty:
        raise SandboxExecutionError(
            SandboxFailureKind.CONFIGURATION,
            "additional-permissions attempt requires a non-empty profile",
            escalatable=False,
        )

    ambient = manager.snapshot(
        permissions=permissions,
        permission_mode=permission_mode,
        workspace=root,
    )
    if ambient.mode is SandboxMode.DISABLED:
        # The baseline already grants unrestricted filesystem authority; an
        # additional profile cannot widen it. Preserve the normal unsandboxed
        # execution path rather than inventing a containment failure.
        return manager.prepare(
            argv=argv,
            cwd=resolved_cwd,
            workspace=root,
            permissions=permissions,
            permission_mode=permission_mode,
            environment=environment,
        )
    if not ambient.enforced:
        raise SandboxExecutionError(
            SandboxFailureKind.CONFIGURATION,
            "additional permissions require an enforced sandbox",
            escalatable=False,
        )

    _validate_write_grants(root, resolved_profile)
    prepared = manager.prepare(
        argv=argv,
        cwd=resolved_cwd,
        workspace=root,
        permissions=permissions,
        permission_mode=permission_mode,
        environment=environment,
    )
    if prepared.snapshot.backend is SandboxBackend.BUBBLEWRAP:
        return _augment_bubblewrap(
            prepared,
            workspace=root,
            profile=resolved_profile,
        )
    if prepared.snapshot.backend is SandboxBackend.WINDOWS_MXC:
        return _augment_windows_mxc(
            prepared,
            workspace=root,
            profile=resolved_profile,
        )
    raise SandboxExecutionError(
        SandboxFailureKind.CONFIGURATION,
        f"sandbox backend cannot enforce additional permissions: {prepared.snapshot.backend.value}",
        escalatable=False,
    )


__all__ = ["prepare_with_additional_permissions"]
