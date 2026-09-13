from __future__ import annotations

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
from app.agent_runtime.sandbox_attempt import SandboxAttemptKind, current_sandbox_attempt
from app.agent_runtime.sandbox_failure import SandboxExecutionError, SandboxFailureKind


class ScriptedPlatform:
    def __init__(self, responses):
        self.responses = list(responses)

    def execute_chat(self, _profile_id, _request):
        if not self.responses:
            raise AssertionError("scripted platform ran out of responses")
        return self.responses.pop(0)


def test_action_binding_survives_initial_approval_and_one_sandbox_escalation(tmp_path):
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
        return ToolResult(ok=True, content="escalated action succeeded")

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
    runtime = SandboxAgentRuntime(
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
    try:
        session = runtime.create_session(
            AGENT_FAST_ROLE.role_id,
            workspace_dir=tmp_path,
            permission_mode=PermissionMode.WORKSPACE,
        )

        initial = runtime.start_turn(session.session_id, "Run it.")
        assert initial.status is AgentStatus.WAITING_APPROVAL
        assert initial.pending_approval is not None
        assert initial.pending_approval.kind is ApprovalKind.INITIAL
        assert attempts == []

        stored = runtime.store.load(session.session_id)
        original_binding = stored.pending_bindings[call.call_id]

        escalated = runtime.resume_approval(
            session.session_id,
            call.call_id,
            approved=True,
        )
        assert escalated.status is AgentStatus.WAITING_APPROVAL
        assert escalated.pending_approval is not None
        assert escalated.pending_approval.kind is ApprovalKind.SANDBOX_ESCALATION
        assert attempts == [SandboxAttemptKind.INITIAL]

        stored = runtime.store.load(session.session_id)
        assert stored.pending_bindings[call.call_id] == original_binding

        completed = runtime.resume_approval(
            session.session_id,
            call.call_id,
            approved=True,
        )
        assert completed.status is AgentStatus.COMPLETED
        assert completed.final_text == "done"
        assert attempts == [
            SandboxAttemptKind.INITIAL,
            SandboxAttemptKind.ESCALATION,
        ]
    finally:
        runtime.close()
