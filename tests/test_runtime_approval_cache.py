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
from app.agent_runtime.approval_actions import ReviewDecision
from app.agent_runtime.permissions import SandboxPermissions
from app.agent_runtime.sandbox_attempt import SandboxAttemptSelection, current_sandbox_attempt


class ScriptedPlatform:
    def __init__(self, responses):
        self.responses = list(responses)

    def execute_chat(self, _profile_id, _request):
        if not self.responses:
            raise AssertionError("scripted platform ran out of responses")
        return self.responses.pop(0)


def _call(call_id: str) -> ToolCall:
    return ToolCall(
        call_id=call_id,
        name="exec",
        arguments={
            "argv": ["synthetic-program", "same-command"],
            "sandbox_permissions": SandboxPermissions.REQUIRE_ESCALATED.value,
            "justification": "Synthetic full escalation for cache contract.",
        },
    )


def _runtime(tmp_path, attempts):
    def handler(_context, _arguments):
        attempts.append(current_sandbox_attempt())
        return ToolResult(ok=True, content="executed")

    tool = AgentTool(
        name="exec",
        description="Synthetic exec for session approval reuse.",
        input_schema={
            "type": "object",
            "properties": {
                "argv": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["argv"],
            "additionalProperties": False,
        },
        handler=handler,
        effect=ToolEffect.SENSITIVE,
    )
    return SandboxAgentRuntime(
        platform=ScriptedPlatform(
            (
                ModelResponse(tool_calls=(_call("exec-1"),)),
                ModelResponse(tool_calls=(_call("exec-2"),)),
                ModelResponse(text="done"),
            )
        ),
        store=FileAgentSessionStore(tmp_path / "state"),
        tools=ToolRegistry((tool,)),
        sandbox_manager=SandboxManager(
            policy=SandboxPolicy.AUTO,
            bubblewrap_executable="/synthetic/bwrap",
            probe_backend=False,
            system_name="Linux",
        ),
    )


def _session(runtime, tmp_path):
    return runtime.create_session(
        AGENT_FAST_ROLE.role_id,
        workspace_dir=tmp_path,
        permission_mode=PermissionMode.WORKSPACE,
    )


def test_approved_for_session_skips_equivalent_second_exec_review_in_same_turn(tmp_path):
    attempts = []
    runtime = _runtime(tmp_path, attempts)
    try:
        session = _session(runtime, tmp_path)
        waiting = runtime.start_turn(session.session_id, "Run the same approved action twice.")
        assert waiting.status is AgentStatus.WAITING_APPROVAL
        assert waiting.pending_approval is not None
        assert waiting.pending_approval.call_id == "exec-1"

        completed = runtime.resume_approval(
            session.session_id,
            "exec-1",
            approved=True,
            review_decision=ReviewDecision.APPROVED_FOR_SESSION,
        )

        assert completed.status is AgentStatus.COMPLETED
        assert completed.final_text == "done"
        assert len(attempts) == 2
        assert all(
            attempt.selection is SandboxAttemptSelection.NO_SANDBOX
            for attempt in attempts
        )
    finally:
        runtime.close()


def test_one_shot_approval_does_not_cache_equivalent_second_exec(tmp_path):
    attempts = []
    runtime = _runtime(tmp_path, attempts)
    try:
        session = _session(runtime, tmp_path)
        waiting = runtime.start_turn(session.session_id, "Run the same approved action twice.")
        assert waiting.status is AgentStatus.WAITING_APPROVAL

        second_wait = runtime.resume_approval(
            session.session_id,
            "exec-1",
            approved=True,
            review_decision=ReviewDecision.APPROVED,
        )

        assert second_wait.status is AgentStatus.WAITING_APPROVAL
        assert second_wait.pending_approval is not None
        assert second_wait.pending_approval.call_id == "exec-2"
        assert len(attempts) == 1
    finally:
        runtime.close()
