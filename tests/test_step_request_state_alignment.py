from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from app.ai import AGENT_FAST_ROLE, AIMessage, MessageRole, ModelResponse, ToolCall
from app.agent_runtime.context_runtime import ContextAgentRuntime
from app.agent_runtime.contracts import AgentStatus, PermissionMode, ToolEffect
from app.agent_runtime.execution_binding import binding_digest
from app.agent_runtime.runtime import CancellationToken
from app.agent_runtime.sandbox import SandboxManager, SandboxPolicy
from app.agent_runtime.sandbox_runtime import SandboxAgentRuntime
from app.agent_runtime.step import RequestStateSnapshot, StepContext
from app.agent_runtime.storage import FileAgentSessionStore
from app.agent_runtime.tools import AgentTool, ToolRegistry, ToolResult, ToolRouter


class ScriptedPlatform:
    def __init__(self, responses=()):
        self.responses = list(responses)

    def execute_chat(self, _profile_id, _request):
        if not self.responses:
            raise AssertionError("scripted platform ran out of responses")
        return self.responses.pop(0)


def _mutating_tool() -> AgentTool:
    return AgentTool(
        name="change",
        description="Change something for a test.",
        input_schema={"type": "object", "properties": {}, "additionalProperties": False},
        handler=lambda _context, _arguments: ToolResult(ok=True, content="changed"),
        effect=ToolEffect.MUTATING,
    )


def _sandbox_off() -> SandboxManager:
    return SandboxManager(
        policy=SandboxPolicy.OFF,
        system_name="Linux",
        probe_backend=False,
    )


def test_request_state_snapshot_is_immutable_and_changes_binding_identity(tmp_path):
    tool = _mutating_tool()
    router = ToolRouter((tool,))
    before = RequestStateSnapshot.build(
        system_prompt="system",
        project_instructions="before",
        communication_language="zh",
        model_profile={"profile_id": "agent.fast", "model": "model-a"},
    )
    after = RequestStateSnapshot.build(
        system_prompt="system",
        project_instructions="after",
        communication_language="zh",
        model_profile={"profile_id": "agent.fast", "model": "model-a"},
    )
    step_before = StepContext.build(
        step_id="step-1",
        session_id="session-1",
        turn_id="turn-1",
        model_step=1,
        workspace_dir=str(tmp_path),
        profile_id="agent.fast",
        permission_mode=PermissionMode.APPROVAL,
        tool_router=router,
        request_state=before,
    )
    step_after = StepContext.build(
        step_id="step-1",
        session_id="session-1",
        turn_id="turn-1",
        model_step=1,
        workspace_dir=str(tmp_path),
        profile_id="agent.fast",
        permission_mode=PermissionMode.APPROVAL,
        tool_router=router,
        request_state=after,
    )

    with pytest.raises(FrozenInstanceError):
        step_before.request_state.project_instructions = "mutated"  # type: ignore[misc]

    assert before.digest() != after.digest()
    assert binding_digest(step_before, tool, object()) != binding_digest(step_after, tool, object())


def test_default_sandbox_runtime_captures_project_language_and_context_budget(tmp_path):
    workspace = tmp_path / "project"
    workspace.mkdir()
    (workspace / "AGENTS.md").write_text("Use the original project rule.\n", encoding="utf-8")
    runtime = SandboxAgentRuntime(
        platform=ScriptedPlatform(),
        store=FileAgentSessionStore(tmp_path / "state"),
        tools=ToolRegistry((_mutating_tool(),)),
        sandbox_manager=_sandbox_off(),
    )
    session = runtime.create_session(
        AGENT_FAST_ROLE.role_id,
        workspace_dir=workspace,
        permission_mode=PermissionMode.APPROVAL,
    )
    session.messages.append(
        AIMessage(role=MessageRole.USER, content="请继续处理这个项目。")
    )

    step = runtime._build_step_context(session, next_model_step=True)
    (workspace / "AGENTS.md").write_text("Use a changed project rule.\n", encoding="utf-8")

    assert step.request_state.captured is True
    assert "original project rule" in step.request_state.project_instructions
    assert "changed project rule" not in step.request_state.project_instructions
    assert step.request_state.communication_language == "zh"
    assert step.request_state.context_limits is not None
    assert step.request_state.context_limits.input_budget_tokens > 0


def test_context_request_uses_frozen_system_instructions_language_and_limits(tmp_path):
    workspace = tmp_path / "project"
    workspace.mkdir()
    agents = workspace / "AGENTS.md"
    agents.write_text("Original project guidance.\n", encoding="utf-8")
    runtime = ContextAgentRuntime(
        platform=ScriptedPlatform(),
        store=FileAgentSessionStore(tmp_path / "state"),
        tools=ToolRegistry((_mutating_tool(),)),
        sandbox_manager=_sandbox_off(),
    )
    session = runtime.create_session(
        AGENT_FAST_ROLE.role_id,
        system_prompt="ORIGINAL SYSTEM PROMPT",
        workspace_dir=workspace,
        permission_mode=PermissionMode.APPROVAL,
    )
    session.messages.append(AIMessage(role=MessageRole.USER, content="请继续完成这个修改。"))
    step = runtime._build_step_context(session, next_model_step=True)

    agents.write_text("Changed project guidance.\n", encoding="utf-8")
    session.system_prompt = "CHANGED LIVE SYSTEM PROMPT"
    session.communication_language = "latin"

    messages, metadata = runtime._prepare_model_request(
        session,
        step,
        CancellationToken(),
    )
    rendered = "\n".join(
        str(message.content)
        for message in messages
        if isinstance(message.content, str)
    )

    assert "ORIGINAL SYSTEM PROMPT" in rendered
    assert "CHANGED LIVE SYSTEM PROMPT" not in rendered
    assert "Original project guidance" in rendered
    assert "Changed project guidance" not in rendered
    assert "Current user communication language: Chinese." in rendered
    assert metadata["context_limits"] == step.request_state.context_limits.as_dict()


def test_approval_resume_fails_closed_when_project_instructions_change(tmp_path):
    workspace = tmp_path / "project"
    workspace.mkdir()
    agents = workspace / "AGENTS.md"
    agents.write_text("Original execution rule.\n", encoding="utf-8")
    platform = ScriptedPlatform(
        (
            ModelResponse(
                tool_calls=(ToolCall(call_id="change-1", name="change", arguments={}),)
            ),
            ModelResponse(text="done"),
        )
    )
    runtime = ContextAgentRuntime(
        platform=platform,
        store=FileAgentSessionStore(tmp_path / "state"),
        tools=ToolRegistry((_mutating_tool(),)),
        sandbox_manager=_sandbox_off(),
    )
    session = runtime.create_session(
        AGENT_FAST_ROLE.role_id,
        workspace_dir=workspace,
        permission_mode=PermissionMode.APPROVAL,
    )

    waiting = runtime.start_turn(session.session_id, "Make the change.")
    assert waiting.status is AgentStatus.WAITING_APPROVAL

    agents.write_text("Changed execution rule.\n", encoding="utf-8")

    with pytest.raises(ValueError, match="approval binding changed"):
        runtime.resume_approval(session.session_id, "change-1", approved=True)
