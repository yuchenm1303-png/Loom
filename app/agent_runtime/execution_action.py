from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from app.ai import ToolCall

from .apply_patch_action import ApplyPatchActionIdentity
from .command_approval import canonicalize_command_for_approval
from .permissions import AdditionalPermissionProfile, SandboxPermissions
from .process_runtime import validate_argv, validate_terminal_size, validate_timeout


_ACTION_BINDING_KEY = secrets.token_bytes(32)
_MAX_STDIN_CHARS = 256_000
_MAX_ENV_ENTRIES = 256
_EXEC_ARGUMENT_NAMES = frozenset(
    {
        "argv",
        "cwd",
        "stdin",
        "env",
        "timeout_seconds",
        "pty",
        "rows",
        "cols",
        "wait",
        "sandbox_permissions",
        "additional_permissions",
        "justification",
        "prefix_rule",
    }
)


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")


def _private_identity(value: object) -> str:
    return hmac.new(_ACTION_BINDING_KEY, _canonical_bytes(value), hashlib.sha256).hexdigest()


def _validate_exec_argument_shape(arguments: dict[str, object]) -> None:
    extras = sorted(set(arguments) - _EXEC_ARGUMENT_NAMES)
    if extras:
        raise ValueError(f"exec contains unsupported arguments: {', '.join(extras)}")

    raw_argv = arguments.get("argv")
    if not isinstance(raw_argv, list) or any(not isinstance(value, str) for value in raw_argv):
        raise ValueError("argv must be an array of strings")
    if "cwd" in arguments and not isinstance(arguments["cwd"], str):
        raise ValueError("cwd must be a string")
    if "stdin" in arguments and not isinstance(arguments["stdin"], str):
        raise ValueError("stdin must be a string")
    if "env" in arguments and not isinstance(arguments["env"], dict):
        raise ValueError("env must be an object of string values")
    if "timeout_seconds" in arguments and (
        isinstance(arguments["timeout_seconds"], bool)
        or not isinstance(arguments["timeout_seconds"], int)
    ):
        raise ValueError("timeout_seconds must be an integer")
    for name in ("pty", "wait"):
        if name in arguments and not isinstance(arguments[name], bool):
            raise ValueError(f"{name} must be a boolean")
    for name in ("rows", "cols"):
        if name in arguments and (
            isinstance(arguments[name], bool) or not isinstance(arguments[name], int)
        ):
            raise ValueError(f"{name} must be an integer")
    if "sandbox_permissions" in arguments and not isinstance(arguments["sandbox_permissions"], str):
        raise ValueError("sandbox_permissions must be a string")
    if "additional_permissions" in arguments and not isinstance(arguments["additional_permissions"], dict):
        raise ValueError("additional_permissions must be an object")
    if "justification" in arguments and not isinstance(arguments["justification"], str):
        raise ValueError("justification must be a string")
    if "prefix_rule" in arguments:
        rule = arguments["prefix_rule"]
        if not isinstance(rule, list) or any(not isinstance(value, str) for value in rule):
            raise ValueError("prefix_rule must be an array of strings")


def _explicit_environment(raw: object) -> dict[str, str]:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ValueError("env must be an object of string values")
    if len(raw) > _MAX_ENV_ENTRIES:
        raise ValueError("env contains too many entries")
    output: dict[str, str] = {}
    for key, value in raw.items():
        if not isinstance(key, str) or not isinstance(value, str):
            raise ValueError("env must be an object of string values")
        output[key] = value
    return output


def _workspace_cwd(workspace: str | Path, raw_cwd: object) -> tuple[str, Path]:
    if raw_cwd is None:
        requested = "."
    elif isinstance(raw_cwd, str):
        requested = raw_cwd.strip() or "."
    else:
        raise ValueError("cwd must be a string")
    root = Path(workspace).expanduser().resolve()
    resolved = (root / requested).resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError("command cwd escapes the Loom workspace") from exc
    if not resolved.is_dir():
        raise ValueError("command cwd must be a workspace directory")
    return requested, resolved


def _sandbox_request(arguments: Mapping[str, object], *, cwd: Path) -> tuple[
    SandboxPermissions,
    AdditionalPermissionProfile | None,
    str,
    tuple[str, ...],
]:
    try:
        sandbox_permissions = SandboxPermissions(
            arguments.get("sandbox_permissions", SandboxPermissions.USE_DEFAULT.value)
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("invalid sandbox_permissions") from exc
    additional = AdditionalPermissionProfile.from_mapping(arguments.get("additional_permissions"))
    if sandbox_permissions.uses_additional_permissions:
        if additional.empty:
            raise ValueError(
                "with_additional_permissions requires a non-empty additional_permissions profile"
            )
        additional_value: AdditionalPermissionProfile | None = additional.resolved(cwd=cwd)
    else:
        if not additional.empty:
            raise ValueError(
                "additional_permissions requires sandbox_permissions=with_additional_permissions"
            )
        additional_value = None
    justification = str(arguments.get("justification") or "").strip()
    if sandbox_permissions.requires_escalated_permissions and not justification:
        raise ValueError("require_escalated requires justification")
    prefix_rule = tuple(arguments.get("prefix_rule") or ())
    return sandbox_permissions, additional_value, justification, prefix_rule


def exec_environment_identity(step, overrides: Mapping[str, object] | None = None) -> str:
    """Return a process-local identity for the exact resolved child environment.

    This is a Loom pending-action integrity binding. Codex approval-cache keys do
    not include the raw/resolved environment map, so callers must not use this
    value as an approval reuse key.
    """

    environment = step.environment_policy.build(overrides)
    return _private_identity(environment)


@dataclass(frozen=True, slots=True)
class ExecApprovalCacheKey:
    """Codex-parity reusable approval identity for Loom local exec launches."""

    environment_id: str
    executable: str | None
    command: tuple[str, ...]
    cwd: str
    tty: bool
    sandbox_permissions: SandboxPermissions
    additional_permissions: AdditionalPermissionProfile | None

    def canonical(self) -> dict[str, object]:
        return {
            "environment_id": self.environment_id,
            "executable": self.executable,
            "command": list(self.command),
            "cwd": self.cwd,
            "tty": self.tty,
            "sandbox_permissions": self.sandbox_permissions.value,
            "additional_permissions": (
                self.additional_permissions.canonical()
                if self.additional_permissions is not None
                else None
            ),
        }

    def digest(self) -> str:
        return hashlib.sha256(_canonical_bytes(self.canonical())).hexdigest()


@dataclass(frozen=True, slots=True)
class ExecActionIdentity:
    """Secret-minimized identity for one model-originated exec action.

    This deliberately remains stricter than Codex's reusable approval key: Loom
    uses it to prove that a durable pending action did not change while the user
    was deciding. Approval reuse uses ``approval_cache_key`` instead.
    """

    call_id: str
    argv: tuple[str, ...]
    cwd: str
    resolved_cwd: str
    wait: bool
    timeout_seconds: int
    pty: bool
    rows: int | None
    cols: int | None
    stdin_identity: str
    explicit_environment_names: tuple[str, ...]
    explicit_environment_identity: str
    resolved_environment_identity: str
    sandbox_permissions: SandboxPermissions
    additional_permissions: AdditionalPermissionProfile | None
    justification: str
    prefix_rule: tuple[str, ...]

    @property
    def kind(self) -> str:
        return "exec_command"

    @classmethod
    def build(cls, step, call: ToolCall) -> "ExecActionIdentity":
        if str(call.name or "") != "exec":
            raise ValueError("ExecActionIdentity requires an exec tool call")
        arguments = dict(call.arguments)
        _validate_exec_argument_shape(arguments)
        argv = validate_argv(arguments.get("argv"))
        requested_cwd, resolved_cwd = _workspace_cwd(
            step.world_state.workspace_dir,
            arguments.get("cwd"),
        )
        timeout_seconds = validate_timeout(arguments.get("timeout_seconds"))
        stdin_text = arguments.get("stdin", "")
        if len(stdin_text) > _MAX_STDIN_CHARS:
            raise ValueError("stdin exceeds 256,000 characters")
        explicit_env = _explicit_environment(arguments.get("env"))
        pty = arguments.get("pty", False)
        parsed_rows, parsed_cols = validate_terminal_size(
            arguments.get("rows", 24),
            arguments.get("cols", 80),
        )
        wait = arguments.get("wait", True)
        sandbox_permissions, additional_permissions, justification, prefix_rule = _sandbox_request(
            arguments,
            cwd=resolved_cwd,
        )
        return cls(
            call_id=str(call.call_id or "").strip(),
            argv=argv,
            cwd=requested_cwd,
            resolved_cwd=str(resolved_cwd),
            wait=wait,
            timeout_seconds=timeout_seconds,
            pty=pty,
            rows=parsed_rows if pty else None,
            cols=parsed_cols if pty else None,
            stdin_identity=_private_identity(stdin_text),
            explicit_environment_names=tuple(sorted(explicit_env)),
            explicit_environment_identity=_private_identity(explicit_env),
            resolved_environment_identity=exec_environment_identity(step, explicit_env),
            sandbox_permissions=sandbox_permissions,
            additional_permissions=additional_permissions,
            justification=justification,
            prefix_rule=prefix_rule,
        )

    def approval_cache_key(self, *, environment_id: str = "local") -> ExecApprovalCacheKey:
        return ExecApprovalCacheKey(
            environment_id=str(environment_id or "local"),
            executable=self.argv[0] if self.argv else None,
            command=canonicalize_command_for_approval(self.argv),
            cwd=self.resolved_cwd,
            tty=self.pty,
            sandbox_permissions=self.sandbox_permissions,
            additional_permissions=self.additional_permissions,
        )

    def binding_payload(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "argv": list(self.argv),
            "resolved_cwd": self.resolved_cwd,
            "wait": self.wait,
            "timeout_seconds": self.timeout_seconds,
            "pty": self.pty,
            "rows": self.rows,
            "cols": self.cols,
            "stdin_identity": self.stdin_identity,
            "explicit_environment_names": list(self.explicit_environment_names),
            "explicit_environment_identity": self.explicit_environment_identity,
            "resolved_environment_identity": self.resolved_environment_identity,
            "sandbox_permissions": self.sandbox_permissions.value,
            "additional_permissions": (
                self.additional_permissions.canonical()
                if self.additional_permissions is not None
                else None
            ),
            "justification": self.justification,
            "prefix_rule": list(self.prefix_rule),
        }

    def instance_payload(self) -> dict[str, Any]:
        return {
            "call_id": self.call_id,
            "cwd": self.cwd,
            **self.binding_payload(),
        }

    def digest(self) -> str:
        return hashlib.sha256(_canonical_bytes(self.binding_payload())).hexdigest()


def execution_action_for(step, call: ToolCall) -> ExecActionIdentity | ApplyPatchActionIdentity | None:
    name = str(call.name or "")
    if name == "exec":
        return ExecActionIdentity.build(step, call)
    if name == "apply_patch":
        return ApplyPatchActionIdentity.build(step, call)
    return None


__all__ = [
    "ApplyPatchActionIdentity",
    "ExecActionIdentity",
    "ExecApprovalCacheKey",
    "exec_environment_identity",
    "execution_action_for",
]
