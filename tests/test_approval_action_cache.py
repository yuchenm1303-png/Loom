from __future__ import annotations

from dataclasses import replace

from app.ai import ToolCall
from app.agent_runtime.approval_actions import (
    ApprovalDecisionStore,
    ApplyPatchApprovalAction,
    ExecApprovalAction,
    ReviewDecision,
    approval_action_for,
)
from app.agent_runtime.contracts import PermissionMode, ToolEffect
from app.agent_runtime.execution_action import ExecActionIdentity
from app.agent_runtime.permissions import SandboxPermissions
from app.agent_runtime.shell_environment import ShellEnvironmentPolicy
from app.agent_runtime.step import StepContext
from app.agent_runtime.tools import AgentTool, ToolResult, ToolRouter


def _exec_tool() -> AgentTool:
    return AgentTool(
        name="exec",
        description="Synthetic exec for approval cache tests.",
        input_schema={"type": "object", "properties": {}, "additionalProperties": True},
        handler=lambda _context, _arguments: ToolResult(ok=True, content="ok"),
        effect=ToolEffect.SENSITIVE,
    )


def _patch_tool() -> AgentTool:
    return AgentTool(
        name="apply_patch",
        description="Synthetic patch for approval cache tests.",
        input_schema={"type": "object", "properties": {}, "additionalProperties": True},
        handler=lambda _context, _arguments: ToolResult(ok=True, content="ok"),
        effect=ToolEffect.MUTATING,
    )


def _step(tmp_path) -> StepContext:
    step = StepContext.build(
        step_id="step-1",
        session_id="session-1",
        turn_id="turn-1",
        model_step=1,
        workspace_dir=str(tmp_path),
        profile_id="agent.fast",
        permission_mode=PermissionMode.WORKSPACE,
        tool_router=ToolRouter((_exec_tool(), _patch_tool())),
    )
    return replace(step, environment_policy=ShellEnvironmentPolicy(inherit="none"))


def _exec_call(*, call_id="exec-1", **overrides) -> ToolCall:
    arguments = {
        "argv": ["synthetic-program", "--flag"],
        "cwd": ".",
        "env": {"NAME": "one"},
        "pty": False,
    }
    arguments.update(overrides)
    return ToolCall(call_id=call_id, name="exec", arguments=arguments)


def _patch_call(*, call_id="patch-1", content="one", path="note.txt") -> ToolCall:
    return ToolCall(
        call_id=call_id,
        name="apply_patch",
        arguments={
            "changes": [
                {"action": "add", "path": path, "content": content},
            ]
        },
    )


def test_exec_approval_action_keeps_call_id_out_of_reusable_cache_key(tmp_path):
    step = _step(tmp_path)
    first = approval_action_for(step, _exec_call(call_id="exec-1"))
    second = approval_action_for(step, _exec_call(call_id="exec-2"))

    assert isinstance(first, ExecApprovalAction)
    assert isinstance(second, ExecApprovalAction)
    assert first.call_id != second.call_id
    assert first.cache_keys() == second.cache_keys()


def test_exec_binding_tracks_environment_but_approval_cache_does_not(tmp_path):
    step = _step(tmp_path)
    first_identity = ExecActionIdentity.build(step, _exec_call(env={"NAME": "one"}))
    second_identity = ExecActionIdentity.build(step, _exec_call(env={"NAME": "two"}))

    assert first_identity.digest() != second_identity.digest()
    assert first_identity.approval_cache_key() == second_identity.approval_cache_key()


def test_exec_cache_key_changes_for_codex_semantic_fields(tmp_path):
    step = _step(tmp_path)
    subdir = tmp_path / "subdir"
    subdir.mkdir()

    base = ExecActionIdentity.build(step, _exec_call()).approval_cache_key()
    changed_command = ExecActionIdentity.build(
        step,
        _exec_call(argv=["synthetic-program", "--other"]),
    ).approval_cache_key()
    changed_cwd = ExecActionIdentity.build(
        step,
        _exec_call(cwd="subdir"),
    ).approval_cache_key()
    changed_tty = ExecActionIdentity.build(step, _exec_call(pty=True)).approval_cache_key()
    changed_sandbox = ExecActionIdentity.build(
        step,
        _exec_call(
            sandbox_permissions=SandboxPermissions.REQUIRE_ESCALATED.value,
            justification="Needs full escalation",
        ),
    ).approval_cache_key()
    changed_additional = ExecActionIdentity.build(
        step,
        _exec_call(
            sandbox_permissions=SandboxPermissions.WITH_ADDITIONAL_PERMISSIONS.value,
            additional_permissions={"file_system": {"read": [str(tmp_path)]}},
        ),
    ).approval_cache_key()

    assert base != changed_command
    assert base != changed_cwd
    assert base != changed_tty
    assert base != changed_sandbox
    assert base != changed_additional


def test_call_id_reuse_cannot_reuse_approval_after_command_changes(tmp_path):
    step = _step(tmp_path)
    first = approval_action_for(
        step,
        _exec_call(call_id="same-id", argv=["synthetic-program", "first"]),
    )
    changed = approval_action_for(
        step,
        _exec_call(call_id="same-id", argv=["synthetic-program", "second"]),
    )
    assert isinstance(first, ExecApprovalAction)
    assert isinstance(changed, ExecApprovalAction)

    store = ApprovalDecisionStore()
    store.record(first.cache_keys(), ReviewDecision.APPROVED_FOR_SESSION)

    assert store.lookup(first.cache_keys()) is ReviewDecision.APPROVED_FOR_SESSION
    assert store.lookup(changed.cache_keys()) is None


def test_apply_patch_cache_is_environment_and_path_not_patch_content(tmp_path):
    step = _step(tmp_path)
    first = approval_action_for(step, _patch_call(content="first"))
    second = approval_action_for(step, _patch_call(call_id="patch-2", content="second"))

    assert isinstance(first, ApplyPatchApprovalAction)
    assert isinstance(second, ApplyPatchApprovalAction)
    assert first.request_digest != second.request_digest
    assert first.cache_keys() == second.cache_keys()

    remote = replace(second, environment_id="remote-env")
    assert remote.cache_keys() != first.cache_keys()


def test_approved_for_session_is_retained_but_one_shot_and_denials_are_not(tmp_path):
    step = _step(tmp_path)
    action = approval_action_for(step, _exec_call())
    assert isinstance(action, ExecApprovalAction)
    keys = action.cache_keys()
    store = ApprovalDecisionStore()

    for decision in (
        ReviewDecision.APPROVED,
        ReviewDecision.DENIED,
        ReviewDecision.TIMED_OUT,
        ReviewDecision.ABORTED,
    ):
        store.record(keys, decision)
        assert store.lookup(keys) is None

    store.record(keys, ReviewDecision.APPROVED_FOR_SESSION)
    assert store.lookup(keys) is ReviewDecision.APPROVED_FOR_SESSION


def test_policy_fingerprint_partitions_exec_session_cache(tmp_path):
    step = _step(tmp_path)
    action = approval_action_for(step, _exec_call())
    assert isinstance(action, ExecApprovalAction)
    keys = action.cache_keys()
    store = ApprovalDecisionStore()

    store.record(
        keys,
        ReviewDecision.APPROVED_FOR_SESSION,
        policy_fingerprint="policy-v1",
    )

    assert (
        store.lookup(keys, policy_fingerprint="policy-v1")
        is ReviewDecision.APPROVED_FOR_SESSION
    )
    assert store.lookup(keys, policy_fingerprint="policy-v2") is None


def test_multi_file_patch_requires_every_path_to_be_session_approved(tmp_path):
    step = _step(tmp_path)
    call = ToolCall(
        call_id="patch-many",
        name="apply_patch",
        arguments={
            "changes": [
                {"action": "add", "path": "one.txt", "content": "one"},
                {"action": "add", "path": "two.txt", "content": "two"},
            ]
        },
    )
    action = approval_action_for(step, call)
    assert isinstance(action, ApplyPatchApprovalAction)
    keys = action.cache_keys()
    assert len(keys) == 2

    store = ApprovalDecisionStore()
    store.record((keys[0],), ReviewDecision.APPROVED_FOR_SESSION)
    assert store.lookup(keys) is None

    store.record((keys[1],), ReviewDecision.APPROVED_FOR_SESSION)
    assert store.lookup(keys) is ReviewDecision.APPROVED_FOR_SESSION
