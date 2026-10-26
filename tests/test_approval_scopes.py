from __future__ import annotations

import time
from dataclasses import replace
from pathlib import Path

import pytest

from app.agent_runtime import (
    AgentEventKind,
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
from app.agent_runtime.approval_rules import PrefixRule, RuleSandbox
from app.agent_runtime.contracts import ApprovalKind
from app.agent_runtime.permissions import ApprovalPolicy
from app.agent_runtime.sandbox_attempt import (
    SandboxAttemptKind,
    SandboxAttemptSelection,
    current_sandbox_attempt,
)
from app.ai import AGENT_FAST_ROLE, ModelResponse, ToolCall
from app.app_server import LoomAppServerService


class ScriptedPlatform:
    def __init__(self, responses):
        self.responses = list(responses)

    def execute_chat(self, _profile_id, _request):
        if not self.responses:
            raise AssertionError("scripted platform ran out of responses")
        return self.responses.pop(0)


class PolicyRuntime(SandboxAgentRuntime):
    """SandboxAgentRuntime with the approval policy pinned, so prompts are predictable."""

    def __init__(self, *args, test_approval_policy=ApprovalPolicy.UNLESS_TRUSTED, **kwargs):
        self._test_approval_policy = test_approval_policy
        super().__init__(*args, **kwargs)

    def _build_step_context(self, session, *, next_model_step, step_id=None):
        step = super()._build_step_context(session, next_model_step=next_model_step, step_id=step_id)
        if self._test_approval_policy is None:
            return step
        return replace(step, permissions=replace(step.permissions, approval_policy=self._test_approval_policy))


def _manager(policy=SandboxPolicy.AUTO) -> SandboxManager:
    return SandboxManager(
        policy=policy,
        bubblewrap_executable="/synthetic/bwrap",
        probe_backend=False,
        system_name="Linux",
    )


def _exec(call_id: str, *argv: str, **extra) -> ToolCall:
    return ToolCall(call_id=call_id, name="exec", arguments={"argv": list(argv), **extra})


def _exec_tool(handler) -> AgentTool:
    return AgentTool(
        name="exec",
        description="Synthetic direct-argv exec used to test approval scopes.",
        input_schema={
            "type": "object",
            "properties": {
                "argv": {"type": "array", "items": {"type": "string"}},
                "env": {"type": "object"},
                "cwd": {"type": "string"},
            },
            "required": ["argv"],
            "additionalProperties": False,
        },
        handler=handler,
        effect=ToolEffect.SENSITIVE,
    )


def _plain_tool(name: str, calls: list[str]) -> AgentTool:
    def handler(_context, arguments):
        calls.append(f"{name}:{arguments.get('value', '')}")
        return ToolResult(ok=True, content=f"{name} ran")

    return AgentTool(
        name=name,
        description=f"Synthetic sensitive tool {name}.",
        input_schema={
            "type": "object",
            "properties": {"value": {"type": "string"}},
            "required": ["value"],
            "additionalProperties": False,
        },
        handler=handler,
        effect=ToolEffect.SENSITIVE,
    )


def _runtime(
    tmp_path: Path,
    tools,
    responses,
    *,
    approval_policy=ApprovalPolicy.UNLESS_TRUSTED,
    sandbox_policy=SandboxPolicy.AUTO,
    state: str = "state",
) -> PolicyRuntime:
    return PolicyRuntime(
        platform=ScriptedPlatform(responses),
        store=FileAgentSessionStore(tmp_path / state),
        tools=ToolRegistry(tuple(tools)),
        sandbox_manager=_manager(sandbox_policy),
        test_approval_policy=approval_policy,
    )


def _session(runtime, workspace: Path):
    return runtime.create_session(
        AGENT_FAST_ROLE.role_id,
        workspace_dir=workspace,
        permission_mode=PermissionMode.WORKSPACE,
    )


def _workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "workspace"
    workspace.mkdir(exist_ok=True)
    return workspace


def _approval_events(runtime, session_id):
    return [e for e in runtime.store.events(session_id) if e.kind is AgentEventKind.TOOL_APPROVAL_REQUIRED]


def _events(runtime, session_id, kind):
    return [e for e in runtime.store.events(session_id) if e.kind is kind]


def _tool_text(runtime, session_id: str) -> str:
    session = runtime.store.load(session_id)
    return "\n".join(
        str(m.content) for m in session.messages if getattr(getattr(m, "role", None), "value", "") == "tool"
    )


def _windows_denial(argv, returncode=3221225794):
    return ToolResult(
        ok=False,
        content=f"process=proc-1 status=exited backend=pipe\nsandbox=windows-mxc:workspace\nexit={returncode}",
        data={
            "argv": list(argv),
            "returncode": returncode,
            "stdout": "",
            "stderr": "",
            "sandbox": {"enforced": True, "backend": "windows-mxc"},
        },
    )


def test_the_card_offers_the_prefix_the_runtime_derived_from_the_waiting_call(tmp_path):
    runtime = _runtime(tmp_path, [_exec_tool(lambda *_: ToolResult(ok=True, content="ok"))],
                       [ModelResponse(tool_calls=(_exec("c1", "git", "status", "--short"),)), ModelResponse(text="done")])
    try:
        session = _session(runtime, _workspace(tmp_path))
        waiting = runtime.start_turn(session.session_id, "go")
        assert waiting.status is AgentStatus.WAITING_APPROVAL
        data = _approval_events(runtime, session.session_id)[-1].data
        assert data["proposed_prefix_rule"] == ["git", "status"]
        assert data["session_scope"] == {"kind": "prefix", "prefix": ["git", "status"]}
        assert data["rule_sandbox"] == "policy"
        assert data["can_persist_rule"] is True
    finally:
        runtime.close()


def test_a_command_with_no_safe_prefix_is_remembered_only_as_the_exact_command(tmp_path):
    ran = []
    runtime = _runtime(
        tmp_path,
        [_exec_tool(lambda _c, a: ran.append(list(a["argv"])) or ToolResult(ok=True, content="ok"))],
        [
            ModelResponse(tool_calls=(_exec("c1", "node", "build.js"),)),
            ModelResponse(tool_calls=(_exec("c2", "node", "build.js"), _exec("c3", "node", "other.js"))),
            ModelResponse(text="done"),
        ],
    )
    try:
        session = _session(runtime, _workspace(tmp_path))
        waiting = runtime.start_turn(session.session_id, "go")
        data = _approval_events(runtime, session.session_id)[-1].data
        # "node" alone would approve any script, so nothing is proposed to save.
        assert "proposed_prefix_rule" not in data
        assert data["session_scope"] == {"kind": "command"}

        again = runtime.resume_approval(
            session.session_id, "c1", approved=True, review_decision="approved_for_session"
        )
        # The identical command runs without asking; a different script still asks.
        assert again.status is AgentStatus.WAITING_APPROVAL
        assert again.pending_approval.call_id == "c3"
        assert ran == [["node", "build.js"], ["node", "build.js"]]
        assert waiting.pending_approval.call_id == "c1"
    finally:
        runtime.close()


def test_session_prefix_covers_the_same_subcommand_and_nothing_else(tmp_path):
    ran = []
    runtime = _runtime(
        tmp_path,
        [_exec_tool(lambda _c, a: ran.append(list(a["argv"])) or ToolResult(ok=True, content="ok"))],
        [
            ModelResponse(tool_calls=(_exec("c1", "git", "status"),)),
            ModelResponse(tool_calls=(_exec("c2", "git", "status", "--short"), _exec("c3", "git", "push"))),
            ModelResponse(text="done"),
        ],
    )
    try:
        session = _session(runtime, _workspace(tmp_path))
        runtime.start_turn(session.session_id, "go")
        result = runtime.resume_approval(
            session.session_id, "c1", approved=True, review_decision="approved_for_session"
        )

        assert ran == [["git", "status"], ["git", "status", "--short"]]
        assert result.status is AgentStatus.WAITING_APPROVAL
        assert result.pending_approval.call_id == "c3"
        approved = _events(runtime, session.session_id, AgentEventKind.TOOL_APPROVED)
        assert approved[-1].data["review"] == {
            "decision": "approved_for_session",
            "rule": {"scope": "session", "prefix": ["git", "status"], "sandbox": "policy"},
        }
    finally:
        runtime.close()


def test_session_rules_do_not_leak_into_another_session(tmp_path):
    runtime = _runtime(
        tmp_path,
        [_exec_tool(lambda *_: ToolResult(ok=True, content="ok"))],
        [
            ModelResponse(tool_calls=(_exec("c1", "git", "status"),)),
            ModelResponse(text="done"),
            ModelResponse(tool_calls=(_exec("c2", "git", "status"),)),
        ],
    )
    try:
        workspace = _workspace(tmp_path)
        first = _session(runtime, workspace)
        runtime.start_turn(first.session_id, "go")
        runtime.resume_approval(first.session_id, "c1", approved=True, review_decision="approved_for_session")

        second = _session(runtime, workspace)
        waiting = runtime.start_turn(second.session_id, "go")
        assert waiting.status is AgentStatus.WAITING_APPROVAL
    finally:
        runtime.close()


def test_project_rule_survives_a_restart_and_is_shared_by_new_sessions(tmp_path):
    workspace = _workspace(tmp_path)
    tool = _exec_tool(lambda *_: ToolResult(ok=True, content="ok"))
    first = _runtime(tmp_path, [tool], [ModelResponse(tool_calls=(_exec("c1", "git", "status"),)), ModelResponse(text="done")])
    try:
        session = _session(first, workspace)
        first.start_turn(session.session_id, "go")
        first.resume_approval(session.session_id, "c1", approved=True, review_decision="approved_with_prefix_rule")
        approved = _events(first, session.session_id, AgentEventKind.TOOL_APPROVED)
        assert approved[-1].data["review"]["rule"] == {
            "scope": "project", "prefix": ["git", "status"], "sandbox": "policy",
        }
    finally:
        first.close()

    # A fresh runtime over the same Loom home: no approval is asked any more.
    restarted = _runtime(tmp_path, [tool], [ModelResponse(tool_calls=(_exec("c9", "git", "status", "-sb"),)), ModelResponse(text="ok")])
    try:
        session = _session(restarted, workspace)
        done = restarted.start_turn(session.session_id, "go")
        assert done.status is AgentStatus.COMPLETED
        # ...but another project does not inherit it.
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        restarted.platform.responses[:] = [ModelResponse(tool_calls=(_exec("c10", "git", "status"),))]
        other = _session(restarted, elsewhere)
        assert restarted.start_turn(other.session_id, "go").status is AgentStatus.WAITING_APPROVAL
    finally:
        restarted.close()


def test_environment_overrides_defeat_a_saved_rule(tmp_path):
    ran = []
    runtime = _runtime(
        tmp_path,
        [_exec_tool(lambda _c, a: ran.append(a) or ToolResult(ok=True, content="ok"))],
        [
            ModelResponse(tool_calls=(_exec("c1", "git", "status"),)),
            # Same prefix, but PATH is replaced: a saved rule must not vouch for that.
            ModelResponse(tool_calls=(_exec("c2", "git", "status", env={"PATH": "C:\\planted"}),)),
            ModelResponse(text="done"),
        ],
    )
    try:
        session = _session(runtime, _workspace(tmp_path))
        runtime.start_turn(session.session_id, "go")
        result = runtime.resume_approval(
            session.session_id, "c1", approved=True, review_decision="approved_with_prefix_rule"
        )
        assert result.status is AgentStatus.WAITING_APPROVAL
        assert result.pending_approval.call_id == "c2"
        # And such a call is offered no wider grant than itself.
        data = _approval_events(runtime, session.session_id)[-1].data
        assert "proposed_prefix_rule" not in data
    finally:
        runtime.close()


def test_a_tool_can_be_approved_for_the_whole_session_by_name(tmp_path):
    calls: list[str] = []
    runtime = _runtime(
        tmp_path,
        [_plain_tool("write_report", calls), _plain_tool("send_note", calls)],
        [
            ModelResponse(tool_calls=(ToolCall(call_id="c1", name="write_report", arguments={"value": "a"}),)),
            ModelResponse(
                tool_calls=(
                    ToolCall(call_id="c2", name="write_report", arguments={"value": "b"}),
                    ToolCall(call_id="c3", name="send_note", arguments={"value": "c"}),
                )
            ),
            ModelResponse(text="done"),
        ],
        approval_policy=ApprovalPolicy.ON_REQUEST,
    )
    try:
        session = _session(runtime, _workspace(tmp_path))
        waiting = runtime.start_turn(session.session_id, "go")
        data = _approval_events(runtime, session.session_id)[-1].data
        assert data["session_scope"] == {"kind": "tool", "tool": "write_report"}
        assert "proposed_prefix_rule" not in data
        assert waiting.pending_approval.call_id == "c1"

        result = runtime.resume_approval(
            session.session_id, "c1", approved=True, review_decision="approved_for_session"
        )
        # Same tool: no second prompt. A different tool still asks.
        assert calls == ["write_report:a", "write_report:b"]
        assert result.status is AgentStatus.WAITING_APPROVAL
        assert result.pending_approval.call_id == "c3"
    finally:
        runtime.close()


def test_a_project_rule_cannot_be_saved_for_a_tool_and_the_request_stays_open(tmp_path):
    calls: list[str] = []
    runtime = _runtime(
        tmp_path,
        [_plain_tool("write_report", calls)],
        [ModelResponse(tool_calls=(ToolCall(call_id="c1", name="write_report", arguments={"value": "a"}),)), ModelResponse(text="done")],
        approval_policy=ApprovalPolicy.ON_REQUEST,
    )
    try:
        session = _session(runtime, _workspace(tmp_path))
        runtime.start_turn(session.session_id, "go")
        with pytest.raises(ValueError, match="only command prefixes"):
            runtime.resume_approval(
                session.session_id, "c1", approved=True, review_decision="approved_with_prefix_rule"
            )
        assert runtime.store.load(session.session_id).status is AgentStatus.WAITING_APPROVAL
        assert calls == []
    finally:
        runtime.close()


def test_declining_with_feedback_tells_the_model_why(tmp_path):
    ran = []
    runtime = _runtime(
        tmp_path,
        [_exec_tool(lambda _c, a: ran.append(a) or ToolResult(ok=True, content="ok"))],
        [ModelResponse(tool_calls=(_exec("c1", "git", "push", "--force"),)), ModelResponse(text="understood")],
    )
    try:
        session = _session(runtime, _workspace(tmp_path))
        runtime.start_turn(session.session_id, "go")
        result = runtime.resume_approval(
            session.session_id, "c1", approved=False, review_decision="denied",
            feedback="  Never force push; open a PR instead.\x00  ",
        )

        assert result.status is AgentStatus.COMPLETED
        assert ran == []
        denied = _events(runtime, session.session_id, AgentEventKind.TOOL_DENIED)[-1].data
        assert denied["source"] == "user"
        assert denied["feedback"] == "Never force push; open a PR instead."
        assert "Tool call denied by the user. The user explained: Never force push; open a PR instead." in _tool_text(
            runtime, session.session_id
        )
    finally:
        runtime.close()


def test_a_plain_decline_keeps_its_old_message(tmp_path):
    runtime = _runtime(
        tmp_path,
        [_exec_tool(lambda *_: ToolResult(ok=True, content="ok"))],
        [ModelResponse(tool_calls=(_exec("c1", "git", "push"),)), ModelResponse(text="ok")],
    )
    try:
        session = _session(runtime, _workspace(tmp_path))
        runtime.start_turn(session.session_id, "go")
        runtime.resume_approval(session.session_id, "c1", approved=False)
        text = _tool_text(runtime, session.session_id)
        assert "Tool call denied by the user." in text
        assert "explained" not in text
        assert "feedback" not in _events(runtime, session.session_id, AgentEventKind.TOOL_DENIED)[-1].data
    finally:
        runtime.close()


def test_feedback_cannot_ride_along_with_an_approval(tmp_path):
    runtime = _runtime(
        tmp_path,
        [_exec_tool(lambda *_: ToolResult(ok=True, content="ok"))],
        [ModelResponse(tool_calls=(_exec("c1", "git", "status"),)), ModelResponse(text="ok")],
    )
    try:
        session = _session(runtime, _workspace(tmp_path))
        runtime.start_turn(session.session_id, "go")
        with pytest.raises(ValueError, match="declined"):
            runtime.resume_approval(session.session_id, "c1", approved=True, feedback="sure")
    finally:
        runtime.close()


def test_allowing_an_escalation_for_the_session_skips_the_sandbox_next_time(tmp_path):
    attempts = []

    def handler(_context, arguments):
        attempt = current_sandbox_attempt()
        attempts.append((attempt.kind, attempt.selection))
        if attempt.selection is SandboxAttemptSelection.NO_SANDBOX:
            return ToolResult(ok=True, content="ran outside the sandbox")
        return _windows_denial(arguments["argv"])

    runtime = _runtime(
        tmp_path,
        [_exec_tool(handler)],
        [
            ModelResponse(tool_calls=(_exec("c1", "git.exe", "status"),)),
            ModelResponse(tool_calls=(_exec("c2", "git.exe", "status", "--short"),)),
            ModelResponse(text="done"),
        ],
        approval_policy=ApprovalPolicy.ON_REQUEST,
    )
    try:
        session = _session(runtime, _workspace(tmp_path))
        waiting = runtime.start_turn(session.session_id, "go")
        # First time: sandboxed attempt dies at startup, the reviewer is asked.
        assert waiting.pending_approval.kind is ApprovalKind.SANDBOX_ESCALATION
        data = _approval_events(runtime, session.session_id)[-1].data
        assert data["rule_sandbox"] == "unsandboxed"
        assert data["proposed_prefix_rule"] == ["git", "status"]
        assert data["sandbox_denial"]["kind"] == "dll_init"

        done = runtime.resume_approval(
            session.session_id, "c1", approved=True, review_decision="approved_for_session"
        )

        assert done.status is AgentStatus.COMPLETED
        assert attempts == [
            (SandboxAttemptKind.INITIAL, SandboxAttemptSelection.POLICY),
            (SandboxAttemptKind.RETRY, SandboxAttemptSelection.NO_SANDBOX),
            # The second call never touches the sandbox that cannot start it.
            (SandboxAttemptKind.INITIAL, SandboxAttemptSelection.NO_SANDBOX),
        ]
    finally:
        runtime.close()


def test_a_saved_unsandboxed_rule_is_inert_where_the_policy_forbids_leaving_the_sandbox(tmp_path):
    for label, approval_policy, sandbox_policy in (
        ("never", ApprovalPolicy.NEVER, SandboxPolicy.AUTO),
        ("required", ApprovalPolicy.ON_REQUEST, SandboxPolicy.REQUIRED),
    ):
        attempts = []

        def handler(_context, _arguments, bucket=attempts):
            bucket.append(current_sandbox_attempt().selection)
            return ToolResult(ok=True, content="ok")

        runtime = _runtime(
            tmp_path,
            [_exec_tool(handler)],
            [ModelResponse(tool_calls=(_exec("c1", "git", "status"),)), ModelResponse(text="done")],
            approval_policy=approval_policy,
            sandbox_policy=sandbox_policy,
            state=f"state-{label}",
        )
        try:
            session = _session(runtime, _workspace(tmp_path))
            runtime.orchestrator._approval_store(session.session_id).add_prefix_rule(
                PrefixRule(("git", "status"), RuleSandbox.UNSANDBOXED)
            )
            done = runtime.start_turn(session.session_id, "go")
            assert done.status is AgentStatus.COMPLETED, label
            assert attempts == [SandboxAttemptSelection.POLICY], label
        finally:
            runtime.close()


def test_a_policy_rule_does_not_cover_the_unsandboxed_fallback(tmp_path):
    """"Run it as the sandbox would" must not turn into "run it with no sandbox"."""

    runtime = PolicyRuntime(
        platform=ScriptedPlatform([ModelResponse(tool_calls=(_exec("c1", "git", "status"),)), ModelResponse(text="done")]),
        store=FileAgentSessionStore(tmp_path / "state"),
        tools=ToolRegistry((_exec_tool(lambda *_: ToolResult(ok=True, content="ok")),)),
        # No backend at all: AUTO would run the command unsandboxed after approval.
        sandbox_manager=SandboxManager(policy=SandboxPolicy.AUTO, system_name="Linux", bubblewrap_executable="", probe_backend=False),
        test_approval_policy=ApprovalPolicy.ON_REQUEST,
    )
    try:
        session = _session(runtime, _workspace(tmp_path))
        runtime.orchestrator._approval_store(session.session_id).add_prefix_rule(
            PrefixRule(("git", "status"), RuleSandbox.POLICY)
        )
        waiting = runtime.start_turn(session.session_id, "go")
        assert waiting.status is AgentStatus.WAITING_APPROVAL
    finally:
        runtime.close()


def test_failing_to_save_a_project_rule_still_runs_the_approved_call(tmp_path):
    ran = []
    runtime = _runtime(
        tmp_path,
        [_exec_tool(lambda _c, a: ran.append(a["argv"]) or ToolResult(ok=True, content="ok"))],
        [ModelResponse(tool_calls=(_exec("c1", "git", "status"),)), ModelResponse(text="done")],
    )
    try:
        # A directory where the rules file belongs makes every write fail.
        (tmp_path / "state" / "approval-rules.json").mkdir(parents=True)
        session = _session(runtime, _workspace(tmp_path))
        runtime.start_turn(session.session_id, "go")
        done = runtime.resume_approval(
            session.session_id, "c1", approved=True, review_decision="approved_with_prefix_rule"
        )
        assert done.status is AgentStatus.COMPLETED
        assert ran == [["git", "status"]]
        review = _events(runtime, session.session_id, AgentEventKind.TOOL_APPROVED)[-1].data["review"]
        assert "rule" not in review
        assert "could not" in review["rule_error"]
    finally:
        runtime.close()


# ---- the app-server protocol -------------------------------------------------


def _service(tmp_path: Path, tools, responses, **kwargs):
    runtime = _runtime(tmp_path, tools, responses, **kwargs)
    workspace = _workspace(tmp_path)
    service = LoomAppServerService(
        runtime=runtime,
        store=runtime.store,
        model="test-model",
        default_workspace=workspace,
        default_permission_mode=PermissionMode.WORKSPACE,
    )
    return service, runtime, workspace


def _wait(predicate, *, timeout: float = 5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.01)
    raise AssertionError("timed out waiting for app-server state")


def _start(service, workspace):
    thread_id = service.thread_start({"workspace": str(workspace), "permissionMode": "workspace"})["thread"]["id"]
    turn_id = service.turn_start({"threadId": thread_id, "input": "go"})["turn"]["id"]
    pending = _wait(lambda: service.thread_read({"threadId": thread_id})["pendingApproval"])
    return thread_id, turn_id, pending


def _respond(service, thread_id, turn_id, pending, decision, **extra):
    return service.approval_respond(
        {
            "threadId": thread_id,
            "turnId": turn_id,
            "requestId": pending["requestId"],
            "callId": pending["callId"],
            "decision": decision,
            **extra,
        }
    )


def test_exec_approvals_advertise_every_decision_and_what_each_would_save(tmp_path):
    service, runtime, workspace = _service(
        tmp_path,
        [_exec_tool(lambda *_: ToolResult(ok=True, content="ok"))],
        [ModelResponse(tool_calls=(_exec("c1", "git", "status"),)), ModelResponse(text="done")],
    )
    try:
        _thread, _turn, pending = _start(service, workspace)
        assert pending["availableDecisions"] == ["accept", "acceptForSession", "acceptWithPrefixRule", "decline"]
        assert pending["proposedPrefixRule"] == ["git", "status"]
        assert pending["sessionScope"] == {"kind": "prefix", "prefix": ["git", "status"]}
        assert pending["ruleSandbox"] == "policy"
        assert pending["sandboxDenial"] is None
    finally:
        runtime.close()


def test_accept_for_session_over_the_protocol_remembers_the_prefix(tmp_path):
    ran = []
    service, runtime, workspace = _service(
        tmp_path,
        [_exec_tool(lambda _c, a: ran.append(a["argv"]) or ToolResult(ok=True, content="ok"))],
        [
            ModelResponse(tool_calls=(_exec("c1", "git", "status"),)),
            ModelResponse(tool_calls=(_exec("c2", "git", "status", "-s"),)),
            ModelResponse(text="done"),
        ],
    )
    try:
        thread_id, turn_id, pending = _start(service, workspace)
        reply = _respond(service, thread_id, turn_id, pending, "acceptForSession")
        assert reply["accepted"] is True and reply["decision"] == "acceptForSession"
        _wait(lambda: runtime.store.load(thread_id).status is AgentStatus.COMPLETED)
        assert ran == [["git", "status"], ["git", "status", "-s"]]
    finally:
        runtime.close()


def test_accept_with_prefix_rule_over_the_protocol_saves_for_the_project_and_lists_it(tmp_path):
    service, runtime, workspace = _service(
        tmp_path,
        [_exec_tool(lambda *_: ToolResult(ok=True, content="ok"))],
        [ModelResponse(tool_calls=(_exec("c1", "git", "status"),)), ModelResponse(text="done")],
    )
    try:
        thread_id, turn_id, pending = _start(service, workspace)
        _respond(service, thread_id, turn_id, pending, "acceptWithPrefixRule")
        _wait(lambda: runtime.orchestrator.project_rules.rules(workspace))

        listing = service.approval_rules_list({})
        assert listing["available"] is True
        assert [rule["prefix"] for project in listing["projects"] for rule in project["rules"]] == [["git", "status"]]
        assert service.approval_rules_list({"workspace": str(tmp_path / "nowhere")})["projects"] == []

        removed = service.approval_rules_remove(
            {"workspace": str(workspace), "prefix": ["git", "status"], "sandbox": "policy"}
        )
        assert removed == {"removed": True}
        assert service.approval_rules_list({})["projects"] == []
        with pytest.raises(ValueError, match="array of strings"):
            service.approval_rules_remove({"workspace": str(workspace), "prefix": "git status"})
    finally:
        runtime.close()


def test_a_decision_the_request_did_not_offer_is_rejected_and_the_request_stays_open(tmp_path):
    calls: list[str] = []
    service, runtime, workspace = _service(
        tmp_path,
        [_plain_tool("write_report", calls)],
        [ModelResponse(tool_calls=(ToolCall(call_id="c1", name="write_report", arguments={"value": "a"}),)), ModelResponse(text="done")],
        approval_policy=ApprovalPolicy.ON_REQUEST,
    )
    try:
        thread_id, turn_id, pending = _start(service, workspace)
        assert pending["availableDecisions"] == ["accept", "acceptForSession", "decline"]
        # No command prefix exists for a plain tool, so it cannot be saved.
        with pytest.raises(ValueError, match="not available"):
            _respond(service, thread_id, turn_id, pending, "acceptWithPrefixRule")
        with pytest.raises(ValueError, match="must be one of"):
            _respond(service, thread_id, turn_id, pending, "acceptForever")
        # The durable identity checks are unchanged.
        with pytest.raises(ValueError, match="requestId does not match"):
            service.approval_respond(
                {"threadId": thread_id, "turnId": turn_id, "requestId": "stale", "callId": "c1", "decision": "acceptForSession"}
            )
        assert service.thread_read({"threadId": thread_id})["pendingApproval"]["requestId"] == pending["requestId"]
        assert calls == []
    finally:
        runtime.close()


def test_feedback_is_accepted_only_with_decline_and_reaches_the_model(tmp_path):
    ran = []
    service, runtime, workspace = _service(
        tmp_path,
        [_exec_tool(lambda _c, a: ran.append(a) or ToolResult(ok=True, content="ok"))],
        [ModelResponse(tool_calls=(_exec("c1", "git", "push"),)), ModelResponse(text="noted")],
    )
    try:
        thread_id, turn_id, pending = _start(service, workspace)
        with pytest.raises(ValueError, match="only accepted with the decline"):
            _respond(service, thread_id, turn_id, pending, "accept", feedback="because")
        with pytest.raises(ValueError, match="must be a string"):
            _respond(service, thread_id, turn_id, pending, "decline", feedback=["no"])
        with pytest.raises(ValueError, match="at most 2000"):
            _respond(service, thread_id, turn_id, pending, "decline", feedback="x" * 2001)

        _respond(service, thread_id, turn_id, pending, "decline", feedback="Use a pull request instead.")
        _wait(lambda: runtime.store.load(thread_id).status is AgentStatus.COMPLETED)
        assert ran == []
        assert "The user explained: Use a pull request instead." in _tool_text(runtime, thread_id)
    finally:
        runtime.close()


def test_capabilities_advertise_the_new_decisions(tmp_path):
    from app.app_server_protocol import APPROVAL_DECISIONS

    assert APPROVAL_DECISIONS == ("accept", "acceptForSession", "acceptWithPrefixRule", "decline")
