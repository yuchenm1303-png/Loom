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


class DriftingCodeModeRuntime(CodeModeRuntime):
    """Simulate live durable permission drift after the sampled StepContext exists."""

    def _code_mode_router(self, session):
        router = super()._code_mode_router(session)
        session.permission_mode = PermissionMode.FULL_ACCESS
        return router


def test_nested_code_mode_tool_uses_sampled_step_permission_after_live_session_drift(
    tmp_path: Path,
):
    observed: list[tuple[str, str]] = []

    def observe(context: ToolContext, _arguments):
        snapshot = context.service("permission_snapshot")
        observed.append((context.permission_mode, snapshot.mode.value))
        return ToolResult(ok=True, content=context.permission_mode)

    tool = AgentTool(
        name="observe_permission",
        description="Record the permission authority seen by the nested tool context.",
        input_schema={"type": "object", "properties": {}, "additionalProperties": False},
        handler=observe,
        effect=ToolEffect.READ_ONLY,
    )
    platform = RecordingPlatform(
        [
            ModelResponse(
                tool_calls=(
                    ToolCall(
                        call_id="code-permission-drift-1",
                        name="code_mode",
                        arguments={
                            "code": "r = tools.observe_permission()\nemit(r['content'])"
                        },
                    ),
                )
            ),
            ModelResponse(text="done"),
        ]
    )
    runtime = DriftingCodeModeRuntime(
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
        result = runtime.start_turn(session.session_id, "Observe the sampled permission authority.")

        assert result.status is AgentStatus.COMPLETED
        assert result.final_text == "done"
        assert observed == [
            (PermissionMode.APPROVAL.value, PermissionMode.APPROVAL.value)
        ]
    finally:
        runtime.close()
