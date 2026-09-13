from __future__ import annotations

from dataclasses import dataclass, field

from .contracts import PermissionMode
from .permissions import (
    ApprovalPolicy,
    PermissionProfile,
    PermissionSnapshot,
    permission_snapshot,
)
from .sandbox import SandboxSnapshot
from .tools import ToolRouter
from .shell_environment import ShellEnvironmentPolicy, get_default_environment_policy


@dataclass(frozen=True, slots=True)
class WorldStateSnapshot:
    workspace_dir: str
    profile_id: str
    permission_mode: PermissionMode
    tool_names: tuple[str, ...]
    sandbox: SandboxSnapshot | None = None


@dataclass(frozen=True, slots=True)
class StepContext:
    """Immutable request-scoped state captured for one model sampling step."""

    step_id: str
    session_id: str
    turn_id: str
    model_step: int
    world_state: WorldStateSnapshot
    permissions: PermissionSnapshot
    tool_router: ToolRouter
    environment_policy: ShellEnvironmentPolicy = field(default_factory=get_default_environment_policy)

    @property
    def permission_profile(self) -> PermissionProfile:
        """Compatibility view; the snapshot is the authoritative source."""

        return self.permissions.profile

    @property
    def approval_policy(self) -> ApprovalPolicy:
        """Compatibility view; the snapshot is the authoritative source."""

        return self.permissions.approval_policy

    @classmethod
    def build(
        cls,
        *,
        step_id: str,
        session_id: str,
        turn_id: str,
        model_step: int,
        workspace_dir: str,
        profile_id: str,
        permission_mode: PermissionMode | str,
        tool_router: ToolRouter,
        sandbox_snapshot: SandboxSnapshot | None = None,
        permissions: PermissionSnapshot | None = None,
    ) -> "StepContext":
        resolved_permissions = permission_snapshot(permissions or permission_mode)
        if PermissionMode(permission_mode) is not resolved_permissions.mode:
            raise ValueError("step permission_mode does not match the supplied permission snapshot")
        world_state = WorldStateSnapshot(
            workspace_dir=str(workspace_dir),
            profile_id=str(profile_id),
            permission_mode=resolved_permissions.mode,
            tool_names=tuple(tool.name for tool in tool_router.all()),
            sandbox=sandbox_snapshot,
        )
        return cls(
            step_id=str(step_id),
            session_id=str(session_id),
            turn_id=str(turn_id),
            model_step=max(0, int(model_step)),
            world_state=world_state,
            permissions=resolved_permissions,
            tool_router=tool_router,
        )


__all__ = ["StepContext", "WorldStateSnapshot"]
