from __future__ import annotations

from dataclasses import dataclass

from app.ai import ToolCall

from .contracts import PermissionMode
from .permissions import PermissionDecision, PermissionEngine
from .step import StepContext
from .tools import AgentTool, ToolPolicy, validate_tool_arguments


@dataclass(frozen=True, slots=True)
class PreparedToolCall:
    call: ToolCall
    tool: AgentTool
    decision: PermissionDecision
    reason: str


class ToolOrchestrator:
    """Central preparation boundary for every model-originated tool call."""

    def __init__(self, *, permission_engine: PermissionEngine | None = None) -> None:
        self.permission_engine = permission_engine or PermissionEngine()

    def prepare(
        self,
        step: StepContext,
        call: ToolCall,
        *,
        legacy_policy: ToolPolicy | None = None,
    ) -> PreparedToolCall:
        tool = step.tool_router.get(call.name)
        if tool is None:
            raise ValueError(f"Unknown or unavailable tool: {call.name}")
        validate_tool_arguments(tool.input_schema, call.arguments)

        evaluation = self.permission_engine.evaluate(
            effect=tool.effect,
            profile=step.permission_profile,
            approval_policy=step.approval_policy,
        )
        decision = evaluation.decision
        reason = evaluation.reason

        if (
            step.world_state.permission_mode is PermissionMode.APPROVAL
            and legacy_policy is not None
        ):
            decision = (
                PermissionDecision.APPROVAL
                if legacy_policy.requires_approval(tool)
                else PermissionDecision.ALLOW
            )
            reason = (
                f"Compatibility approval policy requires approval for {tool.effect.value}."
                if decision is PermissionDecision.APPROVAL
                else f"Compatibility approval policy auto-approves {tool.effect.value}."
            )

        return PreparedToolCall(
            call=call,
            tool=tool,
            decision=decision,
            reason=reason,
        )


__all__ = ["PreparedToolCall", "ToolOrchestrator"]
