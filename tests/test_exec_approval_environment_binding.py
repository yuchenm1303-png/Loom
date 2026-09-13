from __future__ import annotations

import os
from dataclasses import replace

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
from app.agent_runtime.execution_binding import binding_digest
from app.agent_runtime.permissions import permission_snapshot
from app.agent_runtime.step import StepContext
from app.agent_runtime.tools import ToolRouter


class ScriptedPlatform:
    def __init__(self, responses=()):
        self.responses = list(responses)

    def execute_chat(self, _profile_id, _request):
        if not self.responses:
            raise AssertionError("scripted platform ran out of responses")
        return self.responses.pop(0)


def _synthetic_tool(name: str) -> AgentTool:
    return AgentTool(
        name=name,
        description="Synthetic sensitive tool for binding tests.",
        input_schema={
            "type": "object",
            "properties": {"label": {"type": "string"}},
            "required": ["label"],
            "additionalProperties": False,
        },
        handler=lambda _context, arguments: ToolResult(
            ok=True,
            content=str(arguments.get("label") or ""),
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


def test_environment_change_only_changes_exec_binding(monkeypatch, tmp_path):
    exec_action = _synthetic_tool("exec")
    ordinary = _synthetic_tool("change")
    exec_step = _step(exec_action, tmp_path)
    ordinary_step = _step(ordinary, tmp_path)

    monkeypatch.setenv("LOOM_EXEC_BINDING_TEST", "before")
    exec_before = binding_digest(exec_step, exec_action, object())
    ordinary_before = binding_digest(ordinary_step, ordinary, object())

    monkeypatch.setenv("LOOM_EXEC_BINDING_TEST", "after")
    exec_after = binding_digest(exec_step, exec_action, object())
    ordinary_after = binding_digest(ordinary_step, ordinary, object())

    assert exec_before != exec_after
    assert ordinary_before == ordinary_after


def test_pending_exec_approval_rejects_changed_environment(monkeypatch, tmp_path):
    runtime = _runtime(
        tmp_path,
        (
            ModelResponse(
                tool_calls=(
                    ToolCall(
                        call_id="exec-1",
                        name="exec",
                        arguments={"label": "first"},
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

        monkeypatch.setenv(
            "PATH",
            os.environ.get("PATH", "") + os.pathsep + str(tmp_path / "binding-test"),
        )

        with pytest.raises(ValueError, match="approval binding changed"):
            runtime.resume_approval(session.session_id, "exec-1", approved=True)
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
                        arguments={"label": "first"},
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
            arguments={"label": "second"},
        )
        runtime.store.save(persisted)

        with pytest.raises(ValueError, match="approved tool action changed"):
            runtime.resume_approval(session.session_id, "exec-1", approved=True)
    finally:
        runtime.close()
