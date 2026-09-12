from __future__ import annotations

from app.ai import ToolCall
from app.agent_runtime.contracts import PermissionMode, ToolEffect
from app.agent_runtime.exec_policy import ExecPolicy
from app.agent_runtime.network_policy import NetworkAccess, NetworkPolicy
from app.agent_runtime.orchestrator import ToolOrchestrator
from app.agent_runtime.permissions import PermissionDecision, permission_snapshot
from app.agent_runtime.process_tools import exec_tool
from app.agent_runtime.step import StepContext
from app.agent_runtime.tools import ToolPolicy, ToolRouter


def _step(tmp_path, mode: PermissionMode):
    tool = exec_tool()
    return StepContext.build(
        step_id="step-1",
        session_id="session-1",
        turn_id="turn-1",
        model_step=1,
        workspace_dir=str(tmp_path),
        profile_id="agent.fast",
        permission_mode=mode,
        permissions=permission_snapshot(mode),
        tool_router=ToolRouter((tool,)),
    ), tool


def _prepare(tmp_path, mode: PermissionMode, argv: list[str], **extra):
    step, _ = _step(tmp_path, mode)
    return ToolOrchestrator().prepare(
        step,
        ToolCall(
            call_id="call-1",
            name="exec",
            arguments={"argv": argv, **extra},
        ),
        legacy_policy=ToolPolicy(),
    )


def test_exec_policy_auto_allows_recognized_read_only_git_inspection(tmp_path):
    prepared = _prepare(tmp_path, PermissionMode.APPROVAL, ["git", "status"])

    assert prepared.decision is PermissionDecision.ALLOW
    assert prepared.effective_effect is ToolEffect.READ_ONLY
    assert prepared.tool.effect is ToolEffect.READ_ONLY
    assert prepared.network_requested is False
    assert prepared.matched_exec_rule == "git:status"


def test_exec_policy_maps_workspace_local_file_mutation_to_existing_permission_modes(tmp_path):
    workspace = _prepare(tmp_path, PermissionMode.WORKSPACE, ["rm", "-rf", "build"])
    approval = _prepare(tmp_path, PermissionMode.APPROVAL, ["rm", "-rf", "build"])
    read_only = _prepare(tmp_path, PermissionMode.READ_ONLY, ["rm", "-rf", "build"])

    assert workspace.effective_effect is ToolEffect.MUTATING
    assert workspace.decision is PermissionDecision.ALLOW
    assert workspace.tool.effect is ToolEffect.MUTATING
    assert approval.decision is PermissionDecision.APPROVAL
    assert read_only.decision is PermissionDecision.DENY


def test_exec_policy_refuses_to_downgrade_absolute_parent_drive_or_project_paths(tmp_path):
    absolute = _prepare(tmp_path, PermissionMode.WORKSPACE, ["cat", "/etc/passwd"])
    parent = _prepare(tmp_path, PermissionMode.WORKSPACE, ["rm", "-rf", "../other"])
    drive_relative = _prepare(tmp_path, PermissionMode.WORKSPACE, ["cat", "C:outside.txt"])
    embedded = _prepare(
        tmp_path,
        PermissionMode.WORKSPACE,
        ["cp", "source.txt", "--target-directory=/tmp"],
    )
    disguised = _prepare(tmp_path, PermissionMode.WORKSPACE, ["./git", "status"])

    for prepared in (absolute, parent, drive_relative, embedded, disguised):
        assert prepared.effective_effect is ToolEffect.SENSITIVE
        assert prepared.decision is PermissionDecision.APPROVAL


def test_exec_policy_keeps_arbitrary_interpreter_execution_sensitive(tmp_path):
    version = _prepare(tmp_path, PermissionMode.APPROVAL, ["python", "--version"])
    code = _prepare(tmp_path, PermissionMode.WORKSPACE, ["python", "-c", "print('hello')"])

    assert version.effective_effect is ToolEffect.READ_ONLY
    assert version.decision is PermissionDecision.ALLOW
    assert code.effective_effect is ToolEffect.SENSITIVE
    assert code.decision is PermissionDecision.APPROVAL


def test_exec_policy_does_not_downgrade_environment_or_pty_modified_calls(tmp_path):
    env = _prepare(
        tmp_path,
        PermissionMode.WORKSPACE,
        ["git", "status"],
        env={"PATH": "./tools"},
    )
    pty = _prepare(
        tmp_path,
        PermissionMode.WORKSPACE,
        ["git", "log"],
        pty=True,
    )

    assert env.effective_effect is ToolEffect.SENSITIVE
    assert env.decision is PermissionDecision.APPROVAL
    assert env.matched_exec_rule.endswith(":env")
    assert pty.effective_effect is ToolEffect.SENSITIVE
    assert pty.decision is PermissionDecision.APPROVAL
    assert pty.matched_exec_rule.endswith(":pty")


def test_network_policy_upgrades_read_only_remote_git_to_approval(tmp_path):
    workspace = _prepare(
        tmp_path,
        PermissionMode.WORKSPACE,
        ["git", "ls-remote", "https://github.com/openai/codex.git"],
    )
    full = _prepare(
        tmp_path,
        PermissionMode.FULL_ACCESS,
        ["git", "ls-remote", "https://github.com/openai/codex.git"],
    )

    assert workspace.effective_effect is ToolEffect.READ_ONLY
    assert workspace.network_requested is True
    assert workspace.decision is PermissionDecision.APPROVAL
    assert full.decision is PermissionDecision.ALLOW


def test_git_read_only_flags_that_create_side_effects_remain_sensitive(tmp_path):
    equals_form = _prepare(
        tmp_path,
        PermissionMode.WORKSPACE,
        ["git", "diff", "--output=diff.txt"],
    )
    split_form = _prepare(
        tmp_path,
        PermissionMode.WORKSPACE,
        ["git", "diff", "--output", "diff.txt"],
    )

    assert equals_form.effective_effect is ToolEffect.SENSITIVE
    assert equals_form.decision is PermissionDecision.APPROVAL
    assert split_form.effective_effect is ToolEffect.SENSITIVE
    assert split_form.decision is PermissionDecision.APPROVAL


def test_unknown_command_remains_sensitive_instead_of_guessing(tmp_path):
    prepared = _prepare(tmp_path, PermissionMode.WORKSPACE, ["some-new-tool", "inspect"])

    assert prepared.effective_effect is ToolEffect.SENSITIVE
    assert prepared.decision is PermissionDecision.APPROVAL


def test_network_policy_is_explicit_for_each_permission_family():
    policy = NetworkPolicy()

    denied = policy.evaluate(permission_snapshot(PermissionMode.READ_ONLY), requested=True)
    prompted = policy.evaluate(permission_snapshot(PermissionMode.WORKSPACE), requested=True)
    allowed = policy.evaluate(permission_snapshot(PermissionMode.FULL_ACCESS), requested=True)

    assert denied.access is NetworkAccess.DENY
    assert denied.decision is PermissionDecision.DENY
    assert prompted.access is NetworkAccess.ON_REQUEST
    assert prompted.decision is PermissionDecision.APPROVAL
    assert allowed.access is NetworkAccess.ALLOW
    assert allowed.decision is PermissionDecision.ALLOW


def test_exec_policy_classifier_is_conservative_without_runtime_context():
    policy = ExecPolicy()

    assert policy.classify(["git", "status"]).effect is ToolEffect.READ_ONLY
    assert policy.classify(["mkdir", "generated"]).effect is ToolEffect.MUTATING
    assert policy.classify(["bash", "-lc", "echo hi"]).effect is ToolEffect.SENSITIVE
    assert policy.classify(["./git", "status"]).effect is ToolEffect.SENSITIVE
