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
class PermissionPreset:
    mode: PermissionMode
    profile: PermissionProfile
    approval_policy: ApprovalPolicy


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


def permission_preset(mode: PermissionMode | str) -> PermissionPreset:
    resolved = PermissionMode(mode)
    if resolved is PermissionMode.READ_ONLY:
        return PermissionPreset(resolved, _READ_ONLY_PROFILE, ApprovalPolicy.NEVER)
    if resolved is PermissionMode.APPROVAL:
        return PermissionPreset(resolved, _READ_ONLY_PROFILE, ApprovalPolicy.ON_REQUEST)
    if resolved is PermissionMode.WORKSPACE:
        return PermissionPreset(resolved, _WORKSPACE_PROFILE, ApprovalPolicy.ON_REQUEST)
    return PermissionPreset(resolved, _FULL_ACCESS_PROFILE, ApprovalPolicy.NEVER)


@dataclass(frozen=True, slots=True)
class PermissionEvaluation:
    decision: PermissionDecision
    reason: str


class PermissionEngine:
    def evaluate(
        self,
        *,
        effect: ToolEffect,
        profile: PermissionProfile,
        approval_policy: ApprovalPolicy,
    ) -> PermissionEvaluation:
        resolved_effect = ToolEffect(effect)
        if profile.allows(resolved_effect):
            return PermissionEvaluation(
                PermissionDecision.ALLOW,
                f"Permission profile {profile.name} allows {resolved_effect.value} tools.",
            )
        if approval_policy is ApprovalPolicy.ON_REQUEST:
            return PermissionEvaluation(
                PermissionDecision.APPROVAL,
                f"Permission profile {profile.name} does not grant {resolved_effect.value}; user approval can elevate this call.",
            )
        return PermissionEvaluation(
            PermissionDecision.DENY,
            f"Permission profile {profile.name} denies {resolved_effect.value} and approval policy is {approval_policy.value}.",
        )


__all__ = [
    "ApprovalPolicy",
    "PermissionDecision",
    "PermissionEngine",
    "PermissionEvaluation",
    "PermissionPreset",
    "PermissionProfile",
    "permission_preset",
]
