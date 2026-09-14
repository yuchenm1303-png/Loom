from __future__ import annotations

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
from app.agent_runtime.contracts import ApprovalKind
from app.agent_runtime.permissions import ApprovalPolicy, GranularApprovalConfig
from app.agent_runtime.sandbox_attempt import SandboxAttemptKind, current_sandbox_attempt
from app.agent_runtime.sandbox_failure import SandboxExecutionError, SandboxFailureKind


class ScriptedPlatform:
    def __init__(self, responses):
        self.responses = list(responses)

    def execute_chat(self, _profile_id, _request):
        if not self.responses:
            raise AssertionError("scripted platform ran out of responses")
        return self.responses.pop(0)


class GranularSandboxRuntime(SandboxAgentRuntime):
    def _build_step_context(self, session, *, next_model_step, step_id=None):
        step = super()._build_step_context(
            session,
            next_model_step=next_model_step,
            step_id=step_id,
        )
        permissions = replace(
            step.permissions,
            approval_policy=ApprovalPolicy.GRANULAR,
            granular_approval=GranularApprovalConfig(sandbox_approval=True, rules=False),
        )
        return replace(step, permissions=permissions)


def _runtime(tmp_path, handler):
    tool = AgentTool(
        name="exec",
        description="Synthetic exec with real exec-shaped approval arguments.",
        input_schema={
            "type": "object",
            "properties": {
                "argv": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 1,
                },
                "cwd": {"type": "string"},
                "env": {
                    "type": "object",
                    "additionalProperties": {"type": "string"},
                },
                "pty": {"type": "boolean"},
            },
            "required": ["argv"],
            "additionalProperties": True,
        },
        handler=handler,
        effect=ToolEffect.SENSITIVE,
    )
    call = ToolCall(
        call_id="exec-action-1",
        name="exec",
        arguments={
            "argv": ["synthetic-program", "--one"],
            "cwd": ".",
            "env": {"NAME": "one"},
            "pty": False,
        },
    )
    manager = SandboxManager(
        policy=SandboxPolicy.AUTO,
        bubblewrap_executable="/synthetic/bwrap",
        probe_backend=False,
        system_name="Linux",
    )
    runtime = GranularSandboxRuntime(
        platform=ScriptedPlatform(
            (
                ModelResponse(tool_calls=(call,)),
                ModelResponse(text="done"),
            )
        ),
        store=FileAgentSessionStore(tmp_path / "state"),
        tools=ToolRegistry((tool,)),
        sandbox_manager=manager,
    )
    session = runtime.create_session(
        AGENT_FAST_ROLE.role_id,
        workspace_dir=tmp_path,
        permission_mode=PermissionMode.WORKSPACE,
    )
    return runtime, session, call


def test_retry_review_keeps_the_original_durable_action_binding(tmp_path):
    attempts = []

    def handler(_context, _arguments):
        attempt = current_sandbox_attempt()
        attempts.append(attempt.kind)
        if attempt.kind is SandboxAttemptKind.INITIAL:
            raise SandboxExecutionError(
                SandboxFailureKind.DENIED,
                "synthetic proven containment rejection",
                escalatable=True,
            )
        return ToolResult(ok=True, content="reviewed retry succeeded")

    runtime, session, call = _runtime(tmp_path, handler)
    try:
        waiting = runtime.start_turn(session.session_id, "Run it.")
        assert waiting.status is AgentStatus.WAITING_APPROVAL
        assert waiting.pending_approval is not None
        assert waiting.pending_approval.kind is ApprovalKind.SANDBOX_ESCALATION
        assert attempts == [SandboxAttemptKind.INITIAL]

        stored = runtime.store.load(session.session_id)
        original_binding = stored.pending_bindings[call.call_id]

        completed = runtime.resume_approval(
            session.session_id,
            call.call_id,
            approved=True,
        )

        assert completed.status is AgentStatus.COMPLETED
        assert completed.final_text == "done"
        assert attempts == [SandboxAttemptKind.INITIAL, SandboxAttemptKind.RETRY]
        stored = runtime.store.load(session.session_id)
        assert stored.pending_bindings.get(call.call_id) in {None, original_binding}
    finally:
        runtime.close()


def test_retry_review_rejects_action_drift_before_second_attempt(tmp_path):
    attempts = []

    def handler(_context, _arguments):
        attempt = current_sandbox_attempt()
        attempts.append(attempt.kind)
        if attempt.kind is SandboxAttemptKind.INITIAL:
            raise SandboxExecutionError(
                SandboxFailureKind.DENIED,
                "synthetic proven containment rejection",
                escalatable=True,
            )
        return ToolResult(ok=True, content="must not run")

    runtime, session, call = _runtime(tmp_path, handler)
    try:
        waiting = runtime.start_turn(session.session_id, "Run it.")
        assert waiting.status is AgentStatus.WAITING_APPROVAL
        assert waiting.pending_approval is not None
        assert waiting.pending_approval.kind is ApprovalKind.SANDBOX_ESCALATION

        stored = runtime.store.load(session.session_id)
        stored.pending_tool_calls[0] = ToolCall(
            call_id=call.call_id,
            name="exec",
            arguments={
                **call.arguments,
                "argv": ["synthetic-program", "--changed-after-review"],
            },
        )
        runtime.store.save(stored)

        with pytest.raises(ValueError, match="changed|binding"):
            runtime.resume_approval(
                session.session_id,
                call.call_id,
                approved=True,
            )

        assert attempts == [SandboxAttemptKind.INITIAL]
    finally:
        runtime.close()
