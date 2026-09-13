from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, replace
from enum import Enum
from pathlib import Path
from typing import Iterator, Mapping

from .permissions import AdditionalPermissionProfile, SandboxPermissions
from .sandbox import SandboxBackend, SandboxCommand, SandboxManager, SandboxMode, SandboxPolicy
from .sandbox_failure import SandboxExecutionError, SandboxFailureKind


class SandboxAttemptKind(str, Enum):
    INITIAL = "initial"
    RETRY = "retry"
    # Backward-compatible spelling for persisted/test data from #121.
    ESCALATION = "retry"


class SandboxAttemptSelection(str, Enum):
    POLICY = "policy"
    ADDITIONAL_PERMISSIONS = "additional_permissions"
    NO_SANDBOX = "no_sandbox"


@dataclass(frozen=True, slots=True)
class SandboxAttempt:
    """One explicit execution attempt without mutating ambient sandbox policy."""

    kind: SandboxAttemptKind = SandboxAttemptKind.INITIAL
    index: int = 0
    selection: SandboxAttemptSelection = SandboxAttemptSelection.POLICY
    sandbox_permissions: SandboxPermissions = SandboxPermissions.USE_DEFAULT
    additional_permissions: AdditionalPermissionProfile | None = None
    reason: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "kind", SandboxAttemptKind(self.kind))
        object.__setattr__(self, "selection", SandboxAttemptSelection(self.selection))
        object.__setattr__(self, "sandbox_permissions", SandboxPermissions(self.sandbox_permissions))
        object.__setattr__(self, "index", int(self.index))
        object.__setattr__(self, "reason", str(self.reason or "").strip())
        if self.index < 0:
            raise ValueError("sandbox attempt index must be non-negative")
        if self.kind is SandboxAttemptKind.INITIAL and self.index != 0:
            raise ValueError("initial sandbox attempt must use index 0")
        if self.kind is SandboxAttemptKind.RETRY and self.index < 1:
            raise ValueError("sandbox retry attempt must use index >= 1")
        if self.selection is SandboxAttemptSelection.ADDITIONAL_PERMISSIONS:
            if self.sandbox_permissions is not SandboxPermissions.WITH_ADDITIONAL_PERMISSIONS:
                raise ValueError("additional-permissions attempt requires matching sandbox_permissions")
            if self.additional_permissions is None or self.additional_permissions.empty:
                raise ValueError("additional-permissions attempt requires a non-empty profile")
        elif self.additional_permissions is not None:
            raise ValueError("additional_permissions only belongs to an additional-permissions attempt")
        if (
            self.selection is SandboxAttemptSelection.NO_SANDBOX
            and self.kind is SandboxAttemptKind.INITIAL
            and self.sandbox_permissions is not SandboxPermissions.REQUIRE_ESCALATED
        ):
            raise ValueError("initial no-sandbox attempt must be an explicit require_escalated request")

    @classmethod
    def initial(
        cls,
        sandbox_permissions: SandboxPermissions = SandboxPermissions.USE_DEFAULT,
        additional_permissions: AdditionalPermissionProfile | None = None,
    ) -> "SandboxAttempt":
        resolved = SandboxPermissions(sandbox_permissions)
        if resolved is SandboxPermissions.REQUIRE_ESCALATED:
            selection = SandboxAttemptSelection.NO_SANDBOX
        elif resolved is SandboxPermissions.WITH_ADDITIONAL_PERMISSIONS:
            selection = SandboxAttemptSelection.ADDITIONAL_PERMISSIONS
        else:
            selection = SandboxAttemptSelection.POLICY
        return cls(
            kind=SandboxAttemptKind.INITIAL,
            index=0,
            selection=selection,
            sandbox_permissions=resolved,
            additional_permissions=additional_permissions,
        )

    @classmethod
    def retry_without_sandbox(cls, reason: str, *, index: int = 1) -> "SandboxAttempt":
        return cls(
            kind=SandboxAttemptKind.RETRY,
            index=index,
            selection=SandboxAttemptSelection.NO_SANDBOX,
            sandbox_permissions=SandboxPermissions.REQUIRE_ESCALATED,
            reason=reason,
        )

    @classmethod
    def escalated(cls, reason: str, *, index: int = 1) -> "SandboxAttempt":
        """Compatibility alias for #121 callers; retry semantics are now explicit."""

        return cls.retry_without_sandbox(reason, index=index)

    def to_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind.value,
            "index": self.index,
            "selection": self.selection.value,
            "sandbox_permissions": self.sandbox_permissions.value,
            "additional_permissions": (
                self.additional_permissions.canonical()
                if self.additional_permissions is not None
                else None
            ),
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


class AttemptAwareSandboxManager(SandboxManager):
    """Transparent SandboxManager wrapper with a per-attempt planning override."""

    def __init__(self, base: SandboxManager) -> None:
        if isinstance(base, AttemptAwareSandboxManager):
            base = base.base
        if not isinstance(base, SandboxManager):
            raise TypeError("base sandbox manager must be SandboxManager")
        object.__setattr__(self, "base", base)

    def __getattr__(self, name: str):
        return getattr(self.base, name)

    def __setattr__(self, name: str, value) -> None:
        if name == "base" or "base" not in self.__dict__:
            object.__setattr__(self, name, value)
            return
        setattr(self.base, name, value)

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
        additional_permissions: AdditionalPermissionProfile | None = None,
    ) -> SandboxCommand:
        attempt = current_sandbox_attempt()
        if attempt.selection in {
            SandboxAttemptSelection.POLICY,
            SandboxAttemptSelection.ADDITIONAL_PERMISSIONS,
        }:
            overlay = (
                attempt.additional_permissions
                if attempt.selection is SandboxAttemptSelection.ADDITIONAL_PERMISSIONS
                else additional_permissions
            )
            return self.base.prepare(
                argv=argv,
                cwd=cwd,
                workspace=workspace,
                permissions=permissions,
                permission_mode=permission_mode,
                environment=environment,
                additional_permissions=overlay,
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
                "sandbox bypass cannot override SandboxPolicy.REQUIRED",
                escalatable=False,
            )

        detail = f"Explicit attempt {attempt.index} bypassed OS sandbox containment."
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
