from __future__ import annotations

import sys
from dataclasses import replace

import pytest

from app.ai import AGENT_FAST_ROLE, MessageRole, ModelResponse, ToolCall
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
from app.agent_runtime.execution_binding import binding_digest
from app.agent_runtime.permissions import permission_snapshot
from app.agent_runtime.process_tools import exec_tool
from app.agent_runtime.step import StepContext
from app.agent_runtime.tools import ToolRouter


class ScriptedPlatform:
    def __init__(self, responses=()):
        self.responses = list(responses)
        self.requests = []

    def execute_chat(self, _profile_id, request):
        self.requests.append(request)
        if not self.responses:
            raise AssertionError("scripted platform ran out of responses")
        return self.responses.pop(0)


def _synthetic_tool(name: str) -> AgentTool:
    if name == "exec":
        schema = {
            "type": "object",
            "properties": {
                "argv": {"type": "array", "items": {"type": "string"}, "minItems": 1},
            },
            "required": ["argv"],
            "additionalProperties": True,
        }
    else:
        schema = {
            "type": "object",
            "properties": {"label": {"type": "string"}},
            "required": ["label"],
            "additionalProperties": False,
        }
    return AgentTool(
        name=name,
        description="Synthetic sensitive tool for binding tests.",
        input_schema=schema,
        handler=lambda _context, arguments: ToolResult(
            ok=True,
            content=str(arguments.get("label") or arguments.get("argv") or ""),
        ),
        effect=ToolEffect.SENSITIVE,
    )


def _step(tool: AgentTool, workspace) -> StepContext:
    permissions = permission_snapshot(PermissionMode.APPROVAL)
    return StepContext.build(
        step_id="step-1",
        session_id="session-1",
        turn_id="turn-1",
        model_step=1,
        workspace_dir=str(workspace),
        profile_id="agent.fast",
        permission_mode=permissions.mode,
        permissions=permissions,
        tool_router=ToolRouter((tool,)),
    )


def _runtime(tmp_path, responses) -> SandboxAgentRuntime:
    return SandboxAgentRuntime(
        platform=ScriptedPlatform(responses),
        store=FileAgentSessionStore(tmp_path / "state"),
        tools=ToolRegistry((_synthetic_tool("exec"),)),
        sandbox_manager=SandboxManager(
            policy=SandboxPolicy.OFF,
            system_name="Linux",
            probe_backend=False,
        ),
    )


def test_sampled_step_freezes_exec_environment_binding(monkeypatch, tmp_path):
    exec_action = _synthetic_tool("exec")
    other_names = ("change", "exec_wait", "exec_write", "exec_resize")
    others = [(_synthetic_tool(name), name) for name in other_names]

    monkeypatch.setenv("LOOM_EXEC_BINDING_TEST", "before")
    exec_step = _step(exec_action, tmp_path)
    exec_before = binding_digest(exec_step, exec_action, object())
    other_before = {
        name: binding_digest(_step(tool, tmp_path), tool, object())
        for tool, name in others
    }

    monkeypatch.setenv("LOOM_EXEC_BINDING_TEST", "after")
    # Re-reading the same sampled Step must not turn a parent-process env edit
    # into a different reviewed action.
    assert binding_digest(exec_step, exec_action, object()) == exec_before

    # The next semantic Step captures the new environment instead.
    exec_after = binding_digest(_step(exec_action, tmp_path), exec_action, object())
    other_after = {
        name: binding_digest(_step(tool, tmp_path), tool, object())
        for tool, name in others
    }

    assert exec_before != exec_after
    assert other_before == other_after


def test_pending_exec_approval_executes_with_sampled_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("LOOM_STEP_ENV_TEST", "sampled")
    platform = ScriptedPlatform(
        (
            ModelResponse(
                tool_calls=(
                    ToolCall(
                        call_id="exec-1",
                        name="exec",
                        arguments={
                            "argv": [
                                sys.executable,
                                "-c",
                                "import os; print(os.environ.get('LOOM_STEP_ENV_TEST', 'missing'))",
                            ]
                        },
                    ),
                )
            ),
            ModelResponse(text="done"),
        )
    )
    runtime = SandboxAgentRuntime(
        platform=platform,
        store=FileAgentSessionStore(tmp_path / "state"),
        tools=ToolRegistry((exec_tool(),)),
        sandbox_manager=SandboxManager(
            policy=SandboxPolicy.OFF,
            system_name="Linux",
            probe_backend=False,
        ),
    )
    try:
        session = runtime.create_session(
            AGENT_FAST_ROLE.role_id,
            workspace_dir=tmp_path,
            permission_mode=PermissionMode.APPROVAL,
        )
        waiting = runtime.start_turn(session.session_id, "Read the sampled environment.")
        assert waiting.status is AgentStatus.WAITING_APPROVAL

        # This edit belongs to the next Step, not to the action already sampled
        # and under review.
        monkeypatch.setenv("LOOM_STEP_ENV_TEST", "changed-after-sampling")

        completed = runtime.resume_approval(session.session_id, "exec-1", approved=True)

        assert completed.status is AgentStatus.COMPLETED
        stored = runtime.store.load(session.session_id)
        tool_messages = [message for message in stored.messages if message.role is MessageRole.TOOL]
        assert tool_messages
        assert "sampled" in str(tool_messages[-1].content)
        assert "changed-after-sampling" not in str(tool_messages[-1].content)
    finally:
        runtime.close()


def test_approval_arguments_must_match_queued_call(tmp_path):
    runtime = _runtime(
        tmp_path,
        (
            ModelResponse(
                tool_calls=(
                    ToolCall(
                        call_id="exec-1",
                        name="exec",
                        arguments={"argv": ["synthetic-program", "first"]},
                    ),
                )
            ),
        ),
    )
    try:
        session = runtime.create_session(
            AGENT_FAST_ROLE.role_id,
            workspace_dir=tmp_path,
            permission_mode=PermissionMode.APPROVAL,
        )
        waiting = runtime.start_turn(session.session_id, "Use the synthetic action.")
        assert waiting.status is AgentStatus.WAITING_APPROVAL

        persisted = runtime.store.load(session.session_id)
        assert persisted.pending_approval is not None
        persisted.pending_approval = replace(
            persisted.pending_approval,
            arguments={"argv": ["synthetic-program", "second"]},
        )
        runtime.store.save(persisted)

        with pytest.raises(ValueError, match="approved tool action changed"):
            runtime.resume_approval(session.session_id, "exec-1", approved=True)
    finally:
        runtime.close()
