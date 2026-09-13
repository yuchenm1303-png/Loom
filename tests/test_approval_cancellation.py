from __future__ import annotations

import pytest

from app.ai import AGENT_FAST_ROLE, ModelResponse, ToolCall
from app.agent_runtime import (
    AgentStatus,
    AgentTool,
    FileAgentSessionStore,
    PermissionMode,
    SandboxAgentRuntime,
    SandboxManager,
    SandboxPolicy,
    ToolEffect,
    ToolRegistry,
    ToolResult,
)
from app.agent_runtime.permissions import SandboxPermissions


class ScriptedPlatform:
    def __init__(self):
        self.responses = [
            ModelResponse(
                tool_calls=(
                    ToolCall(
                        call_id="exec-cancel",
                        name="exec",
                        arguments={
                            "argv": ["synthetic-program"],
                            "sandbox_permissions": SandboxPermissions.REQUIRE_ESCALATED.value,
                            "justification": "Synthetic approval race.",
                        },
                    ),
                )
            )
        ]

    def execute_chat(self, _profile_id, _request):
        if not self.responses:
            raise AssertionError("scripted platform ran out of responses")
        return self.responses.pop(0)


def test_cancellation_wins_over_late_approval_and_never_executes(tmp_path):
    executions = []
    tool = AgentTool(
        name="exec",
        description="Synthetic exec for cancellation review race.",
        input_schema={
            "type": "object",
            "properties": {"argv": {"type": "array", "items": {"type": "string"}}},
            "required": ["argv"],
            "additionalProperties": False,
        },
        handler=lambda _context, _arguments: (
            executions.append(True) or ToolResult(ok=True, content="unexpected")
        ),
        effect=ToolEffect.SENSITIVE,
    )
    runtime = SandboxAgentRuntime(
        platform=ScriptedPlatform(),
        store=FileAgentSessionStore(tmp_path / "state"),
        tools=ToolRegistry((tool,)),
        sandbox_manager=SandboxManager(
            policy=SandboxPolicy.AUTO,
            bubblewrap_executable="/synthetic/bwrap",
            probe_backend=False,
            system_name="Linux",
        ),
    )
    try:
        session = runtime.create_session(
            AGENT_FAST_ROLE.role_id,
            workspace_dir=tmp_path,
            permission_mode=PermissionMode.WORKSPACE,
        )
        waiting = runtime.start_turn(session.session_id, "Run it.")
        assert waiting.status is AgentStatus.WAITING_APPROVAL

        cancelled = runtime.cancel(session.session_id)
        assert cancelled.status is AgentStatus.CANCELLED
        assert cancelled.pending_approval is None
        assert executions == []

        with pytest.raises(RuntimeError, match="not waiting for approval"):
            runtime.resume_approval(
                session.session_id,
                "exec-cancel",
                approved=True,
            )
        assert executions == []
    finally:
        runtime.close()
