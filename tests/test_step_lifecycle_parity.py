from __future__ import annotations

from app.ai import AGENT_FAST_ROLE, MessageRole, ModelResponse, ReasoningKind, ReasoningRequest, ToolCall
from app.agent_runtime.context_runtime import ContextAgentRuntime
from app.agent_runtime.contracts import AgentStatus, PermissionMode, ToolEffect
from app.agent_runtime.process_runtime import ProcessStore
from app.agent_runtime.runtime import AgentRuntime
from app.agent_runtime.sandbox import SandboxManager, SandboxPolicy
from app.agent_runtime.shell_environment import ShellEnvironmentPolicy
from app.agent_runtime.storage import FileAgentSessionStore
from app.agent_runtime.tools import AgentTool, ToolRegistry, ToolResult


class ScriptedPlatform:
    def __init__(self, responses=()):
        self.responses = list(responses)
        self.requests = []

    def execute_chat(self, _profile_id, request):
        self.requests.append(request)
        if not self.responses:
            raise AssertionError("scripted platform ran out of responses")
        return self.responses.pop(0)


def _sandbox_off() -> SandboxManager:
    return SandboxManager(
        policy=SandboxPolicy.OFF,
        system_name="Linux",
        probe_backend=False,
    )


def test_captured_step_keeps_profile_permissions_and_environment_until_next_capture(tmp_path):
    process_store = ProcessStore(
        sandbox_manager=_sandbox_off(),
        environment_policy=ShellEnvironmentPolicy(inherit="core"),
    )
    runtime = AgentRuntime(
        platform=ScriptedPlatform(),
        store=FileAgentSessionStore(tmp_path / "state"),
        tools=ToolRegistry(),
        process_store=process_store,
    )
    runtime.reasoning = ReasoningRequest(ReasoningKind.OPENAI_EFFORT, "low")
    session = runtime.create_session(
        AGENT_FAST_ROLE.role_id,
        workspace_dir=tmp_path,
        permission_mode=PermissionMode.APPROVAL,
    )
    session.current_turn_id = "turn-1"

    old_step = runtime._capture_step_context(session, next_model_step=True)

    session.profile_id = "agent.changed"
    session.permission_mode = PermissionMode.FULL_ACCESS
    process_store.environment_policy = ShellEnvironmentPolicy(inherit="none")
    runtime.reasoning = ReasoningRequest(ReasoningKind.OPENAI_EFFORT, "high")
    new_step = runtime._capture_step_context(session, next_model_step=True)

    assert old_step.world_state.profile_id == AGENT_FAST_ROLE.role_id
    assert old_step.world_state.permission_mode is PermissionMode.APPROVAL
    assert old_step.environment_policy.inherit == "core"
    assert old_step.reasoning is not None and old_step.reasoning.value == "low"

    assert new_step.world_state.profile_id == "agent.changed"
    assert new_step.world_state.permission_mode is PermissionMode.FULL_ACCESS
    assert new_step.environment_policy.inherit == "none"
    assert new_step.reasoning is not None and new_step.reasoning.value == "high"


def test_invalid_tool_arguments_become_observation_and_turn_continues(tmp_path):
    executed: list[dict[str, object]] = []

    def handler(_context, arguments):
        executed.append(dict(arguments))
        return ToolResult(ok=True, content="unexpected execution")

    tool = AgentTool(
        name="inspect_path",
        description="Inspect one path.",
        input_schema={
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
            "additionalProperties": False,
        },
        handler=handler,
        effect=ToolEffect.READ_ONLY,
    )
    platform = ScriptedPlatform(
        (
            ModelResponse(
                tool_calls=(
                    ToolCall(
                        call_id="bad-1",
                        name="inspect_path",
                        arguments={"path": 7},
                    ),
                )
            ),
            ModelResponse(text="recovered"),
        )
    )
    runtime = ContextAgentRuntime(
        platform=platform,
        store=FileAgentSessionStore(tmp_path / "state"),
        tools=ToolRegistry((tool,)),
        sandbox_manager=_sandbox_off(),
    )
    session = runtime.create_session(
        AGENT_FAST_ROLE.role_id,
        workspace_dir=tmp_path,
        permission_mode=PermissionMode.APPROVAL,
    )

    result = runtime.start_turn(session.session_id, "Inspect the path.")

    assert result.status is AgentStatus.COMPLETED
    assert result.final_text == "recovered"
    assert executed == []
    stored = runtime.store.load(session.session_id)
    tool_messages = [message for message in stored.messages if message.role is MessageRole.TOOL]
    assert len(tool_messages) == 1
    assert "Invalid tool request:" in str(tool_messages[0].content)
    assert len(platform.requests) == 2
