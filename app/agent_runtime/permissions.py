from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Mapping

from .contracts import PermissionMode, ToolEffect


class ApprovalPolicy(str, Enum):
    NEVER = "never"
    ON_REQUEST = "on-request"
    UNLESS_TRUSTED = "unless-trusted"
    GRANULAR = "granular"


@dataclass(frozen=True, slots=True)
class GranularApprovalConfig:
    sandbox_approval: bool = True
    rules: bool = True


class PermissionDecision(str, Enum):
    ALLOW = "allow"
    APPROVAL = "approval"
    DENY = "deny"


class SandboxPermissions(str, Enum):
    """Per-command sandbox override, matching Codex's observable contract."""

    USE_DEFAULT = "use_default"
    REQUIRE_ESCALATED = "require_escalated"
    WITH_ADDITIONAL_PERMISSIONS = "with_additional_permissions"

    @property
    def requires_escalated_permissions(self) -> bool:
        return self is SandboxPermissions.REQUIRE_ESCALATED

    @property
    def requests_sandbox_override(self) -> bool:
        return self is not SandboxPermissions.USE_DEFAULT

    @property
    def uses_additional_permissions(self) -> bool:
        return self is SandboxPermissions.WITH_ADDITIONAL_PERMISSIONS


@dataclass(frozen=True, slots=True)
class AdditionalPermissionProfile:
    """Command-scoped permission overlay.

    Loom currently supports the Codex subset it can enforce locally: network
    enablement plus filesystem read/write path grants. Paths remain explicit so
    approval/cache identity can bind the exact requested authority.
    """

    network_enabled: bool | None = None
    file_system_read: tuple[str, ...] = ()
    file_system_write: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.network_enabled is not None:
            object.__setattr__(self, "network_enabled", bool(self.network_enabled))
        object.__setattr__(
            self,
            "file_system_read",
            tuple(str(value) for value in self.file_system_read),
        )
        object.__setattr__(
            self,
            "file_system_write",
            tuple(str(value) for value in self.file_system_write),
        )

    @property
    def empty(self) -> bool:
        return (
            self.network_enabled is None
            and not self.file_system_read
            and not self.file_system_write
        )

    def canonical(self) -> dict[str, object]:
        payload: dict[str, object] = {}
        if self.network_enabled is not None:
            payload["network"] = {"enabled": self.network_enabled}
        if self.file_system_read or self.file_system_write:
            file_system: dict[str, object] = {}
            if self.file_system_read:
                file_system["read"] = list(self.file_system_read)
            if self.file_system_write:
                file_system["write"] = list(self.file_system_write)
            payload["file_system"] = file_system
        return payload

    def resolved(self, *, cwd: str | Path) -> "AdditionalPermissionProfile":
        root = Path(cwd).expanduser().resolve()

        def normalize(values: tuple[str, ...]) -> tuple[str, ...]:
            output: list[str] = []
            for raw in values:
                path = Path(raw).expanduser()
                if not path.is_absolute():
                    path = root / path
                value = str(path.resolve())
                if value not in output:
                    output.append(value)
            return tuple(output)

        return AdditionalPermissionProfile(
            network_enabled=self.network_enabled,
            file_system_read=normalize(self.file_system_read),
            file_system_write=normalize(self.file_system_write),
        )

    @classmethod
    def from_mapping(cls, raw: object) -> "AdditionalPermissionProfile":
        if raw is None:
            return cls()
        if not isinstance(raw, Mapping):
            raise ValueError("additional_permissions must be an object")
        extras = set(raw) - {"network", "file_system"}
        if extras:
            raise ValueError(
                "additional_permissions contains unsupported fields: "
                + ", ".join(sorted(str(value) for value in extras))
            )
        network_enabled: bool | None = None
        network = raw.get("network")
        if network is not None:
            if not isinstance(network, Mapping) or set(network) - {"enabled"}:
                raise ValueError("additional_permissions.network only supports enabled")
            if "enabled" in network and not isinstance(network["enabled"], bool):
                raise ValueError("additional_permissions.network.enabled must be a boolean")
            network_enabled = network.get("enabled")

        read: tuple[str, ...] = ()
        write: tuple[str, ...] = ()
        file_system = raw.get("file_system")
        if file_system is not None:
            if not isinstance(file_system, Mapping) or set(file_system) - {"read", "write"}:
                raise ValueError("additional_permissions.file_system only supports read/write")

            def paths(name: str) -> tuple[str, ...]:
                value = file_system.get(name, [])
                if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
                    raise ValueError(
                        f"additional_permissions.file_system.{name} must be an array of strings"
                    )
                return tuple(value)

            read = paths("read")
            write = paths("write")
        return cls(
            network_enabled=network_enabled,
            file_system_read=read,
            file_system_write=write,
        )


class ExecApprovalRequirementKind(str, Enum):
    SKIP = "skip"
    NEEDS_APPROVAL = "needs_approval"
    FORBIDDEN = "forbidden"


@dataclass(frozen=True, slots=True)
class ExecApprovalRequirement:
    kind: ExecApprovalRequirementKind
    reason: str = ""
    bypass_sandbox: bool = False
    proposed_prefix_rule: tuple[str, ...] = ()

    @property
    def needs_approval(self) -> bool:
        return self.kind is ExecApprovalRequirementKind.NEEDS_APPROVAL

    @property
    def forbidden(self) -> bool:
        return self.kind is ExecApprovalRequirementKind.FORBIDDEN


class FileSystemAccess(str, Enum):
    """Selected filesystem containment for process execution."""

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
    """One resolved permission version captured for an Agent step."""

    mode: PermissionMode
    profile: PermissionProfile
    approval_policy: ApprovalPolicy
    file_system_access: FileSystemAccess
    granular_approval: GranularApprovalConfig | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "mode", PermissionMode(self.mode))
        object.__setattr__(self, "approval_policy", ApprovalPolicy(self.approval_policy))
        object.__setattr__(self, "file_system_access", FileSystemAccess(self.file_system_access))
        if not isinstance(self.profile, PermissionProfile):
            raise TypeError("permission snapshot profile must be PermissionProfile")
        if self.approval_policy is ApprovalPolicy.GRANULAR and self.granular_approval is None:
            object.__setattr__(self, "granular_approval", GranularApprovalConfig())
        if self.granular_approval is not None and not isinstance(
            self.granular_approval, GranularApprovalConfig
        ):
            raise TypeError("granular_approval must be GranularApprovalConfig or None")


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
    # Loom's product-level "approval" preset means unmatched commands should
    # actually ask. Codex represents that behavior as `unless-trusted`; keeping
    # `on-request` here would make ordinary sandboxed exec calls silently skip
    # the prompt once exec starts using Codex's dedicated requirement logic.
    PermissionMode.APPROVAL: PermissionSnapshot(
        mode=PermissionMode.APPROVAL,
        profile=_READ_ONLY_PROFILE,
        approval_policy=ApprovalPolicy.UNLESS_TRUSTED,
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
    if isinstance(value, PermissionSnapshot):
        return value
    return _PERMISSION_SNAPSHOTS[PermissionMode(value)]


def permission_preset(mode: PermissionMode | str) -> PermissionPreset:
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
        if resolved_policy in {
            ApprovalPolicy.ON_REQUEST,
            ApprovalPolicy.UNLESS_TRUSTED,
            ApprovalPolicy.GRANULAR,
        }:
            return PermissionEvaluation(
                PermissionDecision.APPROVAL,
                f"Permission profile {profile.name} does not grant {resolved_effect.value}; user approval can elevate this call.",
            )
        return PermissionEvaluation(
            PermissionDecision.DENY,
            f"Permission profile {profile.name} denies {resolved_effect.value} and approval policy is {resolved_policy.value}.",
        )

    def exec_requirement(
        self,
        *,
        snapshot: PermissionSnapshot,
        sandbox_permissions: SandboxPermissions,
        dangerous: bool = False,
        explicit_policy_allow: bool = False,
        proposed_prefix_rule: tuple[str, ...] = (),
    ) -> ExecApprovalRequirement:
        """Codex-style unmatched exec approval requirement for Loom's supported subset.

        Loom does not yet ship Codex's command danger classifier or persisted
        exec-policy engine. Callers can supply those facts when available; the
        default path mirrors Codex for ordinary unmatched direct-argv commands.
        """

        resolved = permission_snapshot(snapshot)
        policy = resolved.approval_policy
        requested = SandboxPermissions(sandbox_permissions)

        if explicit_policy_allow:
            return ExecApprovalRequirement(
                ExecApprovalRequirementKind.SKIP,
                bypass_sandbox=True,
                proposed_prefix_rule=proposed_prefix_rule,
            )

        if dangerous:
            if policy is ApprovalPolicy.NEVER:
                return ExecApprovalRequirement(
                    ExecApprovalRequirementKind.FORBIDDEN,
                    "approval required for a dangerous command, but approval policy is never",
                )
            return ExecApprovalRequirement(
                ExecApprovalRequirementKind.NEEDS_APPROVAL,
                "command requires approval because it is classified as dangerous",
                proposed_prefix_rule=proposed_prefix_rule,
            )

        needs_prompt = False
        if policy is ApprovalPolicy.UNLESS_TRUSTED:
            needs_prompt = True
        elif policy in {ApprovalPolicy.ON_REQUEST, ApprovalPolicy.GRANULAR}:
            needs_prompt = (
                resolved.file_system_access is not FileSystemAccess.UNRESTRICTED
                and requested.requests_sandbox_override
            )

        if needs_prompt:
            if (
                policy is ApprovalPolicy.GRANULAR
                and resolved.granular_approval is not None
                and not resolved.granular_approval.sandbox_approval
            ):
                return ExecApprovalRequirement(
                    ExecApprovalRequirementKind.FORBIDDEN,
                    "approval required by sandbox override, but granular sandbox approval is disabled",
                )
            return ExecApprovalRequirement(
                ExecApprovalRequirementKind.NEEDS_APPROVAL,
                "command requests permissions beyond the active sandbox profile"
                if requested.requests_sandbox_override
                else "unless-trusted policy requires approval for unmatched commands",
                proposed_prefix_rule=proposed_prefix_rule,
            )

        return ExecApprovalRequirement(
            ExecApprovalRequirementKind.SKIP,
            bypass_sandbox=False,
            proposed_prefix_rule=proposed_prefix_rule,
        )


__all__ = [
    "AdditionalPermissionProfile",
    "ApprovalPolicy",
    "ExecApprovalRequirement",
    "ExecApprovalRequirementKind",
    "FileSystemAccess",
    "GranularApprovalConfig",
    "PermissionDecision",
    "PermissionEngine",
    "PermissionEvaluation",
    "PermissionPreset",
    "PermissionProfile",
    "PermissionSnapshot",
    "SandboxPermissions",
    "permission_preset",
    "permission_snapshot",
]
