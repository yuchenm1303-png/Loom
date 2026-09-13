from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from app.ai import ReasoningRequest

from .context_limits import ResolvedContextLimits
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

if TYPE_CHECKING:
    from .mcp_runtime import McpBinding


@dataclass(frozen=True, slots=True)
class WorldStateSnapshot:
    workspace_dir: str
    profile_id: str
    permission_mode: PermissionMode
    tool_names: tuple[str, ...]
    sandbox: SandboxSnapshot | None = None


@dataclass(frozen=True, slots=True)
class RequestStateSnapshot:
    """Model-visible and integrity metadata frozen for one sampling step.

    Model metadata is provider-safe output from ``ModelProfile.as_safe_dict``.
    ``mcp_binding_json`` is a secret-free diagnostic/integrity projection only;
    exact MCP execution authority lives on ``StepContext.mcp_binding``.
    """

    captured: bool = False
    system_prompt: str = ""
    project_instructions: str = ""
    communication_language: str = "auto"
    model_profile_json: str = ""
    mcp_binding_json: str = ""
    context_limits: ResolvedContextLimits | None = None

    def __post_init__(self) -> None:
        language = str(self.communication_language or "auto").strip().casefold() or "auto"
        object.__setattr__(self, "captured", bool(self.captured))
        object.__setattr__(self, "system_prompt", str(self.system_prompt or ""))
        object.__setattr__(self, "project_instructions", str(self.project_instructions or ""))
        object.__setattr__(self, "communication_language", language)
        object.__setattr__(self, "model_profile_json", str(self.model_profile_json or ""))
        object.__setattr__(self, "mcp_binding_json", str(self.mcp_binding_json or ""))
        if self.context_limits is not None and not isinstance(self.context_limits, ResolvedContextLimits):
            raise TypeError("context_limits must be ResolvedContextLimits or None")

    @classmethod
    def build(
        cls,
        *,
        system_prompt: str = "",
        project_instructions: str = "",
        communication_language: str = "auto",
        model_profile: dict[str, object] | None = None,
        mcp_binding: dict[str, object] | None = None,
        context_limits: ResolvedContextLimits | None = None,
    ) -> "RequestStateSnapshot":
        profile_json = ""
        if model_profile:
            profile_json = json.dumps(
                model_profile,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
        mcp_json = ""
        if mcp_binding:
            mcp_json = json.dumps(
                mcp_binding,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
        return cls(
            captured=True,
            system_prompt=system_prompt,
            project_instructions=project_instructions,
            communication_language=communication_language,
            model_profile_json=profile_json,
            mcp_binding_json=mcp_json,
            context_limits=context_limits,
        )

    def digest(self) -> str:
        payload = {
            "captured": self.captured,
            "system_prompt": self.system_prompt,
            "project_instructions": self.project_instructions,
            "communication_language": self.communication_language,
            "model_profile_json": self.model_profile_json,
            "mcp_binding_json": self.mcp_binding_json,
            "context_limits": self.context_limits.as_dict() if self.context_limits else None,
        }
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()


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
    request_state: RequestStateSnapshot = field(default_factory=RequestStateSnapshot)
    reasoning: ReasoningRequest | None = None
    environment_policy: ShellEnvironmentPolicy = field(default_factory=get_default_environment_policy)
    mcp_binding: McpBinding | None = None

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
        request_state: RequestStateSnapshot | None = None,
        reasoning: ReasoningRequest | None = None,
        mcp_binding: McpBinding | None = None,
    ) -> "StepContext":
        resolved_permissions = permission_snapshot(permissions or permission_mode)
        if PermissionMode(permission_mode) is not resolved_permissions.mode:
            raise ValueError("step permission_mode does not match the supplied permission snapshot")
        if reasoning is not None and not isinstance(reasoning, ReasoningRequest):
            raise TypeError("reasoning must be ReasoningRequest or None")
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
            request_state=request_state or RequestStateSnapshot(),
            reasoning=reasoning,
            mcp_binding=mcp_binding,
        )


__all__ = ["RequestStateSnapshot", "StepContext", "WorldStateSnapshot"]
