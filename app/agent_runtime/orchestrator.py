from __future__ import annotations

from dataclasses import dataclass

from app.ai import ToolCall

from .contracts import PermissionMode
from .permissions import PermissionDecision, PermissionEngine
from .step import StepContext
from .tools import AgentTool, ToolContext, ToolPolicy, ToolResult, validate_tool_arguments


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
        evaluation = self.permission_engine.evaluate(
            effect=tool.effect,
            snapshot=step.permissions,
        )
        decision = evaluation.decision
        reason = evaluation.reason

        if step.permissions.mode is PermissionMode.APPROVAL and legacy_policy is not None:
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

    def execute(
        self,
        prepared: PreparedToolCall,
        context: ToolContext,
        *,
        approval_granted: bool = False,
    ) -> ToolResult:
        if prepared.decision is PermissionDecision.DENY:
            raise RuntimeError("denied tool reached execution")
        if prepared.decision is PermissionDecision.APPROVAL and not approval_granted:
            raise RuntimeError("tool execution requires approval")

        try:
            result = prepared.tool.handler(context, prepared.call.arguments)
            if not isinstance(result, ToolResult):
                raise TypeError("agent tool handler must return ToolResult")
            return result
        except Exception as exc:
            return ToolResult(ok=False, content=f"{type(exc).__name__}: {exc}")


__all__ = ["PreparedToolCall", "ToolOrchestrator"]
