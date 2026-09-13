from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, replace
from enum import Enum
from pathlib import Path
from typing import Iterator, Mapping

from .sandbox import SandboxBackend, SandboxCommand, SandboxManager, SandboxMode, SandboxPolicy
from .sandbox_failure import SandboxExecutionError, SandboxFailureKind


class SandboxAttemptKind(str, Enum):
    INITIAL = "initial"
    ESCALATION = "escalation"


class SandboxAttemptSelection(str, Enum):
    POLICY = "policy"
    NO_SANDBOX = "no_sandbox"


@dataclass(frozen=True, slots=True)
class SandboxAttempt:
    """One explicit execution attempt without mutating the ambient sandbox policy."""

    kind: SandboxAttemptKind = SandboxAttemptKind.INITIAL
    index: int = 0
    selection: SandboxAttemptSelection = SandboxAttemptSelection.POLICY
    reason: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "kind", SandboxAttemptKind(self.kind))
        object.__setattr__(self, "selection", SandboxAttemptSelection(self.selection))
        object.__setattr__(self, "index", int(self.index))
        object.__setattr__(self, "reason", str(self.reason or "").strip())
        if self.index < 0:
            raise ValueError("sandbox attempt index must be non-negative")
        if self.kind is SandboxAttemptKind.INITIAL and self.index != 0:
            raise ValueError("initial sandbox attempt must use index 0")
        if self.kind is SandboxAttemptKind.ESCALATION and self.index < 1:
            raise ValueError("sandbox escalation attempt must use index >= 1")
        if (
            self.kind is SandboxAttemptKind.INITIAL
            and self.selection is SandboxAttemptSelection.NO_SANDBOX
        ):
            raise ValueError("initial sandbox attempt cannot request an escalation bypass")

    @classmethod
    def initial(cls) -> "SandboxAttempt":
        return cls()

    @classmethod
    def escalated(cls, reason: str, *, index: int = 1) -> "SandboxAttempt":
        return cls(
            kind=SandboxAttemptKind.ESCALATION,
            index=index,
            selection=SandboxAttemptSelection.NO_SANDBOX,
            reason=reason,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind.value,
            "index": self.index,
            "selection": self.selection.value,
            "reason": self.reason,
        }


_INITIAL_ATTEMPT = SandboxAttempt.initial()
_ACTIVE_ATTEMPT: ContextVar[SandboxAttempt] = ContextVar(
    "loom_sandbox_attempt",
    default=_INITIAL_ATTEMPT,
)


def current_sandbox_attempt() -> SandboxAttempt:
    return _ACTIVE_ATTEMPT.get()


@contextmanager
def sandbox_attempt_scope(attempt: SandboxAttempt) -> Iterator[SandboxAttempt]:
    resolved = attempt if isinstance(attempt, SandboxAttempt) else SandboxAttempt(**dict(attempt))
    token = _ACTIVE_ATTEMPT.set(resolved)
    try:
        yield resolved
    finally:
        _ACTIVE_ATTEMPT.reset(token)


class AttemptAwareSandboxManager:
    """Delegate normal planning, with an explicit override for the active attempt."""

    def __init__(self, base: SandboxManager) -> None:
        if isinstance(base, AttemptAwareSandboxManager):
            base = base.base
        if not isinstance(base, SandboxManager):
            raise TypeError("base sandbox manager must be SandboxManager")
        self.base = base

    def __getattr__(self, name: str):
        return getattr(self.base, name)

    def snapshot(self, *args, **kwargs):
        return self.base.snapshot(*args, **kwargs)

    def prepare(
        self,
        *,
        argv: tuple[str, ...],
        cwd: Path,
        workspace: Path,
        permissions=None,
        permission_mode=None,
        environment: Mapping[str, str] | None = None,
    ) -> SandboxCommand:
        attempt = current_sandbox_attempt()
        if attempt.selection is SandboxAttemptSelection.POLICY:
            return self.base.prepare(
                argv=argv,
                cwd=cwd,
                workspace=workspace,
                permissions=permissions,
                permission_mode=permission_mode,
                environment=environment,
            )

        root = Path(workspace).expanduser().resolve()
        resolved_cwd = Path(cwd).expanduser().resolve()
        try:
            resolved_cwd.relative_to(root)
        except ValueError as exc:
            raise ValueError("command cwd escapes the Loom workspace") from exc

        ambient = self.base.snapshot(
            permissions=permissions,
            permission_mode=permission_mode,
            workspace=root,
        )
        if ambient.mode is SandboxMode.DISABLED or ambient.policy is SandboxPolicy.OFF:
            return self.base.prepare(
                argv=argv,
                cwd=resolved_cwd,
                workspace=root,
                permissions=permissions,
                permission_mode=permission_mode,
                environment=environment,
            )
        if ambient.policy is SandboxPolicy.REQUIRED:
            raise SandboxExecutionError(
                SandboxFailureKind.CONFIGURATION,
                "sandbox escalation cannot bypass SandboxPolicy.REQUIRED",
                escalatable=False,
            )

        detail = f"Explicit sandbox escalation attempt {attempt.index} bypassed OS containment."
        if attempt.reason:
            detail = f"{detail} {attempt.reason}"
        snapshot = replace(
            ambient,
            backend=SandboxBackend.NONE,
            enforced=False,
            reason=detail,
            network_isolated=False,
        )
        return SandboxCommand(
            argv=tuple(argv),
            cwd=resolved_cwd,
            snapshot=snapshot,
        )


def ensure_attempt_aware_sandbox_manager(manager: SandboxManager | AttemptAwareSandboxManager):
    if isinstance(manager, AttemptAwareSandboxManager):
        return manager
    return AttemptAwareSandboxManager(manager)


__all__ = [
    "AttemptAwareSandboxManager",
    "SandboxAttempt",
    "SandboxAttemptKind",
    "SandboxAttemptSelection",
    "current_sandbox_attempt",
    "ensure_attempt_aware_sandbox_manager",
    "sandbox_attempt_scope",
]
