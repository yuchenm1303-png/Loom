from __future__ import annotations

from dataclasses import replace

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
from app.agent_runtime.permissions import (
    ApprovalPolicy,
    GranularApprovalConfig,
    SandboxPermissions,
)
from app.agent_runtime.sandbox_attempt import (
    SandboxAttemptKind,
    SandboxAttemptSelection,
    current_sandbox_attempt,
)
from app.agent_runtime.sandbox_failure import SandboxExecutionError, SandboxFailureKind


class ScriptedPlatform:
    def __init__(self, responses):
        self.responses = list(responses)

    def execute_chat(self, _profile_id, _request):
        if not self.responses:
            raise AssertionError("scripted platform ran out of responses")
        return self.responses.pop(0)


class PolicySandboxRuntime(SandboxAgentRuntime):
    def __init__(self, *args, test_approval_policy=None, granular=None, **kwargs):
        self._test_approval_policy = test_approval_policy
        self._test_granular = granular
        super().__init__(*args, **kwargs)

    def _build_step_context(self, session, *, next_model_step, step_id=None):
        step = super()._build_step_context(
            session,
            next_model_step=next_model_step,
            step_id=step_id,
        )
        if self._test_approval_policy is None:
            return step
        permissions = replace(
            step.permissions,
            approval_policy=self._test_approval_policy,
            granular_approval=self._test_granular,
        )
        return replace(step, permissions=permissions)


def _manager(policy=SandboxPolicy.AUTO) -> SandboxManager:
    return SandboxManager(
        policy=policy,
        bubblewrap_executable="/synthetic/bwrap",
        probe_backend=False,
        system_name="Linux",
    )


def _call(**extra) -> ToolCall:
    arguments = {"argv": ["synthetic-program", "same-action"]}
    arguments.update(extra)
    return ToolCall(call_id="exec-1", name="exec", arguments=arguments)


def _runtime(
    tmp_path,
    handler,
    final_text: str,
    *,
    call: ToolCall | None = None,
    approval_policy=None,
    granular=None,
    sandbox_policy=SandboxPolicy.AUTO,
) -> SandboxAgentRuntime:
    tool = AgentTool(
        name="exec",
        description="Synthetic direct-argv exec used to test sandbox orchestration.",
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
    return PolicySandboxRuntime(
        platform=ScriptedPlatform(
            (
                ModelResponse(tool_calls=(call or _call(),)),
                ModelResponse(text=final_text),
            )
        ),
        store=FileAgentSessionStore(tmp_path / "state"),
        tools=ToolRegistry((tool,)),
        sandbox_manager=_manager(sandbox_policy),
        test_approval_policy=approval_policy,
        granular=granular,
    )


def _session(runtime: SandboxAgentRuntime, tmp_path):
    return runtime.create_session(
        AGENT_FAST_ROLE.role_id,
        workspace_dir=tmp_path,
        permission_mode=PermissionMode.WORKSPACE,
    )


def test_on_request_typed_denial_returns_to_model_without_automatic_full_escalation(tmp_path):
    attempts = []

    def handler(_context, _arguments):
        attempts.append(current_sandbox_attempt())
        raise SandboxExecutionError(
            SandboxFailureKind.DENIED,
            "synthetic containment rejection",
            escalatable=True,
        )

    runtime = _runtime(tmp_path, handler, "failure observed")
    try:
        session = _session(runtime, tmp_path)
        completed = runtime.start_turn(session.session_id, "Run the synthetic action.")

        assert completed.status is AgentStatus.COMPLETED
        assert completed.pending_approval is None
        assert completed.final_text == "failure observed"
        assert len(attempts) == 1
        assert attempts[0].kind is SandboxAttemptKind.INITIAL
        assert attempts[0].selection is SandboxAttemptSelection.POLICY
    finally:
        runtime.close()


def test_require_escalated_is_one_review_then_first_attempt_without_sandbox(tmp_path):
    attempts = []

    def handler(_context, _arguments):
        attempts.append(current_sandbox_attempt())
        return ToolResult(ok=True, content="approved full escalation")

    runtime = _runtime(
        tmp_path,
        handler,
        "done",
        call=_call(
            sandbox_permissions=SandboxPermissions.REQUIRE_ESCALATED.value,
            justification="Allow this command to run outside the sandbox?",
        ),
    )
    try:
        session = _session(runtime, tmp_path)
        waiting = runtime.start_turn(session.session_id, "Run the action.")

        assert waiting.status is AgentStatus.WAITING_APPROVAL
        assert waiting.pending_approval is not None
        assert waiting.pending_approval.kind is ApprovalKind.INITIAL
        assert attempts == []

        completed = runtime.resume_approval(session.session_id, "exec-1", approved=True)
        assert completed.status is AgentStatus.COMPLETED
        assert len(attempts) == 1
        assert attempts[0].kind is SandboxAttemptKind.INITIAL
        assert attempts[0].selection is SandboxAttemptSelection.NO_SANDBOX
    finally:
        runtime.close()


def test_unless_trusted_initial_approval_bypasses_retry_reapproval(tmp_path):
    attempts = []

    def handler(_context, _arguments):
        attempt = current_sandbox_attempt()
        attempts.append(attempt)
        if attempt.kind is SandboxAttemptKind.INITIAL:
            raise SandboxExecutionError(
                SandboxFailureKind.DENIED,
                "sandbox rejected the approved action",
                escalatable=True,
            )
        return ToolResult(ok=True, content="retry succeeded")

    runtime = _runtime(
        tmp_path,
        handler,
        "done",
        approval_policy=ApprovalPolicy.UNLESS_TRUSTED,
    )
    try:
        session = _session(runtime, tmp_path)
        waiting = runtime.start_turn(session.session_id, "Run the action.")
        assert waiting.status is AgentStatus.WAITING_APPROVAL
        assert waiting.pending_approval is not None
        assert waiting.pending_approval.kind is ApprovalKind.INITIAL
        assert attempts == []

        completed = runtime.resume_approval(session.session_id, "exec-1", approved=True)
        assert completed.status is AgentStatus.COMPLETED
        assert completed.pending_approval is None
        assert [attempt.kind for attempt in attempts] == [
            SandboxAttemptKind.INITIAL,
            SandboxAttemptKind.RETRY,
        ]
        assert attempts[1].selection is SandboxAttemptSelection.NO_SANDBOX
    finally:
        runtime.close()


def test_granular_retry_without_prior_approval_requires_fresh_review(monkeypatch, tmp_path):
    attempts = []
    monkeypatch.setenv("LOOM_GRANULAR_RETRY_ENV", "sampled")

    def handler(_context, _arguments):
        attempt = current_sandbox_attempt()
        attempts.append(attempt)
        if attempt.kind is SandboxAttemptKind.INITIAL:
            # The fresh retry review still belongs to the same sampled action.
            # Parent-process environment drift after the typed denial must not
            # change either its approval identity or the environment it executes.
            monkeypatch.setenv("LOOM_GRANULAR_RETRY_ENV", "changed-after-denial")
            raise SandboxExecutionError(
                SandboxFailureKind.DENIED,
                "sandbox rejected an otherwise allowed action",
                escalatable=True,
            )
        return ToolResult(ok=True, content="reviewed retry succeeded")

    runtime = _runtime(
        tmp_path,
        handler,
        "done",
        approval_policy=ApprovalPolicy.GRANULAR,
        granular=GranularApprovalConfig(sandbox_approval=True, rules=False),
    )
    try:
        session = _session(runtime, tmp_path)
        waiting = runtime.start_turn(session.session_id, "Run the action.")

        assert waiting.status is AgentStatus.WAITING_APPROVAL
        assert waiting.pending_approval is not None
        assert waiting.pending_approval.kind is ApprovalKind.SANDBOX_ESCALATION
        assert [attempt.kind for attempt in attempts] == [SandboxAttemptKind.INITIAL]

        completed = runtime.resume_approval(session.session_id, "exec-1", approved=True)
        assert completed.status is AgentStatus.COMPLETED
        assert [attempt.kind for attempt in attempts] == [
            SandboxAttemptKind.INITIAL,
            SandboxAttemptKind.RETRY,
        ]
    finally:
        runtime.close()


def test_second_typed_denial_is_returned_without_third_retry_or_review(tmp_path):
    attempts = []

    def handler(_context, _arguments):
        attempt = current_sandbox_attempt()
        attempts.append(attempt)
        raise SandboxExecutionError(
            SandboxFailureKind.DENIED,
            f"denied on attempt {attempt.index}",
            escalatable=True,
        )

    runtime = _runtime(
        tmp_path,
        handler,
        "failure observed",
        approval_policy=ApprovalPolicy.UNLESS_TRUSTED,
    )
    try:
        session = _session(runtime, tmp_path)
        waiting = runtime.start_turn(session.session_id, "Run the action.")
        assert waiting.status is AgentStatus.WAITING_APPROVAL

        completed = runtime.resume_approval(session.session_id, "exec-1", approved=True)
        assert completed.status is AgentStatus.COMPLETED
        assert completed.pending_approval is None
        assert [attempt.kind for attempt in attempts] == [
            SandboxAttemptKind.INITIAL,
            SandboxAttemptKind.RETRY,
        ]
    finally:
        runtime.close()


def test_required_policy_forbids_explicit_full_bypass(tmp_path):
    attempts = []

    def handler(_context, _arguments):
        attempts.append(current_sandbox_attempt())
        return ToolResult(ok=True, content="must not execute")

    runtime = _runtime(
        tmp_path,
        handler,
        "blocked observed",
        sandbox_policy=SandboxPolicy.REQUIRED,
        call=_call(
            sandbox_permissions=SandboxPermissions.REQUIRE_ESCALATED.value,
            justification="Allow this command outside containment?",
        ),
    )
    try:
        session = _session(runtime, tmp_path)
        completed = runtime.start_turn(session.session_id, "Run the action.")
        assert completed.status is AgentStatus.COMPLETED
        assert completed.pending_approval is None
        assert attempts == []
    finally:
        runtime.close()


def test_additional_permissions_request_stays_in_sandbox_attempt(tmp_path):
    attempts = []

    def handler(_context, _arguments):
        attempts.append(current_sandbox_attempt())
        return ToolResult(ok=True, content="scoped request observed")

    runtime = _runtime(
        tmp_path,
        handler,
        "done",
        call=_call(
            sandbox_permissions=SandboxPermissions.WITH_ADDITIONAL_PERMISSIONS.value,
            additional_permissions={"file_system": {"write": [str(tmp_path)]}},
        ),
    )
    try:
        session = _session(runtime, tmp_path)
        waiting = runtime.start_turn(session.session_id, "Run the action.")
        assert waiting.status is AgentStatus.WAITING_APPROVAL

        completed = runtime.resume_approval(session.session_id, "exec-1", approved=True)
        assert completed.status is AgentStatus.COMPLETED
        assert len(attempts) == 1
        assert attempts[0].selection is SandboxAttemptSelection.ADDITIONAL_PERMISSIONS
        assert attempts[0].additional_permissions is not None
    finally:
        runtime.close()


def test_plain_failed_tool_result_is_not_inferred_as_sandbox_denial(tmp_path):
    attempts = []

    def handler(_context, _arguments):
        attempts.append(current_sandbox_attempt())
        return ToolResult(
            ok=False,
            content="permission denied: ordinary stderr/exit failure",
            data={"returncode": 1, "stderr": "permission denied"},
        )

    runtime = _runtime(tmp_path, handler, "failure observed")
    try:
        session = _session(runtime, tmp_path)
        completed = runtime.start_turn(session.session_id, "Run the action.")
        assert completed.status is AgentStatus.COMPLETED
        assert completed.pending_approval is None
        assert len(attempts) == 1
    finally:
        runtime.close()


def _windows_failure(argv, *, returncode, stderr=""):
    """What exec returns when Windows MXC stopped the command (before typing)."""

    return ToolResult(
        ok=False,
        content=f"process=proc-1 status=exited backend=pipe\nsandbox=windows-mxc:workspace\nexit={returncode}",
        data={
            "argv": list(argv),
            "returncode": returncode,
            "stdout": "",
            "stderr": stderr,
            "sandbox": {"enforced": True, "backend": "windows-mxc"},
        },
    )


def _tool_result_text(runtime, session_id: str) -> str:
    session = runtime.store.load(session_id)
    return "\n".join(
        str(message.content)
        for message in session.messages
        if getattr(getattr(message, "role", None), "value", "") == "tool"
    )


def test_on_request_windows_startup_denial_asks_to_run_once_outside_the_sandbox(tmp_path, monkeypatch):
    from app.agent_runtime import sandbox_denial
    from app.agent_runtime.contracts import AgentEventKind

    monkeypatch.setattr(sandbox_denial, "_default_prep_command", lambda _denial: "")
    attempts = []

    def handler(_context, _arguments):
        attempt = current_sandbox_attempt()
        attempts.append(attempt)
        if attempt.kind is SandboxAttemptKind.INITIAL:
            return _windows_failure(["git.exe", "status"], returncode=3221225794)
        return ToolResult(ok=True, content="ran outside the sandbox")

    runtime = _runtime(tmp_path, handler, "done")
    try:
        session = _session(runtime, tmp_path)
        waiting = runtime.start_turn(session.session_id, "Run the action.")

        # Workspace mode (on-request) used to hand the model a bare 3221225794.
        assert waiting.status is AgentStatus.WAITING_APPROVAL
        pending = waiting.pending_approval
        assert pending is not None
        assert pending.kind is ApprovalKind.SANDBOX_ESCALATION
        assert "could not start this command" in pending.reason
        assert pending.retry_reason.startswith("Sandbox denied: git.exe could not start inside the Windows sandbox")
        requested = [
            event for event in runtime.store.events(session.session_id)
            if event.kind is AgentEventKind.TOOL_APPROVAL_REQUIRED
        ]
        assert requested[-1].data["sandbox_denial"]["kind"] == "dll_init"
        assert requested[-1].data["sandbox_denial"]["startup"] is True

        completed = runtime.resume_approval(session.session_id, "exec-1", approved=True)
        assert completed.status is AgentStatus.COMPLETED
        assert [attempt.kind for attempt in attempts] == [SandboxAttemptKind.INITIAL, SandboxAttemptKind.RETRY]
        assert attempts[1].selection is SandboxAttemptSelection.NO_SANDBOX
    finally:
        runtime.close()


def test_an_unattended_session_gets_a_startup_denial_back_instead_of_asking(tmp_path, monkeypatch):
    # A persistent project: nobody watches its threads, so the question would stall it.
    from app.agent_runtime import sandbox_denial

    monkeypatch.setattr(sandbox_denial, "_default_prep_command", lambda _denial: "")
    attempts = []

    def handler(_context, _arguments):
        attempts.append(current_sandbox_attempt())
        return _windows_failure(["git.exe", "status"], returncode=3221225794)

    runtime = _runtime(tmp_path, handler, "reported")
    try:
        session = _session(runtime, tmp_path)
        runtime.unattended_sessions = {session.session_id}
        completed = runtime.start_turn(session.session_id, "Run the action.")

        assert completed.status is AgentStatus.COMPLETED
        assert len(attempts) == 1  # never retried outside the sandbox
        assert "Sandbox denied: git.exe could not start inside the Windows sandbox" in _tool_result_text(runtime, session.session_id)
    finally:
        runtime.close()


def test_never_policy_explains_a_windows_startup_denial_without_retrying(tmp_path, monkeypatch):
    from app.agent_runtime import sandbox_denial

    monkeypatch.setattr(sandbox_denial, "_default_prep_command", lambda _denial: "")
    attempts = []

    def handler(_context, _arguments):
        attempts.append(current_sandbox_attempt())
        return _windows_failure(
            ["git.exe", "status"],
            returncode=128,
            stderr="fatal: could not open '/dev/null' for reading and writing: Permission denied",
        )

    runtime = _runtime(tmp_path, handler, "reported", approval_policy=ApprovalPolicy.NEVER)
    try:
        session = _session(runtime, tmp_path)
        completed = runtime.start_turn(session.session_id, "Run the action.")

        assert completed.status is AgentStatus.COMPLETED
        assert len(attempts) == 1
        text = _tool_result_text(runtime, session.session_id)
        assert "Sandbox denied: git.exe could not open the NUL device" in text
        assert "does not allow running it outside the sandbox" in text
    finally:
        runtime.close()


def test_on_request_runtime_access_denial_tells_the_model_how_to_escalate(tmp_path, monkeypatch):
    from app.agent_runtime import sandbox_denial

    monkeypatch.setattr(sandbox_denial, "_default_prep_command", lambda _denial: "")
    attempts = []

    def handler(_context, _arguments):
        attempts.append(current_sandbox_attempt())
        return _windows_failure(["cmd.exe", "/c", "type", "x"], returncode=1, stderr="Access is denied.")

    runtime = _runtime(tmp_path, handler, "reported")
    try:
        session = _session(runtime, tmp_path)
        completed = runtime.start_turn(session.session_id, "Run the action.")

        # The command ran and hit a denial mid-way: Codex parity, no automatic
        # prompt; the model gets the reason and the escalation route.
        assert completed.status is AgentStatus.COMPLETED
        assert completed.pending_approval is None
        assert len(attempts) == 1
        text = _tool_result_text(runtime, session.session_id)
        assert "Sandbox denied: cmd.exe reported an access denial" in text
        assert "call exec again with sandbox_permissions=" in text
        assert "require_escalated" in text
    finally:
        runtime.close()
