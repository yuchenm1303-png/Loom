from __future__ import annotations

from dataclasses import dataclass

from .contracts import PermissionMode
from .permissions import ApprovalPolicy, PermissionProfile, permission_preset
from .sandbox import SandboxSnapshot
from .tools import ToolRouter


@dataclass(frozen=True, slots=True)
class WorldStateSnapshot:
    workspace_dir: str
    profile_id: str
    permission_mode: PermissionMode
    tool_names: tuple[str, ...]
    sandbox: SandboxSnapshot | None = None


@dataclass(frozen=True, slots=True)
class StepContext:
    step_id: str
    session_id: str
    turn_id: str
    model_step: int
    world_state: WorldStateSnapshot
    permission_profile: PermissionProfile
    approval_policy: ApprovalPolicy
    tool_router: ToolRouter

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
    ) -> "StepContext":
        preset = permission_preset(permission_mode)
        world_state = WorldStateSnapshot(
            workspace_dir=str(workspace_dir),
            profile_id=str(profile_id),
            permission_mode=preset.mode,
            tool_names=tuple(tool.name for tool in tool_router.all()),
            sandbox=sandbox_snapshot,
        )
        return cls(
            step_id=str(step_id),
            session_id=str(session_id),
            turn_id=str(turn_id),
            model_step=max(0, int(model_step)),
            world_state=world_state,
            permission_profile=preset.profile,
            approval_policy=preset.approval_policy,
            tool_router=tool_router,
        )


__all__ = ["StepContext", "WorldStateSnapshot"]
