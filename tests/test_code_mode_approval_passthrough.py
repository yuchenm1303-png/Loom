from __future__ import annotations

from pathlib import Path

from app.agent_runtime import (
    AgentStatus,
    AgentTool,
    CodeModeRuntime,
    FileAgentSessionStore,
    PermissionMode,
    ToolContext,
    ToolEffect,
    ToolRegistry,
    ToolResult,
)
from app.ai import AGENT_FAST_ROLE, ModelResponse, ToolCall


class RecordingPlatform:
    def __init__(self, responses):
        self.responses = list(responses)

    def execute_chat(self, _profile_id, _request):
        if not self.responses:
            raise AssertionError("scripted platform ran out of responses")
        return self.responses.pop(0)


def test_code_mode_runtime_forwards_approval_granted_to_base_executor(tmp_path: Path):
    called: list[str] = []

    def mutate(_context: ToolContext, arguments):
        called.append(arguments["value"])
        return ToolResult(ok=True, content="mutated")

    tool = AgentTool(
        name="mutate",
        description="Mutating test tool",
        input_schema={
            "type": "object",
            "properties": {"value": {"type": "string"}},
            "required": ["value"],
            "additionalProperties": False,
        },
        handler=mutate,
        effect=ToolEffect.MUTATING,
    )
    platform = RecordingPlatform(
        [
            ModelResponse(
                tool_calls=(
                    ToolCall(
                        call_id="approve-direct-1",
                        name="mutate",
                        arguments={"value": "ok"},
                    ),
                )
            ),
            ModelResponse(text="done"),
        ]
    )
    runtime = CodeModeRuntime(
        platform=platform,
        store=FileAgentSessionStore(tmp_path / "state"),
        tools=ToolRegistry((tool,)),
        mcp_servers=(),
        auto_configure_browser=False,
        auto_configure_web_search=False,
    )
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    session = runtime.create_session(
        AGENT_FAST_ROLE.role_id,
        workspace_dir=workspace,
        permission_mode=PermissionMode.APPROVAL,
    )

    try:
        waiting = runtime.start_turn(session.session_id, "Run the mutation after approval.")
        assert waiting.status is AgentStatus.WAITING_APPROVAL
        assert called == []

        result = runtime.resume_approval(
            session.session_id,
            "approve-direct-1",
            approved=True,
        )

        assert result.status is AgentStatus.COMPLETED
        assert result.final_text == "done"
        assert called == ["ok"]
    finally:
        runtime.close()
