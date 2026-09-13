from __future__ import annotations

from app.agent_runtime.contracts import PermissionMode, ToolEffect
from app.agent_runtime.orchestrator import ToolOrchestrator
from app.agent_runtime.permissions import PermissionDecision
from app.agent_runtime.sandbox import SandboxManager, SandboxPolicy
from app.agent_runtime.step import StepContext
from app.agent_runtime.tools import AgentTool, ToolResult, ToolRouter


def _exec_tool() -> AgentTool:
    return AgentTool(
        name="exec",
        description="Synthetic exec tool for authorization tests.",
        input_schema={"type": "object", "properties": {}, "additionalProperties": False},
        handler=lambda _context, _arguments: ToolResult(ok=True, content="ok"),
        effect=ToolEffect.READ_ONLY,
    )


def _step(tmp_path, *, mode: PermissionMode, manager: SandboxManager) -> StepContext:
    tool = _exec_tool()
    router = ToolRouter((tool,))
    return StepContext.build(
        step_id="step-1",
        session_id="session-1",
        turn_id="turn-1",
        model_step=1,
        workspace_dir=str(tmp_path),
        profile_id="agent.fast",
        permission_mode=mode,
        tool_router=router,
        sandbox_snapshot=manager.snapshot(
            workspace=tmp_path,
            permission_mode=mode,
        ),
    )


def test_auto_exec_fallback_requires_approval_when_no_backend_exists(tmp_path):
    manager = SandboxManager(
        policy=SandboxPolicy.AUTO,
        system_name="Darwin",
        probe_backend=False,
    )
    step = _step(tmp_path, mode=PermissionMode.WORKSPACE, manager=manager)
    tool = step.tool_router.get("exec")
    assert tool is not None

    decision, reason = ToolOrchestrator().evaluate_tool(step, tool)

    assert decision is PermissionDecision.APPROVAL
    assert "unsandboxed fallback" in reason
    assert "unavailable" in reason.casefold() or "no loom os sandbox backend" in reason.casefold()


def test_full_access_does_not_add_redundant_sandbox_fallback_approval(tmp_path):
    manager = SandboxManager(
        policy=SandboxPolicy.AUTO,
        system_name="Darwin",
        probe_backend=False,
    )
    step = _step(tmp_path, mode=PermissionMode.FULL_ACCESS, manager=manager)
    tool = step.tool_router.get("exec")
    assert tool is not None

    decision, _reason = ToolOrchestrator().evaluate_tool(step, tool)

    assert decision is PermissionDecision.ALLOW


def test_explicit_sandbox_off_does_not_add_auto_fallback_approval(tmp_path):
    manager = SandboxManager(
        policy=SandboxPolicy.OFF,
        system_name="Darwin",
        probe_backend=False,
    )
    step = _step(tmp_path, mode=PermissionMode.WORKSPACE, manager=manager)
    tool = step.tool_router.get("exec")
    assert tool is not None

    decision, _reason = ToolOrchestrator().evaluate_tool(step, tool)

    assert decision is PermissionDecision.ALLOW
