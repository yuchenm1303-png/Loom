from __future__ import annotations

from pathlib import Path

from app.ai import ToolCall
from app.agent_runtime.contracts import PermissionMode, ToolEffect
from app.agent_runtime.orchestrator import ToolOrchestrator
from app.agent_runtime.permissions import PermissionDecision
from app.agent_runtime.sandbox_failure import SandboxExecutionError, SandboxFailureKind
from app.agent_runtime.step import StepContext
from app.agent_runtime.tools import AgentTool, ToolContext, ToolRouter


def _step(tool: AgentTool, workspace: Path) -> StepContext:
    return StepContext.build(
        step_id="step-1",
        session_id="session-1",
        turn_id="turn-1",
        model_step=1,
        workspace_dir=str(workspace),
        profile_id="agent.fast",
        permission_mode=PermissionMode.FULL_ACCESS,
        tool_router=ToolRouter((tool,)),
    )


def test_orchestrator_preserves_structured_sandbox_failure(tmp_path):
    def handler(_context, _arguments):
        raise SandboxExecutionError(
            SandboxFailureKind.DENIED,
            "sandbox rejected this synthetic attempt",
            escalatable=True,
        )

    tool = AgentTool(
        name="synthetic_action",
        description="Synthetic action for typed failure propagation.",
        input_schema={"type": "object", "properties": {}, "additionalProperties": False},
        handler=handler,
        effect=ToolEffect.READ_ONLY,
    )
    step = _step(tool, tmp_path)
    orchestrator = ToolOrchestrator()
    prepared = orchestrator.prepare(
        step,
        ToolCall(call_id="call-1", name=tool.name, arguments={}),
    )
    assert prepared.decision is PermissionDecision.ALLOW

    result = orchestrator.execute(
        prepared,
        ToolContext(
            session_id="session-1",
            turn_id="turn-1",
            workspace=tmp_path,
            permission_mode=PermissionMode.FULL_ACCESS.value,
        ),
    )

    assert result.ok is False
    assert result.content == "sandbox rejected this synthetic attempt"
    assert result.data == {
        "failure_kind": "sandbox",
        "sandbox_failure": "denied",
        "sandbox_escalatable": True,
    }
