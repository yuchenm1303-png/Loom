from __future__ import annotations

from pathlib import Path

from app.agent_runtime import (
    AgentStatus,
    AgentTool,
    FileAgentSessionStore,
    PermissionMode,
    ToolExposure,
    ToolRegistry,
    ToolResult,
    ToolSearchRuntime,
)
from app.agent_runtime.contracts import ToolEffect
from app.ai import AGENT_FAST_ROLE, ModelResponse, ToolCall


class _Platform:
    def __init__(self, responses):
        self.responses = list(responses)

    def execute_chat(self, _profile_id, _request):
        if not self.responses:
            raise AssertionError("scripted platform ran out of responses")
        return self.responses.pop(0)


def test_approval_resume_executes_with_sampled_permission_mode(tmp_path: Path):
    observed_permission_modes: list[str] = []

    def handler(context, _arguments):
        observed_permission_modes.append(context.permission_mode)
        return ToolResult(ok=True, content="ok")

    sensitive = AgentTool(
        name="external.write_record",
        description="synthetic sensitive action",
        input_schema={
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
        handler=handler,
        effect=ToolEffect.SENSITIVE,
        exposure=ToolExposure.DIRECT,
    )
    platform = _Platform(
        (
            ModelResponse(
                tool_calls=(
                    ToolCall(
                        call_id="write-1",
                        name="external.write_record",
                        arguments={},
                    ),
                )
            ),
            ModelResponse(text="done"),
        )
    )
    store = FileAgentSessionStore(tmp_path / "state")
    runtime = ToolSearchRuntime(
        platform=platform,
        store=store,
        tools=ToolRegistry((sensitive,)),
        mcp_servers=(),
        auto_configure_browser=False,
        auto_configure_web_search=False,
    )
    try:
        session = runtime.create_session(
            AGENT_FAST_ROLE.role_id,
            workspace_dir=tmp_path,
            permission_mode=PermissionMode.APPROVAL,
        )
        waiting = runtime.start_turn(session.session_id, "Write the record.")
        assert waiting.status is AgentStatus.WAITING_APPROVAL
        assert observed_permission_modes == []

        # Simulate live durable session drift after the model sampled the action.
        # Approval execution must still use the immutable StepContext authority.
        live = store.load(session.session_id)
        live.permission_mode = PermissionMode.FULL_ACCESS
        store.save(live)

        completed = runtime.resume_approval(
            session.session_id,
            "write-1",
            approved=True,
        )

        assert completed.status is AgentStatus.COMPLETED
        assert completed.final_text == "done"
        assert observed_permission_modes == [PermissionMode.APPROVAL.value]
    finally:
        runtime.close()
