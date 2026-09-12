from __future__ import annotations

from dataclasses import dataclass, replace

from app.ai import ToolCall

from .contracts import PermissionMode, ToolEffect
from .exec_policy import ExecPolicy, ExecPolicyEvaluation
from .network_policy import NetworkPolicy
from .permissions import PermissionDecision, PermissionEngine
from .step import StepContext
from .tools import AgentTool, ToolPolicy, validate_tool_arguments


_DECISION_SEVERITY = {
    PermissionDecision.ALLOW: 0,
    PermissionDecision.APPROVAL: 1,
    PermissionDecision.DENY: 2,
}


@dataclass(frozen=True, slots=True)
class PreparedToolCall:
    call: ToolCall
    tool: AgentTool
    decision: PermissionDecision
    reason: str
    effective_effect: ToolEffect
    network_requested: bool = False
    matched_exec_rule: str = ""


class ToolOrchestrator:
    """Central preparation boundary for every model-originated tool call."""

    def __init__(
        self,
        *,
        permission_engine: PermissionEngine | None = None,
        exec_policy: ExecPolicy | None = None,
        network_policy: NetworkPolicy | None = None,
    ) -> None:
        self.permission_engine = permission_engine or PermissionEngine()
        self.exec_policy = exec_policy or ExecPolicy()
        self.network_policy = network_policy or NetworkPolicy()

    @staticmethod
    def _stricter(
        left: PermissionDecision,
        right: PermissionDecision,
    ) -> PermissionDecision:
        return max((left, right), key=_DECISION_SEVERITY.__getitem__)

    @staticmethod
    def _exec_request_semantics(
        evaluation: ExecPolicyEvaluation,
        arguments: dict,
    ) -> ExecPolicyEvaluation:
        """Do not downgrade calls whose request shape can change command semantics."""

        if evaluation.effect is ToolEffect.SENSITIVE:
            return evaluation
        if arguments.get("env"):
            return ExecPolicyEvaluation(
                ToolEffect.SENSITIVE,
                evaluation.requires_network,
                f"{evaluation.matched_rule}:env",
                "Per-call environment overrides can alter PATH, helpers, pagers, and executable behavior.",
            )
        if bool(arguments.get("pty", False)):
            return ExecPolicyEvaluation(
                ToolEffect.SENSITIVE,
                evaluation.requires_network,
                f"{evaluation.matched_rule}:pty",
                "PTY execution can activate interactive helpers or pagers and requires explicit approval.",
            )
        return evaluation

    def _evaluate_details(
        self,
        step: StepContext,
        tool: AgentTool,
        *,
        arguments: dict | None = None,
        legacy_policy: ToolPolicy | None = None,
    ) -> tuple[PermissionDecision, str, ToolEffect, ExecPolicyEvaluation | None]:
        """Resolve one authoritative execution decision including call-specific policy."""

        exec_evaluation: ExecPolicyEvaluation | None = None
        effective_effect = tool.effect
        if tool.name == "exec" and isinstance(arguments, dict):
            exec_evaluation = self._exec_request_semantics(
                self.exec_policy.classify(arguments.get("argv")),
                arguments,
            )
            effective_effect = exec_evaluation.effect

        evaluation = self.permission_engine.evaluate(
            effect=effective_effect,
            snapshot=step.permissions,
        )
        decision = evaluation.decision
        reason_parts = [evaluation.reason]

        if exec_evaluation is not None:
            reason_parts.append(
                f"Exec policy [{exec_evaluation.matched_rule}] classified the command as "
                f"{effective_effect.value}: {exec_evaluation.reason}"
            )

        if (
            step.permissions.mode is PermissionMode.APPROVAL
            and legacy_policy is not None
        ):
            decision = (
                PermissionDecision.APPROVAL
                if effective_effect not in legacy_policy.auto_approved_effects
                else PermissionDecision.ALLOW
            )
            reason_parts.append(
                f"Compatibility approval policy {'requires approval for' if decision is PermissionDecision.APPROVAL else 'auto-approves'} "
                f"the effective {effective_effect.value} effect."
            )

        if exec_evaluation is not None and exec_evaluation.requires_network:
            network = self.network_policy.evaluate(step.permissions, requested=True)
            decision = self._stricter(decision, network.decision)
            reason_parts.append(network.reason)

        return decision, " ".join(reason_parts), effective_effect, exec_evaluation

    def evaluate_tool(
        self,
        step: StepContext,
        tool: AgentTool,
        *,
        arguments: dict | None = None,
        legacy_policy: ToolPolicy | None = None,
    ) -> tuple[PermissionDecision, str]:
        """Return the exact authorization decision used by real execution.

        The frozen StepContext permission snapshot is authoritative. For the
        unified ``exec`` tool the concrete argv is additionally classified so a
        harmless repository inspection no longer shares the same approval path
        as an arbitrary interpreter or network client.
        """

        decision, reason, _, _ = self._evaluate_details(
            step,
            tool,
            arguments=arguments,
            legacy_policy=legacy_policy,
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

        decision, reason, effective_effect, exec_evaluation = self._evaluate_details(
            step,
            tool,
            arguments=call.arguments,
            legacy_policy=legacy_policy,
        )
        prepared_tool = (
            tool
            if effective_effect is tool.effect
            else replace(tool, effect=effective_effect)
        )

        return PreparedToolCall(
            call=call,
            tool=prepared_tool,
            decision=decision,
            reason=reason,
            effective_effect=effective_effect,
            network_requested=bool(exec_evaluation and exec_evaluation.requires_network),
            matched_exec_rule=(exec_evaluation.matched_rule if exec_evaluation else ""),
        )


__all__ = ["PreparedToolCall", "ToolOrchestrator"]
