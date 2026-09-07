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

    def evaluate_tool(
        self,
        step: StepContext,
        tool: AgentTool,
        *,
        legacy_policy: ToolPolicy | None = None,
    ) -> tuple[PermissionDecision, str]:
        """Return the exact authorization decision used by real execution.

        The frozen StepContext permission snapshot is authoritative. The model
        does not need to predict this decision before it emits a tool call;
        Loom's runtime owns allow/approval/deny enforcement.
        """

        evaluation = self.permission_engine.evaluate(
            effect=tool.effect,
            snapshot=step.permissions,
        )
        decision = evaluation.decision
        reason = evaluation.reason

        if (
            step.permissions.mode is PermissionMode.APPROVAL
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
        return decision, reason

    def capability_contract(
        self,
        step: StepContext,
        *,
        legacy_policy: ToolPolicy | None = None,
    ) -> str:
        """Return model-facing tool-harness rules without leaking permission policy.

        This method keeps its historical name for compatibility. Earlier Loom
        versions injected a per-step capability matrix containing tool names,
        permission decisions, sandbox state, and hand-written intent routes.
        That made the model perform a second, fallible authorization pass before
        calling tools. Codex instead treats the finalized tool plan as the
        model-visible capability surface and keeps authorization in the runtime.

        The current contract is therefore intentionally invariant across
        permission profiles for the same model/tool request. ``step`` and
        ``legacy_policy`` remain parameters only so callers do not need a
        migration in the same release.
        """

        del step, legacy_policy
        return "\n".join(
            (
                "<loom_tool_harness>",
                "The tool definitions attached to this model request are the authoritative capability surface for this step.",
                "Choose tools from their semantic descriptions and schemas. A general-purpose tool may satisfy a request even when no specialist tool has a matching name.",
                "When a suitable tool exists, issue the tool call directly. Do not ask the user to pre-authorize it in prose; Loom's runtime will allow it, request approval, or deny it according to the active policy.",
                "A tool status or failure is scoped to that tool or subsystem. Do not infer that unrelated tools or subsystems are unavailable from one disabled status, sandbox report, denial, or execution failure.",
                "If tool_search is exposed and no direct tool is suitable, use it before concluding that the requested capability is unavailable.",
                "Only claim that Loom cannot perform a requested action after the exposed/deferred tool surface and actual runtime results provide that evidence.",
                "</loom_tool_harness>",
            )
        )

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

        decision, reason = self.evaluate_tool(
            step,
            tool,
            legacy_policy=legacy_policy,
        )

        return PreparedToolCall(
            call=call,
            tool=tool,
            decision=decision,
            reason=reason,
        )


__all__ = ["PreparedToolCall", "ToolOrchestrator"]
