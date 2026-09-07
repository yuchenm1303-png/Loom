from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from app.agent_runtime.contracts import PermissionMode, ToolEffect
from app.agent_runtime.orchestrator import ToolOrchestrator
from app.agent_runtime.permissions import (
    ApprovalPolicy,
    FileSystemAccess,
    PermissionDecision,
    PermissionEngine,
    permission_preset,
    permission_snapshot,
)
from app.agent_runtime.sandbox import SandboxManager, SandboxMode, SandboxPolicy
from app.agent_runtime.step import StepContext
from app.agent_runtime.tools import AgentTool, ToolResult, ToolRouter


def _tool(name: str, effect: ToolEffect) -> AgentTool:
    return AgentTool(
        name=name,
        description=f"{name} test tool",
        input_schema={"type": "object", "properties": {}, "additionalProperties": False},
        handler=lambda _context, _arguments: ToolResult(ok=True, content="ok"),
        effect=effect,
    )


def test_permission_snapshots_are_canonical_and_preserve_existing_mode_semantics():
    expected = {
        PermissionMode.READ_ONLY: (
            ApprovalPolicy.NEVER,
            FileSystemAccess.READ_ONLY,
            frozenset({ToolEffect.READ_ONLY}),
        ),
        PermissionMode.APPROVAL: (
            ApprovalPolicy.ON_REQUEST,
            FileSystemAccess.WORKSPACE_WRITE,
            frozenset({ToolEffect.READ_ONLY}),
        ),
        PermissionMode.WORKSPACE: (
            ApprovalPolicy.ON_REQUEST,
            FileSystemAccess.WORKSPACE_WRITE,
            frozenset({ToolEffect.READ_ONLY, ToolEffect.MUTATING}),
        ),
        PermissionMode.FULL_ACCESS: (
            ApprovalPolicy.NEVER,
            FileSystemAccess.UNRESTRICTED,
            frozenset(ToolEffect),
        ),
    }

    for mode, (approval, filesystem, effects) in expected.items():
        snapshot = permission_snapshot(mode)
        assert snapshot.mode is mode
        assert snapshot.approval_policy is approval
        assert snapshot.file_system_access is filesystem
        assert snapshot.profile.allowed_effects == effects
        assert permission_snapshot(snapshot) is snapshot
        assert permission_preset(mode) is snapshot


def test_approval_mode_explicitly_separates_tool_authorization_from_containment():
    snapshot = permission_snapshot(PermissionMode.APPROVAL)
    engine = PermissionEngine()

    assert snapshot.file_system_access is FileSystemAccess.WORKSPACE_WRITE
    assert engine.evaluate(effect=ToolEffect.READ_ONLY, snapshot=snapshot).decision is PermissionDecision.ALLOW
    assert engine.evaluate(effect=ToolEffect.MUTATING, snapshot=snapshot).decision is PermissionDecision.APPROVAL
    assert engine.evaluate(effect=ToolEffect.SENSITIVE, snapshot=snapshot).decision is PermissionDecision.APPROVAL


def test_step_context_captures_one_immutable_permission_snapshot():
    snapshot = permission_snapshot(PermissionMode.WORKSPACE)
    router = ToolRouter((_tool("inspect", ToolEffect.READ_ONLY),))

    step = StepContext.build(
        step_id="step-1",
        session_id="session-1",
        turn_id="turn-1",
        model_step=1,
        workspace_dir="/tmp/workspace",
        profile_id="agent.fast",
        permission_mode=PermissionMode.WORKSPACE,
        permissions=snapshot,
        tool_router=router,
    )

    assert step.permissions is snapshot
    assert step.world_state.permission_mode is snapshot.mode
    assert step.permission_profile is snapshot.profile
    assert step.approval_policy is snapshot.approval_policy
    with pytest.raises(FrozenInstanceError):
        step.permissions.file_system_access = FileSystemAccess.UNRESTRICTED  # type: ignore[misc]


def test_step_context_rejects_mismatched_permission_versions():
    with pytest.raises(ValueError, match="does not match"):
        StepContext.build(
            step_id="step-1",
            session_id="session-1",
            turn_id="turn-1",
            model_step=1,
            workspace_dir="/tmp/workspace",
            profile_id="agent.fast",
            permission_mode=PermissionMode.READ_ONLY,
            permissions=permission_snapshot(PermissionMode.FULL_ACCESS),
            tool_router=ToolRouter(()),
        )


def test_orchestrator_and_sandbox_consume_the_same_resolved_snapshot(tmp_path):
    snapshot = permission_snapshot(PermissionMode.WORKSPACE)
    tool = _tool("change", ToolEffect.MUTATING)
    step = StepContext.build(
        step_id="step-1",
        session_id="session-1",
        turn_id="turn-1",
        model_step=1,
        workspace_dir=str(tmp_path),
        profile_id="agent.fast",
        permission_mode=snapshot.mode,
        permissions=snapshot,
        tool_router=ToolRouter((tool,)),
    )

    decision, _ = ToolOrchestrator().evaluate_tool(step, tool)
    sandbox = SandboxManager(
        policy=SandboxPolicy.AUTO,
        system_name="Windows",
        probe_backend=False,
    ).snapshot(permissions=step.permissions, workspace=tmp_path)

    assert decision is PermissionDecision.ALLOW
    assert sandbox.mode is SandboxMode.WORKSPACE
    assert sandbox.enforced is False


def test_sandbox_compatibility_path_also_resolves_through_permission_snapshot(tmp_path):
    manager = SandboxManager(policy=SandboxPolicy.OFF, system_name="Windows", probe_backend=False)

    for mode in PermissionMode:
        snapshot = permission_snapshot(mode)
        from_snapshot = manager.snapshot(permissions=snapshot, workspace=tmp_path)
        from_legacy_mode = manager.snapshot(permission_mode=mode, workspace=tmp_path)
        assert from_snapshot.mode is from_legacy_mode.mode
        assert manager.mode_for_permission(mode) is manager.mode_for_permissions(snapshot)
