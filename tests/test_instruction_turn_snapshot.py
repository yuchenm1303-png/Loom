from __future__ import annotations

from app.agent_runtime import (
    AgentStatus,
    AgentTool,
    FileAgentSessionStore,
    PermissionMode,
    SandboxManager,
    SandboxPolicy,
    ToolEffect,
    ToolExposure,
    ToolRegistry,
    ToolResult,
)
from app.agent_runtime.context_runtime import ContextAgentRuntime
from app.ai import AIMessage, MessageRole, ModelResponse, ToolCall


class ScriptedPlatform:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def execute_chat(self, profile_id, request):
        self.requests.append((profile_id, request))
        if not self.responses:
            raise AssertionError("scripted platform ran out of responses")
        return self.responses.pop(0)


def _sensitive_tool(calls: list[str]) -> AgentTool:
    def handler(_context, arguments):
        calls.append(str(arguments.get("value") or ""))
        return ToolResult(ok=True, content="approved")

    return AgentTool(
        name="sensitive_action",
        description="Perform a sensitive test action.",
        input_schema={
            "type": "object",
            "properties": {"value": {"type": "string"}},
            "required": ["value"],
            "additionalProperties": False,
        },
        handler=handler,
        effect=ToolEffect.SENSITIVE,
        exposure=ToolExposure.DIRECT,
    )


def _runtime(state_root, platform, tool):
    return ContextAgentRuntime(
        platform=platform,
        store=FileAgentSessionStore(state_root),
        tools=ToolRegistry((tool,)),
        sandbox_manager=SandboxManager(policy=SandboxPolicy.OFF),
    )


def _project_instructions(request) -> AIMessage:
    return next(
        message
        for message in request.messages
        if message.role is MessageRole.USER
        and message.name == "loom_project_instructions"
    )


def test_approval_resume_after_restart_reuses_turn_instruction_snapshot(tmp_path):
    workspace = tmp_path / "project"
    workspace.mkdir()
    (workspace / ".git").mkdir()
    agents = workspace / "AGENTS.md"
    agents.write_text("original turn instructions", encoding="utf-8")

    state_root = tmp_path / "state"
    calls: list[str] = []
    tool = _sensitive_tool(calls)
    first_platform = ScriptedPlatform(
        [
            ModelResponse(
                tool_calls=(
                    ToolCall(
                        call_id="sensitive-1",
                        name="sensitive_action",
                        arguments={"value": "one"},
                    ),
                )
            )
        ]
    )
    runtime1 = _runtime(state_root, first_platform, tool)
    session = runtime1.create_session(
        "agent.fast",
        workspace_dir=workspace,
        permission_mode=PermissionMode.APPROVAL,
    )

    waiting = runtime1.start_turn(session.session_id, "run the sensitive action")

    assert waiting.status is AgentStatus.WAITING_APPROVAL
    assert calls == []
    first_project = _project_instructions(first_platform.requests[0][1])
    assert "original turn instructions" in str(first_project.content)
    turn_id = runtime1.get_session(session.session_id).current_turn_id
    snapshot = runtime1.instruction_snapshot_store.load(session.session_id, turn_id)
    assert snapshot is not None
    assert "original turn instructions" in snapshot.rendered
    runtime1.close()

    agents.write_text("changed while waiting for approval", encoding="utf-8")

    second_platform = ScriptedPlatform([ModelResponse(text="done")])
    runtime2 = _runtime(state_root, second_platform, tool)
    try:
        completed = runtime2.resume_approval(
            session.session_id,
            "sensitive-1",
            approved=True,
        )

        assert completed.status is AgentStatus.COMPLETED
        assert calls == ["one"]
        assert len(second_platform.requests) == 1
        resumed_project = _project_instructions(second_platform.requests[0][1])
        assert "original turn instructions" in str(resumed_project.content)
        assert "changed while waiting for approval" not in str(resumed_project.content)
    finally:
        runtime2.close()
