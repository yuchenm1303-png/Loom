from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .contracts import PermissionMode, ToolEffect


class ApprovalPolicy(str, Enum):
    NEVER = "never"
    ON_REQUEST = "on-request"


class PermissionDecision(str, Enum):
    ALLOW = "allow"
    APPROVAL = "approval"
    DENY = "deny"


class FileSystemAccess(str, Enum):
    """Selected filesystem containment for process execution.

    This is deliberately independent from tool authorization. For example, the
    compatibility ``approval`` mode starts from read-only tool authorization but
    approved exec calls still run inside the writable-workspace sandbox. Keeping
    both dimensions in one immutable snapshot makes that distinction explicit.
    """

    READ_ONLY = "read-only"
    WORKSPACE_WRITE = "workspace-write"
    UNRESTRICTED = "unrestricted"


@dataclass(frozen=True, slots=True)
class PermissionProfile:
    name: str
    allowed_effects: frozenset[ToolEffect]

    def __post_init__(self) -> None:
        name = str(self.name or "").strip()
        if not name:
            raise ValueError("permission profile name must not be empty")
        object.__setattr__(self, "name", name)
        object.__setattr__(
            self,
            "allowed_effects",
            frozenset(ToolEffect(effect) for effect in self.allowed_effects),
        )

    def allows(self, effect: ToolEffect) -> bool:
        return ToolEffect(effect) in self.allowed_effects


@dataclass(frozen=True, slots=True)
class PermissionSnapshot:
    """One resolved permission version captured for an Agent step.

    Inspired by Codex's PermissionProfileSnapshot/ResolvedStepSettings boundary:
    authorization policy and execution containment are selected once, then
    consumers read this immutable value instead of interpreting PermissionMode
    independently.
    """

    mode: PermissionMode
    profile: PermissionProfile
    approval_policy: ApprovalPolicy
    file_system_access: FileSystemAccess

    def __post_init__(self) -> None:
        object.__setattr__(self, "mode", PermissionMode(self.mode))
        object.__setattr__(self, "approval_policy", ApprovalPolicy(self.approval_policy))
        object.__setattr__(self, "file_system_access", FileSystemAccess(self.file_system_access))
        if not isinstance(self.profile, PermissionProfile):
            raise TypeError("permission snapshot profile must be PermissionProfile")


# Backward-compatible public name used by existing embedders/tests. A preset is
# now the canonical resolved snapshot rather than a second structure.
PermissionPreset = PermissionSnapshot


_READ_ONLY_PROFILE = PermissionProfile(
    name="read-only",
    allowed_effects=frozenset({ToolEffect.READ_ONLY}),
)
_WORKSPACE_PROFILE = PermissionProfile(
    name="workspace-write",
    allowed_effects=frozenset({ToolEffect.READ_ONLY, ToolEffect.MUTATING}),
)
_FULL_ACCESS_PROFILE = PermissionProfile(
    name="unrestricted",
    allowed_effects=frozenset(ToolEffect),
)


_PERMISSION_SNAPSHOTS: dict[PermissionMode, PermissionSnapshot] = {
    PermissionMode.READ_ONLY: PermissionSnapshot(
        mode=PermissionMode.READ_ONLY,
        profile=_READ_ONLY_PROFILE,
        approval_policy=ApprovalPolicy.NEVER,
        file_system_access=FileSystemAccess.READ_ONLY,
    ),
    PermissionMode.APPROVAL: PermissionSnapshot(
        mode=PermissionMode.APPROVAL,
        profile=_READ_ONLY_PROFILE,
        approval_policy=ApprovalPolicy.ON_REQUEST,
        file_system_access=FileSystemAccess.WORKSPACE_WRITE,
    ),
    PermissionMode.WORKSPACE: PermissionSnapshot(
        mode=PermissionMode.WORKSPACE,
        profile=_WORKSPACE_PROFILE,
        approval_policy=ApprovalPolicy.ON_REQUEST,
        file_system_access=FileSystemAccess.WORKSPACE_WRITE,
    ),
    PermissionMode.FULL_ACCESS: PermissionSnapshot(
        mode=PermissionMode.FULL_ACCESS,
        profile=_FULL_ACCESS_PROFILE,
        approval_policy=ApprovalPolicy.NEVER,
        file_system_access=FileSystemAccess.UNRESTRICTED,
    ),
}


def permission_snapshot(value: PermissionSnapshot | PermissionMode | str) -> PermissionSnapshot:
    """Resolve one canonical immutable permission snapshot.

    Passing an existing snapshot is identity-preserving. This lets a frozen
    StepContext hand the exact same resolved policy to downstream consumers.
    """

    if isinstance(value, PermissionSnapshot):
        return value
    return _PERMISSION_SNAPSHOTS[PermissionMode(value)]


def permission_preset(mode: PermissionMode | str) -> PermissionPreset:
    """Compatibility alias for callers using the pre-snapshot API."""

    return permission_snapshot(mode)


@dataclass(frozen=True, slots=True)
class PermissionEvaluation:
    decision: PermissionDecision
    reason: str


class PermissionEngine:
    def evaluate(
        self,
        *,
        effect: ToolEffect,
        snapshot: PermissionSnapshot | None = None,
        profile: PermissionProfile | None = None,
        approval_policy: ApprovalPolicy | None = None,
    ) -> PermissionEvaluation:
        """Evaluate a tool effect from one snapshot or legacy split arguments."""

        if snapshot is not None:
            resolved = permission_snapshot(snapshot)
            if profile is not None or approval_policy is not None:
                raise ValueError("pass snapshot or profile/approval_policy, not both")
            profile = resolved.profile
            approval_policy = resolved.approval_policy
        elif profile is None or approval_policy is None:
            raise ValueError("permission evaluation requires a snapshot or profile and approval_policy")

        resolved_effect = ToolEffect(effect)
        resolved_policy = ApprovalPolicy(approval_policy)
        if profile.allows(resolved_effect):
            return PermissionEvaluation(
                PermissionDecision.ALLOW,
                f"Permission profile {profile.name} allows {resolved_effect.value} tools.",
            )
        if resolved_policy is ApprovalPolicy.ON_REQUEST:
            return PermissionEvaluation(
                PermissionDecision.APPROVAL,
                f"Permission profile {profile.name} does not grant {resolved_effect.value}; user approval can elevate this call.",
            )
        return PermissionEvaluation(
            PermissionDecision.DENY,
            f"Permission profile {profile.name} denies {resolved_effect.value} and approval policy is {resolved_policy.value}.",
        )


__all__ = [
    "ApprovalPolicy",
    "FileSystemAccess",
    "PermissionDecision",
    "PermissionEngine",
    "PermissionEvaluation",
    "PermissionPreset",
    "PermissionProfile",
    "PermissionSnapshot",
    "permission_preset",
    "permission_snapshot",
]
