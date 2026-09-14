from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from app.ai import (
    AGENT_FAST_ROLE,
    AIMessage,
    MessageRole,
    ModelResponse,
    ReasoningKind,
    ReasoningRequest,
    ToolCall,
)
from app.agent_runtime.context_runtime import ContextAgentRuntime
from app.agent_runtime.contracts import AgentStatus, PermissionMode, ToolEffect
from app.agent_runtime.execution_binding import binding_digest
from app.agent_runtime.runtime import AgentRuntime, CancellationToken
from app.agent_runtime.sandbox import SandboxManager, SandboxPolicy
from app.agent_runtime.sandbox_runtime import SandboxAgentRuntime
from app.agent_runtime.step import RequestStateSnapshot, StepContext
from app.agent_runtime.storage import FileAgentSessionStore
from app.agent_runtime.tools import AgentTool, ToolRegistry, ToolResult, ToolRouter


class ScriptedPlatform:
    def __init__(self, responses=()):
        self.responses = list(responses)
        self.profile_ids: list[str] = []
        self.requests = []

    def execute_chat(self, profile_id, request):
        self.profile_ids.append(str(profile_id))
        self.requests.append(request)
        if not self.responses:
            raise AssertionError("scripted platform ran out of responses")
        return self.responses.pop(0)


def _mutating_tool(
    executions: list[str] | None = None,
    *,
    label: str = "changed",
    binding_key: str = "",
) -> AgentTool:
    def handler(_context, _arguments):
        if executions is not None:
            executions.append(label)
        return ToolResult(ok=True, content=label)

    return AgentTool(
        name="change",
        description="Change something for a test.",
        input_schema={"type": "object", "properties": {}, "additionalProperties": False},
        handler=handler,
        effect=ToolEffect.MUTATING,
        binding_key=binding_key,
    )


def _read_only_tool(name: str) -> AgentTool:
    return AgentTool(
        name=name,
        description=f"Read-only {name} test tool.",
        input_schema={"type": "object", "properties": {}, "additionalProperties": False},
        handler=lambda _context, _arguments: ToolResult(ok=True, content=name),
        effect=ToolEffect.READ_ONLY,
    )


def _sandbox_off() -> SandboxManager:
    return SandboxManager(
        policy=SandboxPolicy.OFF,
        system_name="Linux",
        probe_backend=False,
    )


def _render_request(request) -> str:
    return "\n".join(
        str(message.content)
        for message in request.messages
        if isinstance(message.content, str)
    )


class RecordingContextRuntime(ContextAgentRuntime):
    def __init__(self, *args, **kwargs):
        self.captured_steps = []
        self.executed_steps = []
        super().__init__(*args, **kwargs)

    def _capture_step_context(self, session, *, next_model_step, step_id=None):
        step = super()._capture_step_context(
            session,
            next_model_step=next_model_step,
            step_id=step_id,
        )
        self.captured_steps.append(step)
        return step

    def _execute_prepared_tool(
        self,
        session,
        prepared,
        *,
        token,
        step,
        approval_granted: bool = False,
    ):
        self.executed_steps.append(step)
        return super()._execute_prepared_tool(
            session,
            prepared,
            token=token,
            step=step,
            approval_granted=approval_granted,
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


def test_core_runtime_captures_old_step_and_new_step_sees_live_updates(tmp_path):
    workspace = tmp_path / "project"
    workspace.mkdir()
    agents = workspace / "AGENTS.md"
    agents.write_text("Original core rule.\n", encoding="utf-8")
    runtime = AgentRuntime(
        platform=ScriptedPlatform(),
        store=FileAgentSessionStore(tmp_path / "state"),
        tools=ToolRegistry((_mutating_tool(),)),
    )
    runtime.reasoning = ReasoningRequest(ReasoningKind.OPENAI_EFFORT, "low")
    session = runtime.create_session(
        AGENT_FAST_ROLE.role_id,
        workspace_dir=workspace,
        permission_mode=PermissionMode.APPROVAL,
    )
    session.current_turn_id = "turn-1"

    old_step = runtime._capture_step_context(session, next_model_step=True)
    agents.write_text("Updated core rule.\n", encoding="utf-8")
    runtime.reasoning = ReasoningRequest(ReasoningKind.OPENAI_EFFORT, "high")
    runtime.tools.register(_read_only_tool("new_capability"))
    new_step = runtime._capture_step_context(session, next_model_step=True)

    assert "Original core rule" in old_step.request_state.project_instructions
    assert "Updated core rule" not in old_step.request_state.project_instructions
    assert old_step.reasoning is not None and old_step.reasoning.value == "low"
    assert old_step.tool_router.get("new_capability") is None

    assert "Updated core rule" in new_step.request_state.project_instructions
    assert new_step.reasoning is not None and new_step.reasoning.value == "high"
    assert new_step.tool_router.get("new_capability") is not None
    assert old_step is not new_step


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


def test_same_sampled_tool_calls_share_step_and_next_sampling_gets_new_step(tmp_path):
    platform = ScriptedPlatform(
        (
            ModelResponse(
                tool_calls=(
                    ToolCall(call_id="read-a", name="read_a", arguments={}),
                    ToolCall(call_id="read-b", name="read_b", arguments={}),
                )
            ),
            ModelResponse(text="done"),
        )
    )
    runtime = RecordingContextRuntime(
        platform=platform,
        store=FileAgentSessionStore(tmp_path / "state"),
        tools=ToolRegistry((_read_only_tool("read_a"), _read_only_tool("read_b"))),
        sandbox_manager=_sandbox_off(),
    )
    session = runtime.create_session(
        AGENT_FAST_ROLE.role_id,
        workspace_dir=tmp_path,
        permission_mode=PermissionMode.APPROVAL,
    )

    result = runtime.start_turn(session.session_id, "Inspect both values.")

    assert result.status is AgentStatus.COMPLETED
    assert len(runtime.executed_steps) == 2
    assert runtime.executed_steps[0] is runtime.executed_steps[1]
    assert len(runtime.captured_steps) == 2
    assert runtime.captured_steps[0] is runtime.executed_steps[0]
    assert runtime.captured_steps[1] is not runtime.captured_steps[0]


def test_approval_resume_reuses_sampled_step_across_live_drift(tmp_path):
    workspace = tmp_path / "project"
    workspace.mkdir()
    agents = workspace / "AGENTS.md"
    agents.write_text("Original execution rule.\n", encoding="utf-8")
    executions: list[str] = []
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
        tools=ToolRegistry((
            _mutating_tool(executions, label="old-handler", binding_key="old-binding"),
        )),
        sandbox_manager=_sandbox_off(),
    )
    session = runtime.create_session(
        AGENT_FAST_ROLE.role_id,
        workspace_dir=workspace,
        permission_mode=PermissionMode.APPROVAL,
    )

    waiting = runtime.start_turn(session.session_id, "Make the change.")
    assert waiting.status is AgentStatus.WAITING_APPROVAL
    waiting_session = runtime.store.load(session.session_id)
    captured = runtime._captured_step_context(waiting_session)

    agents.write_text("Changed execution rule.\n", encoding="utf-8")
    # Simulate live registry drift. The sampled response must still execute the
    # exact handler/router that was advertised in its captured StepContext.
    runtime.tools._tools["change"] = _mutating_tool(  # noqa: SLF001 - contract test
        executions,
        label="new-handler",
        binding_key="new-binding",
    )

    resumed = runtime.resume_approval(session.session_id, "change-1", approved=True)

    assert resumed.status is AgentStatus.COMPLETED
    assert executions == ["old-handler"]
    assert "Original execution rule" in _render_request(platform.requests[0])
    # Approval continuation remains inside the sampled Step. A new Step is not
    # captured until the next semantic sampling boundary, so live AGENTS.md drift
    # cannot rewrite the authority of the follow-up request for this response.
    assert "Original execution rule" in _render_request(platform.requests[1])
    assert "Changed execution rule" not in _render_request(platform.requests[1])
    with pytest.raises(RuntimeError, match="captured step context is unavailable"):
        runtime._captured_step_context(
            runtime.store.load(session.session_id),
            step_id=captured.step_id,
        )


def test_approval_resume_fails_closed_if_captured_step_was_lost(tmp_path):
    executions: list[str] = []
    platform = ScriptedPlatform((
        ModelResponse(tool_calls=(ToolCall(call_id="change-1", name="change", arguments={}),)),
    ))
    runtime = ContextAgentRuntime(
        platform=platform,
        store=FileAgentSessionStore(tmp_path / "state"),
        tools=ToolRegistry((_mutating_tool(executions),)),
        sandbox_manager=_sandbox_off(),
    )
    session = runtime.create_session(
        AGENT_FAST_ROLE.role_id,
        workspace_dir=tmp_path,
        permission_mode=PermissionMode.APPROVAL,
    )
    waiting = runtime.start_turn(session.session_id, "Make the change.")
    assert waiting.status is AgentStatus.WAITING_APPROVAL

    with runtime._captured_steps_guard:
        runtime._captured_steps.clear()

    with pytest.raises(RuntimeError, match="captured step context is unavailable"):
        runtime.resume_approval(session.session_id, "change-1", approved=True)
    assert executions == []


def test_cancellation_clears_pending_step_and_prevents_tool_execution(tmp_path):
    executions: list[str] = []
    platform = ScriptedPlatform((
        ModelResponse(tool_calls=(ToolCall(call_id="change-1", name="change", arguments={}),)),
    ))
    runtime = ContextAgentRuntime(
        platform=platform,
        store=FileAgentSessionStore(tmp_path / "state"),
        tools=ToolRegistry((_mutating_tool(executions),)),
        sandbox_manager=_sandbox_off(),
    )
    session = runtime.create_session(
        AGENT_FAST_ROLE.role_id,
        workspace_dir=tmp_path,
        permission_mode=PermissionMode.APPROVAL,
    )
    waiting = runtime.start_turn(session.session_id, "Make the change.")
    assert waiting.status is AgentStatus.WAITING_APPROVAL

    cancelled = runtime.cancel(session.session_id)

    assert cancelled.status is AgentStatus.CANCELLED
    assert executions == []
    assert not runtime._captured_steps
    with pytest.raises(RuntimeError, match="not waiting for approval"):
        runtime.resume_approval(session.session_id, "change-1", approved=True)
