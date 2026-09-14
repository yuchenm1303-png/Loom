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
from app.agent_runtime.approval_actions import ReviewDecision
from app.agent_runtime.permissions import SandboxPermissions


class ScriptedPlatform:
    def __init__(self):
        self.responses = [
            ModelResponse(
                tool_calls=(
                    ToolCall(
                        call_id="exec-review",
                        name="exec",
                        arguments={
                            "argv": ["synthetic-program"],
                            "sandbox_permissions": SandboxPermissions.REQUIRE_ESCALATED.value,
                            "justification": "Synthetic review boundary.",
                        },
                    ),
                )
            )
        ]

    def execute_chat(self, _profile_id, _request):
        return self.responses.pop(0)


def _runtime(tmp_path):
    tool = AgentTool(
        name="exec",
        description="Synthetic exec for review boundary tests.",
        input_schema={
            "type": "object",
            "properties": {"argv": {"type": "array", "items": {"type": "string"}}},
            "required": ["argv"],
            "additionalProperties": False,
        },
        handler=lambda _context, _arguments: ToolResult(ok=True, content="ok"),
        effect=ToolEffect.SENSITIVE,
    )
    return SandboxAgentRuntime(
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


@pytest.mark.parametrize("decision", [ReviewDecision.TIMED_OUT, ReviewDecision.ABORTED])
def test_legacy_boolean_resume_does_not_mislabel_terminal_review_outcomes(tmp_path, decision):
    runtime = _runtime(tmp_path)
    try:
        session = runtime.create_session(
            AGENT_FAST_ROLE.role_id,
            workspace_dir=tmp_path,
            permission_mode=PermissionMode.WORKSPACE,
        )
        waiting = runtime.start_turn(session.session_id, "Run it.")
        assert waiting.status is AgentStatus.WAITING_APPROVAL

        with pytest.raises(ValueError, match="not supported by the boolean resume boundary"):
            runtime.resume_approval(
                session.session_id,
                "exec-review",
                approved=False,
                review_decision=decision,
            )

        still_waiting = runtime.get_session(session.session_id)
        assert still_waiting.status is AgentStatus.WAITING_APPROVAL
    finally:
        runtime.close()
