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
        """Return the same authorization decision used by real tool execution.

        Model-facing capability grounding must never maintain a second,
        approximate permission model. Keeping this logic here lets the runtime
        explain allow/approval/deny state using the exact execution boundary.
        """

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
        return decision, reason

    def capability_contract(
        self,
        step: StepContext,
        *,
        legacy_policy: ToolPolicy | None = None,
    ) -> str:
        """Build a compact, authoritative capability contract for one model step.

        Tool schemas remain the semantic source of truth. This block only tells
        the model how those real tools relate to current authorization and
        sandbox state, preventing it from inventing capability restrictions from
        tool names, missing specialist tools, or generic safety assumptions.
        """

        grouped: dict[PermissionDecision, list[str]] = {
            PermissionDecision.ALLOW: [],
            PermissionDecision.APPROVAL: [],
            PermissionDecision.DENY: [],
        }
        for tool in step.tool_router.all():
            decision, _ = self.evaluate_tool(step, tool, legacy_policy=legacy_policy)
            grouped[decision].append(f"{tool.name}[{tool.effect.value}]")

        def names(decision: PermissionDecision) -> str:
            values = grouped[decision]
            return ", ".join(values) if values else "(none)"

        sandbox = step.world_state.sandbox
        if sandbox is None:
            sandbox_line = "sandbox=not-reported"
        else:
            sandbox_line = (
                f"sandbox={sandbox.backend.value}:{sandbox.mode.value}; "
                f"enforced={str(sandbox.enforced).lower()}; scope=process-execution"
            )

        return "\n".join(
            (
                "<loom_capability_contract>",
                "This block is generated from the current Loom runtime state and is authoritative for capability claims.",
                f"permission_mode={step.world_state.permission_mode.value}",
                sandbox_line,
                f"allow={names(PermissionDecision.ALLOW)}",
                f"approval={names(PermissionDecision.APPROVAL)}",
                f"deny={names(PermissionDecision.DENY)}",
                "Rules:",
                "1. Tool names are not a capability ontology. Match the user's intent to tool semantics and schemas; a general-purpose tool may satisfy a request even when no specialist tool has a matching name.",
                "2. Authorization is not capability. A tool listed under approval is available to attempt; call it when appropriate and let the harness request user approval. A tool under deny is blocked by the current permission mode, not evidence that Loom never supports that capability.",
                "3. Before claiming that Loom cannot access, inspect, control, read, write, browse, execute, or observe something, check all suitable exposed tools. If tool_search is available, use it to discover deferred tools before declaring that no matching capability exists.",
                "4. Do not infer host, OS, network, filesystem, process, browser, or GUI inaccessibility merely because access is tool-mediated or because sandboxing exists. Sandbox state describes process-execution isolation only unless a tool result explicitly states a broader limitation.",
                "5. Disambiguate user vocabulary from Loom subsystem names by context. Do not map a user noun to a same-named internal subsystem unless the request actually refers to that subsystem.",
                "6. Say a capability is unavailable only from runtime evidence: no suitable exposed/deferred tool exists, a relevant status/tool result reports it unavailable, or permission/runtime policy explicitly blocks it with no usable alternative. Distinguish unavailable, approval-required, denied, and execution-failed.",
                "</loom_capability_contract>",
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
