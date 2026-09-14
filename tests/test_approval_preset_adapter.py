from __future__ import annotations

from app.ai import ToolCall
from app.agent_runtime.contracts import PermissionMode, ToolEffect
from app.agent_runtime.orchestrator import ToolOrchestrator
from app.agent_runtime.permissions import ApprovalPolicy, PermissionDecision, permission_snapshot
from app.agent_runtime.sandbox import SandboxManager, SandboxPolicy
from app.agent_runtime.step import StepContext
from app.agent_runtime.tools import AgentTool, ToolResult, ToolRouter


def test_loom_approval_preset_maps_to_unless_trusted():
    assert permission_snapshot(PermissionMode.APPROVAL).approval_policy is ApprovalPolicy.UNLESS_TRUSTED


def test_loom_approval_preset_still_prompts_for_unmatched_exec(tmp_path):
    tool = AgentTool(
        name="exec",
        description="Synthetic exec for approval preset mapping.",
        input_schema={"type": "object", "properties": {}, "additionalProperties": True},
        handler=lambda _context, _arguments: ToolResult(ok=True, content="ok"),
        effect=ToolEffect.SENSITIVE,
    )
    permissions = permission_snapshot(PermissionMode.APPROVAL)
    manager = SandboxManager(
        policy=SandboxPolicy.AUTO,
        bubblewrap_executable="/synthetic/bwrap",
        probe_backend=False,
        system_name="Linux",
    )
    step = StepContext.build(
        step_id="step-approval",
        session_id="session-approval",
        turn_id="turn-approval",
        model_step=1,
        workspace_dir=str(tmp_path),
        profile_id="agent.fast",
        permission_mode=PermissionMode.APPROVAL,
        permissions=permissions,
        sandbox_snapshot=manager.snapshot(permissions=permissions, workspace=tmp_path),
        tool_router=ToolRouter((tool,)),
    )

    prepared = ToolOrchestrator().prepare(
        step,
        ToolCall(call_id="exec-1", name="exec", arguments={"argv": ["synthetic-program"]}),
    )

    assert prepared.decision is PermissionDecision.APPROVAL
