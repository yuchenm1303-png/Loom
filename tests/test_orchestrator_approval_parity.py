from __future__ import annotations

from dataclasses import replace

from app.ai import ToolCall
from app.agent_runtime.contracts import PermissionMode, ToolEffect
from app.agent_runtime.orchestrator import SandboxRetryDisposition, ToolOrchestrator
from app.agent_runtime.permissions import (
    ApprovalPolicy,
    GranularApprovalConfig,
    PermissionDecision,
    SandboxPermissions,
    permission_snapshot,
)
from app.agent_runtime.sandbox import SandboxManager, SandboxPolicy
from app.agent_runtime.sandbox_failure import SandboxExecutionError, SandboxFailureKind
from app.agent_runtime.step import StepContext
from app.agent_runtime.tools import AgentTool, ToolResult, ToolRouter


def _tool() -> AgentTool:
    return AgentTool(
        name="exec",
        description="Synthetic exec for orchestrator policy contracts.",
        input_schema={"type": "object", "properties": {}, "additionalProperties": True},
        handler=lambda _context, _arguments: ToolResult(ok=True, content="ok"),
        effect=ToolEffect.SENSITIVE,
    )


def _step(tmp_path, policy, *, granular=None, sandbox_policy=SandboxPolicy.AUTO, backend=True):
    tool = _tool()
    permissions = replace(
        permission_snapshot(PermissionMode.WORKSPACE),
        approval_policy=policy,
        granular_approval=granular,
    )
    manager = SandboxManager(
        policy=sandbox_policy,
        bubblewrap_executable="/synthetic/bwrap" if backend else "",
        probe_backend=False,
        system_name="Linux",
    )
    return StepContext.build(
        step_id="step-1",
        session_id="session-1",
        turn_id="turn-1",
        model_step=1,
        workspace_dir=str(tmp_path),
        profile_id="agent.fast",
        permission_mode=PermissionMode.WORKSPACE,
        tool_router=ToolRouter((tool,)),
        sandbox_snapshot=manager.snapshot(
            permissions=permissions,
            workspace=tmp_path,
        ),
        permissions=permissions,
    )


def _call(**extra):
    arguments = {"argv": ["synthetic-program", "one"]}
    arguments.update(extra)
    return ToolCall(call_id="exec-1", name="exec", arguments=arguments)


def _denial() -> ToolResult:
    exc = SandboxExecutionError(
        SandboxFailureKind.DENIED,
        "typed sandbox denial",
        escalatable=True,
    )
    return ToolResult(ok=False, content=str(exc), data=exc.to_result_data())


def test_unless_trusted_retry_reuses_initial_review_unless_strict(tmp_path):
    orchestrator = ToolOrchestrator()
    step = _step(tmp_path, ApprovalPolicy.UNLESS_TRUSTED)
    prepared = orchestrator.prepare(step, _call())
    assert prepared.decision is PermissionDecision.APPROVAL

    normal = orchestrator.sandbox_retry_plan(
        step,
        prepared,
        _denial(),
        already_approved=True,
    )
    strict = orchestrator.sandbox_retry_plan(
        step,
        prepared,
        _denial(),
        already_approved=True,
        strict_auto_review=True,
    )

    assert normal.disposition is SandboxRetryDisposition.WITHOUT_SANDBOX
    assert normal.approval_required is False
    assert strict.disposition is SandboxRetryDisposition.WITHOUT_SANDBOX
    assert strict.approval_required is True


def test_network_denial_context_requires_fresh_review_even_after_prior_approval(tmp_path):
    orchestrator = ToolOrchestrator()
    step = _step(tmp_path, ApprovalPolicy.UNLESS_TRUSTED)
    prepared = orchestrator.prepare(step, _call())

    plan = orchestrator.sandbox_retry_plan(
        step,
        prepared,
        _denial(),
        already_approved=True,
        network_approval_context={"host": "blocked.example"},
    )

    assert plan.disposition is SandboxRetryDisposition.WITHOUT_SANDBOX
    assert plan.approval_required is True


def test_on_request_only_uses_retry_review_for_structured_network_context(tmp_path):
    orchestrator = ToolOrchestrator()
    step = _step(tmp_path, ApprovalPolicy.ON_REQUEST)
    prepared = orchestrator.prepare(step, _call())

    plain = orchestrator.sandbox_retry_plan(
        step,
        prepared,
        _denial(),
        already_approved=False,
    )
    network = orchestrator.sandbox_retry_plan(
        step,
        prepared,
        _denial(),
        already_approved=False,
        network_approval_context={"host": "blocked.example"},
    )

    assert plain.disposition is SandboxRetryDisposition.NONE
    assert network.disposition is SandboxRetryDisposition.WITHOUT_SANDBOX
    assert network.approval_required is True


def test_never_does_not_turn_sandbox_denial_into_retry_prompt(tmp_path):
    orchestrator = ToolOrchestrator()
    step = _step(tmp_path, ApprovalPolicy.NEVER)
    prepared = orchestrator.prepare(step, _call())

    plan = orchestrator.sandbox_retry_plan(
        step,
        prepared,
        _denial(),
        already_approved=False,
    )

    assert plan.disposition is SandboxRetryDisposition.NONE


def test_never_rejects_any_explicit_sandbox_permissions_request(tmp_path):
    orchestrator = ToolOrchestrator()
    step = _step(tmp_path, ApprovalPolicy.NEVER)

    explicit_default = orchestrator.prepare(
        step,
        _call(sandbox_permissions=SandboxPermissions.USE_DEFAULT.value),
    )
    scoped = orchestrator.prepare(
        step,
        _call(
            sandbox_permissions=SandboxPermissions.WITH_ADDITIONAL_PERMISSIONS.value,
            additional_permissions={"file_system": {"read": [str(tmp_path)]}},
        ),
    )
    escalated = orchestrator.prepare(
        step,
        _call(
            sandbox_permissions=SandboxPermissions.REQUIRE_ESCALATED.value,
            justification="Should be rejected under never.",
        ),
    )

    assert explicit_default.decision is PermissionDecision.DENY
    assert scoped.decision is PermissionDecision.DENY
    assert escalated.decision is PermissionDecision.DENY


def test_never_still_allows_omitted_sandbox_permissions_under_ambient_sandbox(tmp_path):
    orchestrator = ToolOrchestrator()
    step = _step(tmp_path, ApprovalPolicy.NEVER)
    prepared = orchestrator.prepare(step, _call())
    assert prepared.decision is PermissionDecision.ALLOW
    assert prepared.sandbox_permissions is SandboxPermissions.USE_DEFAULT


def test_required_sandbox_unavailable_is_denied_before_execution(tmp_path):
    orchestrator = ToolOrchestrator()
    step = _step(
        tmp_path,
        ApprovalPolicy.ON_REQUEST,
        sandbox_policy=SandboxPolicy.REQUIRED,
        backend=False,
    )

    prepared = orchestrator.prepare(step, _call())

    assert prepared.decision is PermissionDecision.DENY
    assert "required" in prepared.reason.casefold()
    assert "backend" in prepared.reason.casefold()


def test_granular_can_forbid_explicit_sandbox_override_prompt(tmp_path):
    orchestrator = ToolOrchestrator()
    step = _step(
        tmp_path,
        ApprovalPolicy.GRANULAR,
        granular=GranularApprovalConfig(sandbox_approval=False, rules=False),
    )
    prepared = orchestrator.prepare(
        step,
        _call(
            sandbox_permissions=SandboxPermissions.REQUIRE_ESCALATED.value,
            justification="Needs escalation",
        ),
    )

    assert prepared.decision is PermissionDecision.DENY
