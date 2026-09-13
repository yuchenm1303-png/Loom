from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from app.ai import ToolCall

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


def exec_environment_identity(step, overrides: Mapping[str, object] | None = None) -> str:
    """Return a process-local identity for the exact resolved child environment."""

    environment = step.environment_policy.build(overrides)
    return _private_identity(environment)


@dataclass(frozen=True, slots=True)
class ExecActionIdentity:
    """Secret-minimized identity for one model-originated exec action.

    ``call_id`` and the requested cwd spelling identify the protocol instance but
    are intentionally excluded from the semantic digest. Two calls that resolve
    to the same execution action therefore share an action identity, matching
    Codex's separation between ApprovalAction request data and approval/cache keys.

    User-visible arguments remain in the ToolCall/PendingToolApproval. This object
    records execution semantics for binding without copying stdin or environment
    values into another durable representation.
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
        }

    def instance_payload(self) -> dict[str, Any]:
        return {
            "call_id": self.call_id,
            "cwd": self.cwd,
            **self.binding_payload(),
        }

    def digest(self) -> str:
        return hashlib.sha256(_canonical_bytes(self.binding_payload())).hexdigest()


def execution_action_for(step, call: ToolCall) -> ExecActionIdentity | None:
    """Resolve the typed execution action for a model call when one exists."""

    if str(call.name or "") == "exec":
        return ExecActionIdentity.build(step, call)
    return None


__all__ = [
    "ExecActionIdentity",
    "exec_environment_identity",
    "execution_action_for",
]
