from __future__ import annotations

import threading
from dataclasses import dataclass, replace
from enum import Enum

from app.ai import ToolCall

from .approval_actions import ApprovalDecisionStore, ReviewDecision, approval_action_for
from .contracts import PermissionMode
from .execution_action import ExecActionIdentity
from .permissions import (
    AdditionalPermissionProfile,
    ApprovalPolicy,
    ExecApprovalRequirement,
    ExecApprovalRequirementKind,
    PermissionDecision,
    PermissionEngine,
    SandboxPermissions,
)
from .sandbox import SandboxMode, SandboxPolicy
from .sandbox_denial import classify_exec_sandbox_denial
from .sandbox_failure import SandboxExecutionError
from .step import StepContext
from .tools import AgentTool, ToolContext, ToolPolicy, ToolResult, validate_tool_arguments


@dataclass(frozen=True, slots=True)
class PreparedToolCall:
    call: ToolCall
    tool: AgentTool
    decision: PermissionDecision
    reason: str
    sandbox_permissions: SandboxPermissions = SandboxPermissions.USE_DEFAULT
    additional_permissions: AdditionalPermissionProfile | None = None
    exec_requirement: ExecApprovalRequirement | None = None
    exec_action: ExecActionIdentity | None = None

    @property
    def initially_approved(self) -> bool:
        return self.decision is PermissionDecision.APPROVAL


class SandboxRetryDisposition(str, Enum):
    NONE = "none"
    WITHOUT_SANDBOX = "without_sandbox"


@dataclass(frozen=True, slots=True)
class SandboxRetryPlan:
    disposition: SandboxRetryDisposition
    approval_required: bool = False
    retry_reason: str = ""

    @property
    def retry(self) -> bool:
        return self.disposition is not SandboxRetryDisposition.NONE


class ToolOrchestrator:
    """Central approval + sandbox orchestration contract.

    Loom's durable runtime may have to pause while a user reviews an action, so
    execution itself cannot be one async stack frame like Codex. The policy and
    state-machine decisions nevertheless live here: runtime layers only persist
    the returned decision and resume the exact same bound action.
    """

    def __init__(self, *, permission_engine: PermissionEngine | None = None) -> None:
        self.permission_engine = permission_engine or PermissionEngine()
        self._approval_stores: dict[str, ApprovalDecisionStore] = {}
        self._approval_stores_guard = threading.RLock()

    def _approval_store(self, session_id: str) -> ApprovalDecisionStore:
        key = str(session_id or "").strip()
        if not key:
            raise ValueError("approval cache requires session_id")
        with self._approval_stores_guard:
            store = self._approval_stores.get(key)
            if store is None:
                store = ApprovalDecisionStore()
                self._approval_stores[key] = store
            return store

    @staticmethod
    def _approval_action(step: StepContext, call: ToolCall):
        try:
            return approval_action_for(step, call, environment_id="local")
        except (TypeError, ValueError):
            # Invalid actions must never gain a cache bypass. Normal validation
            # and tool execution will report the actual malformed request.
            return None

    def cached_review_decision(
        self,
        step: StepContext,
        call: ToolCall,
    ) -> ReviewDecision | None:
        action = self._approval_action(step, call)
        if action is None:
            return None
        return self._approval_store(step.session_id).lookup(action.cache_keys())

    def record_review_decision(
        self,
        step: StepContext,
        call: ToolCall,
        decision: ReviewDecision | str,
    ) -> None:
        action = self._approval_action(step, call)
        if action is None:
            return
        self._approval_store(step.session_id).record(
            action.cache_keys(),
            ReviewDecision(decision),
        )

    def clear_session_approvals(self, session_id: str) -> None:
        with self._approval_stores_guard:
            self._approval_stores.pop(str(session_id or "").strip(), None)

    def _apply_cached_review(
        self,
        step: StepContext,
        prepared: PreparedToolCall,
    ) -> PreparedToolCall:
        if prepared.decision is not PermissionDecision.APPROVAL:
            return prepared
        if (
            self.cached_review_decision(step, prepared.call)
            is not ReviewDecision.APPROVED_FOR_SESSION
        ):
            return prepared
        return replace(
            prepared,
            decision=PermissionDecision.ALLOW,
            reason="Equivalent action was approved for this session.",
        )

    @staticmethod
    def _exec_needs_unsandboxed_fallback_approval(step: StepContext, tool: AgentTool) -> bool:
        sandbox = step.world_state.sandbox
        return bool(
            tool.name == "exec"
            and sandbox is not None
            and sandbox.policy is SandboxPolicy.AUTO
            and sandbox.mode is not SandboxMode.DISABLED
            and not sandbox.enforced
        )

    @staticmethod
    def _exec_required_sandbox_unavailable(step: StepContext, tool: AgentTool) -> bool:
        sandbox = step.world_state.sandbox
        return bool(
            tool.name == "exec"
            and sandbox is not None
            and sandbox.policy is SandboxPolicy.REQUIRED
            and sandbox.mode is not SandboxMode.DISABLED
            and not sandbox.enforced
        )

    @staticmethod
    def _exec_decision(requirement: ExecApprovalRequirement) -> tuple[PermissionDecision, str]:
        if requirement.kind is ExecApprovalRequirementKind.FORBIDDEN:
            return PermissionDecision.DENY, requirement.reason or "exec policy forbids this command"
        if requirement.kind is ExecApprovalRequirementKind.NEEDS_APPROVAL:
            return PermissionDecision.APPROVAL, requirement.reason or "exec command requires approval"
        return PermissionDecision.ALLOW, requirement.reason or "exec command may run under the active policy"

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

        if (
            decision is not PermissionDecision.DENY
            and self._exec_required_sandbox_unavailable(step, tool)
        ):
            sandbox = step.world_state.sandbox
            return (
                PermissionDecision.DENY,
                (
                    "OS sandbox containment is required for this exec call, but no enforced "
                    f"backend is available. {sandbox.reason if sandbox is not None else ''}"
                ).strip(),
            )

        if (
            decision is not PermissionDecision.DENY
            and self._exec_needs_unsandboxed_fallback_approval(step, tool)
        ):
            sandbox = step.world_state.sandbox
            if step.permissions.approval_policy is ApprovalPolicy.NEVER:
                return (
                    PermissionDecision.DENY,
                    (
                        "OS sandbox containment is unavailable and approval policy is never; "
                        "Loom will not silently fall back to unrestricted execution. "
                        f"{sandbox.reason if sandbox is not None else ''}"
                    ).strip(),
                )
            decision = PermissionDecision.APPROVAL
            reason = (
                "OS sandbox containment is unavailable for this exec call. "
                "Approval is required before Loom may use its AUTO unsandboxed fallback. "
                f"{sandbox.reason if sandbox is not None else ''}"
            ).strip()
        return decision, reason

    def _prepare_exec(self, step: StepContext, call: ToolCall, tool: AgentTool) -> PreparedToolCall:
        action = ExecActionIdentity.build(step, call)
        sandbox = step.world_state.sandbox

        # Codex tells models that approval_policy=never rejects commands that
        # explicitly provide sandbox_permissions. Do not interpret Never as a
        # silent grant of either scoped or full extra authority. Omitted
        # sandbox_permissions still resolves to use_default and can run under the
        # ambient sandbox profile.
        if (
            step.permissions.approval_policy is ApprovalPolicy.NEVER
            and "sandbox_permissions" in call.arguments
        ):
            return PreparedToolCall(
                call=call,
                tool=tool,
                decision=PermissionDecision.DENY,
                reason="approval policy never rejects explicit sandbox_permissions requests",
                sandbox_permissions=action.sandbox_permissions,
                additional_permissions=action.additional_permissions,
                exec_action=action,
            )

        # SandboxPolicy.REQUIRED is a Loom platform fail-closed adapter. Resolve
        # the missing backend before any tool handler/process spawn so the
        # runtime cannot fall through to a late RuntimeError or unsandboxed
        # execution.
        if self._exec_required_sandbox_unavailable(step, tool):
            return PreparedToolCall(
                call=call,
                tool=tool,
                decision=PermissionDecision.DENY,
                reason=(
                    "OS sandbox containment is required for this exec call, but no enforced "
                    f"backend is available. {sandbox.reason if sandbox is not None else ''}"
                ).strip(),
                sandbox_permissions=action.sandbox_permissions,
                additional_permissions=action.additional_permissions,
                exec_action=action,
            )

        if (
            action.sandbox_permissions.requires_escalated_permissions
            and sandbox is not None
            and sandbox.policy is SandboxPolicy.REQUIRED
            and sandbox.mode is not SandboxMode.DISABLED
        ):
            return PreparedToolCall(
                call=call,
                tool=tool,
                decision=PermissionDecision.DENY,
                reason="SandboxPolicy.REQUIRED forbids full sandbox bypass for this exec call.",
                sandbox_permissions=action.sandbox_permissions,
                additional_permissions=action.additional_permissions,
                exec_action=action,
            )

        if (
            action.sandbox_permissions.uses_additional_permissions
            and sandbox is not None
            and sandbox.mode is not SandboxMode.DISABLED
            and not sandbox.enforced
        ):
            return PreparedToolCall(
                call=call,
                tool=tool,
                decision=PermissionDecision.DENY,
                reason=(
                    "Additional permissions require an enforced sandbox; Loom will not convert "
                    "a scoped grant into unrestricted execution."
                ),
                sandbox_permissions=action.sandbox_permissions,
                additional_permissions=action.additional_permissions,
                exec_action=action,
            )

        requirement = self.permission_engine.exec_requirement(
            snapshot=step.permissions,
            sandbox_permissions=action.sandbox_permissions,
            proposed_prefix_rule=action.prefix_rule,
        )
        decision, reason = self._exec_decision(requirement)

        # REQUIRE_ESCALATED is a first-attempt sandbox override. It must never
        # inherit Loom's older "run once in sandbox, then ask again" behavior.
        if action.sandbox_permissions.requires_escalated_permissions:
            if step.permissions.approval_policy is ApprovalPolicy.NEVER:
                decision = PermissionDecision.DENY
                reason = "approval policy never forbids require_escalated execution"
            elif decision is PermissionDecision.ALLOW:
                decision = PermissionDecision.APPROVAL
                reason = action.justification or "command requests full sandbox bypass"

        # AUTO with no backend is a Loom platform adaptation. Codex assumes an
        # enforceable sandbox/profile boundary; fail closed or require explicit
        # user review rather than pretending the command was sandboxed.
        if (
            decision is not PermissionDecision.DENY
            and action.sandbox_permissions is SandboxPermissions.USE_DEFAULT
            and self._exec_needs_unsandboxed_fallback_approval(step, tool)
        ):
            sandbox_snapshot = step.world_state.sandbox
            if step.permissions.approval_policy is ApprovalPolicy.NEVER:
                decision = PermissionDecision.DENY
                reason = "sandbox unavailable and approval policy never forbids fallback"
            else:
                decision = PermissionDecision.APPROVAL
                reason = (
                    "OS sandbox containment is unavailable; approval is required before AUTO "
                    f"fallback. {sandbox_snapshot.reason if sandbox_snapshot is not None else ''}"
                ).strip()

        return PreparedToolCall(
            call=call,
            tool=tool,
            decision=decision,
            reason=reason,
            sandbox_permissions=action.sandbox_permissions,
            additional_permissions=action.additional_permissions,
            exec_requirement=requirement,
            exec_action=action,
        )

    def capability_contract(
        self,
        step: StepContext,
        *,
        legacy_policy: ToolPolicy | None = None,
    ) -> str:
        del legacy_policy
        if step.permissions.approval_policy is ApprovalPolicy.NEVER:
            exec_permission_guidance = (
                "Approval policy is never. For exec, do not provide sandbox_permissions, "
                "additional_permissions, justification, or prefix_rule; use the ambient sandbox profile."
            )
        else:
            exec_permission_guidance = (
                "For exec, prefer sandbox_permissions=with_additional_permissions with only the needed "
                "filesystem/network grants. Use require_escalated only when a sandboxed grant cannot "
                "satisfy the action."
            )
        return "\n".join(
            (
                "<loom_tool_harness>",
                "The tool definitions attached to this model request are the authoritative capability surface for this step.",
                "Choose tools from their semantic descriptions and schemas. A general-purpose tool may satisfy a request even when no specialist tool has a matching name.",
                "When a suitable tool exists, issue the tool call directly. Do not ask the user to pre-authorize it in prose; Loom's runtime will allow it, request approval, or deny it according to the active policy.",
                exec_permission_guidance,
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

        if tool.name == "exec":
            return self._apply_cached_review(step, self._prepare_exec(step, call, tool))

        decision, reason = self.evaluate_tool(
            step,
            tool,
            legacy_policy=legacy_policy,
        )
        return self._apply_cached_review(
            step,
            PreparedToolCall(
                call=call,
                tool=tool,
                decision=decision,
                reason=reason,
            ),
        )

    def sandbox_retry_plan(
        self,
        step: StepContext,
        prepared: PreparedToolCall,
        result: ToolResult,
        *,
        already_approved: bool,
        strict_auto_review: bool = False,
        network_approval_context: object | None = None,
    ) -> SandboxRetryPlan:
        """Resolve Codex's one retry after a typed sandbox denial.

        Ordinary stderr/non-zero exit is never sufficient: callers must provide
        the typed sandbox-denied result produced by a platform adapter or by the
        centralized Codex-compatible denial classifier.
        """

        if prepared.tool.name != "exec" or result.ok:
            return SandboxRetryPlan(SandboxRetryDisposition.NONE)
        if result.data.get("failure_kind") != "sandbox" or result.data.get("sandbox_failure") != "denied":
            return SandboxRetryPlan(SandboxRetryDisposition.NONE)
        if prepared.sandbox_permissions.requires_escalated_permissions:
            return SandboxRetryPlan(SandboxRetryDisposition.NONE)

        sandbox = step.world_state.sandbox
        if sandbox is None or not sandbox.enforced or sandbox.mode is SandboxMode.DISABLED:
            return SandboxRetryPlan(SandboxRetryDisposition.NONE)
        if sandbox.policy is SandboxPolicy.REQUIRED:
            return SandboxRetryPlan(SandboxRetryDisposition.NONE)

        policy = step.permissions.approval_policy
        wants_no_sandbox_approval = (
            policy is ApprovalPolicy.UNLESS_TRUSTED
            or (
                policy is ApprovalPolicy.GRANULAR
                and step.permissions.granular_approval is not None
                and step.permissions.granular_approval.sandbox_approval
            )
        )
        # Codex has a special on-request network approval route. Window 04 owns
        # the network service; this interface accepts its structured context
        # without inferring network denial from stderr.
        if policy is ApprovalPolicy.ON_REQUEST and network_approval_context is not None:
            wants_no_sandbox_approval = True
        if not wants_no_sandbox_approval:
            return SandboxRetryPlan(SandboxRetryDisposition.NONE)

        bypass_retry_approval = (
            not strict_auto_review
            and already_approved
            and network_approval_context is None
        )
        return SandboxRetryPlan(
            SandboxRetryDisposition.WITHOUT_SANDBOX,
            approval_required=not bypass_retry_approval,
            retry_reason=str(result.content or "sandbox denied the first attempt").strip(),
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
            if prepared.tool.name == "exec":
                result = classify_exec_sandbox_denial(result)
            return result
        except SandboxExecutionError as exc:
            return ToolResult(
                ok=False,
                content=str(exc),
                data=exc.to_result_data(),
            )
        except Exception as exc:
            return ToolResult(ok=False, content=f"{type(exc).__name__}: {exc}")


__all__ = [
    "PreparedToolCall",
    "SandboxRetryDisposition",
    "SandboxRetryPlan",
    "ToolOrchestrator",
]
