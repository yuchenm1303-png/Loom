from __future__ import annotations

from dataclasses import replace

from app.ai import ToolCall
from app.agent_runtime.contracts import PermissionMode, ToolEffect
from app.agent_runtime.orchestrator import ToolOrchestrator
from app.agent_runtime.permissions import (
    ApprovalPolicy,
    GranularApprovalConfig,
    PermissionDecision,
    SandboxPermissions,
    permission_snapshot,
)
from app.agent_runtime.sandbox import SandboxManager, SandboxPolicy
from app.agent_runtime.step import StepContext
from app.agent_runtime.tools import AgentTool, ToolResult, ToolRouter


def _step(tmp_path, policy: ApprovalPolicy) -> StepContext:
    tool = AgentTool(
        name="exec",
        description="Synthetic exec for additional-permission policy contracts.",
        input_schema={"type": "object", "properties": {}, "additionalProperties": True},
        handler=lambda _context, _arguments: ToolResult(ok=True, content="ok"),
        effect=ToolEffect.SENSITIVE,
    )
    granular = GranularApprovalConfig() if policy is ApprovalPolicy.GRANULAR else None
    permissions = replace(
        permission_snapshot(PermissionMode.WORKSPACE),
        approval_policy=policy,
        granular_approval=granular,
    )
    manager = SandboxManager(
        policy=SandboxPolicy.AUTO,
        bubblewrap_executable="/synthetic/bwrap",
        probe_backend=False,
        system_name="Linux",
    )
    return StepContext.build(
        step_id="step-additional",
        session_id="session-additional",
        turn_id="turn-additional",
        model_step=1,
        workspace_dir=str(tmp_path),
        profile_id="agent.fast",
        permission_mode=PermissionMode.WORKSPACE,
        permissions=permissions,
        sandbox_snapshot=manager.snapshot(permissions=permissions, workspace=tmp_path),
        tool_router=ToolRouter((tool,)),
    )


def _call(tmp_path) -> ToolCall:
    extra = tmp_path / "extra"
    extra.mkdir(exist_ok=True)
    return ToolCall(
        call_id="exec-additional",
        name="exec",
        arguments={
            "argv": ["synthetic-program"],
            "sandbox_permissions": SandboxPermissions.WITH_ADDITIONAL_PERMISSIONS.value,
            "additional_permissions": {"file_system": {"write": [str(extra)]}},
        },
    )


def test_fresh_additional_permissions_are_reviewable_under_on_request(tmp_path):
    prepared = ToolOrchestrator().prepare(
        _step(tmp_path, ApprovalPolicy.ON_REQUEST),
        _call(tmp_path),
    )

    assert prepared.decision is PermissionDecision.APPROVAL
    assert prepared.sandbox_permissions is SandboxPermissions.WITH_ADDITIONAL_PERMISSIONS
    assert prepared.additional_permissions is not None


def test_fresh_additional_permissions_fail_closed_under_unless_trusted(tmp_path):
    prepared = ToolOrchestrator().prepare(
        _step(tmp_path, ApprovalPolicy.UNLESS_TRUSTED),
        _call(tmp_path),
    )

    assert prepared.decision is PermissionDecision.DENY
    assert "on-request" in prepared.reason
    assert "preapproved" in prepared.reason


def test_fresh_additional_permissions_fail_closed_under_granular(tmp_path):
    prepared = ToolOrchestrator().prepare(
        _step(tmp_path, ApprovalPolicy.GRANULAR),
        _call(tmp_path),
    )

    assert prepared.decision is PermissionDecision.DENY
    assert "on-request" in prepared.reason


def test_capability_guidance_only_recommends_fresh_additional_permissions_on_request(tmp_path):
    orchestrator = ToolOrchestrator()

    on_request = orchestrator.capability_contract(_step(tmp_path, ApprovalPolicy.ON_REQUEST))
    unless_trusted = orchestrator.capability_contract(
        _step(tmp_path, ApprovalPolicy.UNLESS_TRUSTED)
    )

    assert "prefer sandbox_permissions=with_additional_permissions" in on_request
    assert "Do not request with_additional_permissions" in unless_trusted
