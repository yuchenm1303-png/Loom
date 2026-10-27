from __future__ import annotations

from pathlib import Path

from app.ai import ToolCall
from app.agent_runtime.contracts import PermissionMode, ToolEffect
from app.agent_runtime.orchestrator import ToolOrchestrator
from app.agent_runtime.permissions import PermissionDecision
from app.agent_runtime.sandbox_failure import SandboxExecutionError, SandboxFailureKind
from app.agent_runtime.step import StepContext
from app.agent_runtime.tools import AgentTool, ToolContext, ToolResult, ToolRouter


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


def test_exec_denial_names_the_workspace_even_when_the_command_ran_deeper(tmp_path):
    nested = tmp_path / "src" / "app"

    def handler(_context, _arguments):
        return ToolResult(
            ok=False,
            content="process=proc-1 status=exited backend=pipe\nexit=128",
            data={
                "argv": [r"C:\Program Files\Git\cmd\git.exe", "status"],
                "cwd": str(nested),
                "returncode": 128,
                "stdout": "",
                "stderr": "fatal: Unable to read current working directory: Permission denied\n",
                "sandbox": {"enforced": True, "backend": "windows-mxc"},
            },
        )

    tool = AgentTool(
        name="exec",
        description="Synthetic exec for the denial classifier.",
        input_schema={
            "type": "object",
            "properties": {"argv": {"type": "array", "items": {"type": "string"}}},
            "required": ["argv"],
            "additionalProperties": False,
        },
        handler=handler,
        effect=ToolEffect.READ_ONLY,
    )
    step = _step(tool, tmp_path)
    orchestrator = ToolOrchestrator()
    prepared = orchestrator.prepare(
        step,
        ToolCall(call_id="call-1", name=tool.name, arguments={"argv": ["git", "status"]}),
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

    denial = result.data["sandbox_denial"]
    assert denial["kind"] == "cwd"
    # The folders that are closed sit above the workspace the sandbox opened, not above the
    # deeper directory the command happened to run in.
    assert denial["path"] == str(tmp_path)
