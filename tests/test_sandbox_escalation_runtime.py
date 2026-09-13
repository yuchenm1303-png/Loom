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


def _manager() -> SandboxManager:
    return SandboxManager(
        policy=SandboxPolicy.AUTO,
        bubblewrap_executable="/synthetic/bwrap",
        probe_backend=False,
        system_name="Linux",
    )


def _runtime(tmp_path, handler, final_text: str) -> SandboxAgentRuntime:
    tool = AgentTool(
        name="exec",
        description="Synthetic exec used to test runtime sandbox attempts.",
        input_schema={
            "type": "object",
            "properties": {"label": {"type": "string"}},
            "required": ["label"],
            "additionalProperties": False,
        },
        handler=handler,
        effect=ToolEffect.READ_ONLY,
    )
    return SandboxAgentRuntime(
        platform=ScriptedPlatform(
            (
                ModelResponse(
                    tool_calls=(
                        ToolCall(
                            call_id="exec-1",
                            name="exec",
                            arguments={"label": "same-action"},
                        ),
                    )
                ),
                ModelResponse(text=final_text),
            )
        ),
        store=FileAgentSessionStore(tmp_path / "state"),
        tools=ToolRegistry((tool,)),
        sandbox_manager=_manager(),
    )


def _session(runtime: SandboxAgentRuntime, tmp_path):
    return runtime.create_session(
        AGENT_FAST_ROLE.role_id,
        workspace_dir=tmp_path,
        permission_mode=PermissionMode.WORKSPACE,
    )


def test_typed_denial_requests_one_sandbox_escalation_then_retries(tmp_path):
    attempts = []

    def handler(_context, _arguments):
        attempt = current_sandbox_attempt()
        attempts.append(attempt)
        if attempt.kind is SandboxAttemptKind.INITIAL:
            raise SandboxExecutionError(
                SandboxFailureKind.DENIED,
                "synthetic containment rejection",
                escalatable=True,
            )
        return ToolResult(ok=True, content="escalated attempt succeeded")

    runtime = _runtime(tmp_path, handler, "done")
    try:
        session = _session(runtime, tmp_path)
        waiting = runtime.start_turn(session.session_id, "Run the synthetic action.")

        assert waiting.status is AgentStatus.WAITING_APPROVAL
        assert waiting.pending_approval is not None
        assert waiting.pending_approval.kind is ApprovalKind.SANDBOX_ESCALATION
        assert waiting.pending_approval.retry_reason == "synthetic containment rejection"
        assert [attempt.kind for attempt in attempts] == [SandboxAttemptKind.INITIAL]

        completed = runtime.resume_approval(
            session.session_id,
            "exec-1",
            approved=True,
        )

        assert completed.status is AgentStatus.COMPLETED
        assert completed.final_text == "done"
        assert [attempt.kind for attempt in attempts] == [
            SandboxAttemptKind.INITIAL,
            SandboxAttemptKind.ESCALATION,
        ]
        assert attempts[1].index == 1
    finally:
        runtime.close()


def test_second_typed_denial_is_returned_to_model_without_third_approval(tmp_path):
    attempts = []

    def handler(_context, _arguments):
        attempt = current_sandbox_attempt()
        attempts.append(attempt)
        raise SandboxExecutionError(
            SandboxFailureKind.DENIED,
            f"denied on attempt {attempt.index}",
            escalatable=True,
        )

    runtime = _runtime(tmp_path, handler, "failure observed")
    try:
        session = _session(runtime, tmp_path)
        waiting = runtime.start_turn(session.session_id, "Run the synthetic action.")
        assert waiting.status is AgentStatus.WAITING_APPROVAL
        assert waiting.pending_approval is not None
        assert waiting.pending_approval.kind is ApprovalKind.SANDBOX_ESCALATION

        completed = runtime.resume_approval(
            session.session_id,
            "exec-1",
            approved=True,
        )

        assert completed.status is AgentStatus.COMPLETED
        assert completed.pending_approval is None
        assert completed.final_text == "failure observed"
        assert [attempt.kind for attempt in attempts] == [
            SandboxAttemptKind.INITIAL,
            SandboxAttemptKind.ESCALATION,
        ]
    finally:
        runtime.close()


def test_user_can_deny_sandbox_escalation_without_second_attempt(tmp_path):
    attempts = []

    def handler(_context, _arguments):
        attempt = current_sandbox_attempt()
        attempts.append(attempt)
        raise SandboxExecutionError(
            SandboxFailureKind.DENIED,
            "synthetic containment rejection",
            escalatable=True,
        )

    runtime = _runtime(tmp_path, handler, "denial respected")
    try:
        session = _session(runtime, tmp_path)
        waiting = runtime.start_turn(session.session_id, "Run the synthetic action.")
        assert waiting.status is AgentStatus.WAITING_APPROVAL

        completed = runtime.resume_approval(
            session.session_id,
            "exec-1",
            approved=False,
        )

        assert completed.status is AgentStatus.COMPLETED
        assert completed.final_text == "denial respected"
        assert [attempt.kind for attempt in attempts] == [SandboxAttemptKind.INITIAL]
    finally:
        runtime.close()


def test_escalation_scope_is_limited_to_the_approved_call(tmp_path):
    seen: list[tuple[str, SandboxAttemptKind]] = []

    def handler(_context, arguments):
        label = str(arguments["label"])
        attempt = current_sandbox_attempt()
        seen.append((label, attempt.kind))
        if label == "first" and attempt.kind is SandboxAttemptKind.INITIAL:
            raise SandboxExecutionError(
                SandboxFailureKind.DENIED,
                "first action needs escalation",
                escalatable=True,
            )
        return ToolResult(ok=True, content=f"completed:{label}")

    tool = AgentTool(
        name="exec",
        description="Synthetic exec used to verify attempt scope isolation.",
        input_schema={
            "type": "object",
            "properties": {"label": {"type": "string"}},
            "required": ["label"],
            "additionalProperties": False,
        },
        handler=handler,
        effect=ToolEffect.READ_ONLY,
    )
    runtime = SandboxAgentRuntime(
        platform=ScriptedPlatform(
            (
                ModelResponse(
                    tool_calls=(
                        ToolCall(call_id="exec-1", name="exec", arguments={"label": "first"}),
                    )
                ),
                ModelResponse(
                    tool_calls=(
                        ToolCall(call_id="exec-2", name="exec", arguments={"label": "second"}),
                    )
                ),
                ModelResponse(text="both complete"),
            )
        ),
        store=FileAgentSessionStore(tmp_path / "state"),
        tools=ToolRegistry((tool,)),
        sandbox_manager=_manager(),
    )
    try:
        session = _session(runtime, tmp_path)
        waiting = runtime.start_turn(session.session_id, "Run both synthetic actions.")
        assert waiting.status is AgentStatus.WAITING_APPROVAL
        assert waiting.pending_approval is not None
        assert waiting.pending_approval.kind is ApprovalKind.SANDBOX_ESCALATION

        completed = runtime.resume_approval(session.session_id, "exec-1", approved=True)

        assert completed.status is AgentStatus.COMPLETED
        assert completed.final_text == "both complete"
        assert seen == [
            ("first", SandboxAttemptKind.INITIAL),
            ("first", SandboxAttemptKind.ESCALATION),
            ("second", SandboxAttemptKind.INITIAL),
        ]
    finally:
        runtime.close()
