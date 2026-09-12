from __future__ import annotations

import base64
import json

from app.ai import ToolCall
from app.agent_runtime.contracts import PermissionMode, ToolEffect
from app.agent_runtime.exec_policy import ExecPolicy, exec_effect_scope
from app.agent_runtime.network_policy import NetworkAccess, NetworkPolicy, network_access_scope
from app.agent_runtime.orchestrator import ToolOrchestrator
from app.agent_runtime.permissions import PermissionDecision, permission_snapshot
from app.agent_runtime.process_tools import exec_tool
from app.agent_runtime.sandbox import SandboxManager, SandboxMode, SandboxPolicy
from app.agent_runtime.step import StepContext
from app.agent_runtime.tools import ToolPolicy, ToolRouter


def _step(tmp_path, mode: PermissionMode):
    tool = exec_tool()
    return StepContext.build(step_id="step-1", session_id="session-1", turn_id="turn-1", model_step=1, workspace_dir=str(tmp_path), profile_id="agent.fast", permission_mode=mode, permissions=permission_snapshot(mode), tool_router=ToolRouter((tool,))), tool


def _prepare(tmp_path, mode: PermissionMode, argv: list[str], **extra):
    step, _ = _step(tmp_path, mode)
    return ToolOrchestrator().prepare(step, ToolCall(call_id="call-1", name="exec", arguments={"argv": argv, **extra}), legacy_policy=ToolPolicy())


def test_exec_policy_auto_allows_recognized_read_only_git_inspection(tmp_path):
    prepared = _prepare(tmp_path, PermissionMode.APPROVAL, ["git", "status"])
    assert prepared.decision is PermissionDecision.ALLOW
    assert prepared.effective_effect is ToolEffect.READ_ONLY
    assert prepared.tool.effect is ToolEffect.READ_ONLY
    assert prepared.network_requested is False


def test_exec_policy_maps_safe_workspace_mutation_to_existing_permission_modes(tmp_path):
    workspace = _prepare(tmp_path, PermissionMode.WORKSPACE, ["rm", "-rf", "build"])
    approval = _prepare(tmp_path, PermissionMode.APPROVAL, ["rm", "-rf", "build"])
    read_only = _prepare(tmp_path, PermissionMode.READ_ONLY, ["rm", "-rf", "build"])
    assert workspace.effective_effect is ToolEffect.MUTATING and workspace.decision is PermissionDecision.ALLOW
    assert approval.decision is PermissionDecision.APPROVAL
    assert read_only.decision is PermissionDecision.DENY


def test_generic_file_readers_and_copy_stay_sensitive(tmp_path):
    for argv in (["cat", "notes.txt"], ["cp", "source.txt", "copy.txt"]):
        prepared = _prepare(tmp_path, PermissionMode.WORKSPACE, list(argv))
        assert prepared.effective_effect is ToolEffect.SENSITIVE
        assert prepared.decision is PermissionDecision.APPROVAL


def test_exec_policy_refuses_path_escape_or_project_executable(tmp_path):
    cases = (["cat", "/etc/passwd"], ["rm", "-rf", "../other"], ["cat", "C:outside.txt"], ["cp", "source.txt", "--target-directory=/tmp"], ["./git", "status"])
    for argv in cases:
        prepared = _prepare(tmp_path, PermissionMode.WORKSPACE, list(argv))
        assert prepared.effective_effect is ToolEffect.SENSITIVE
        assert prepared.decision is PermissionDecision.APPROVAL
        assert prepared.network_requested is True


def test_workspace_path_executable_cannot_impersonate_git(tmp_path, monkeypatch):
    fake = tmp_path / "git"
    fake.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    fake.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path))
    prepared = _prepare(tmp_path, PermissionMode.WORKSPACE, ["git", "status"])
    assert prepared.effective_effect is ToolEffect.SENSITIVE
    assert prepared.matched_exec_rule.endswith(":workspace-program")


def test_arbitrary_interpreter_is_sensitive_and_network_capable(tmp_path):
    version = _prepare(tmp_path, PermissionMode.APPROVAL, ["python", "--version"])
    code = _prepare(tmp_path, PermissionMode.WORKSPACE, ["python", "-c", "print('hello')"])
    assert version.effective_effect is ToolEffect.READ_ONLY and version.network_requested is False
    assert code.effective_effect is ToolEffect.SENSITIVE and code.network_requested is True
    assert code.decision is PermissionDecision.APPROVAL


def test_env_or_pty_prevents_read_only_downgrade(tmp_path):
    env = _prepare(tmp_path, PermissionMode.WORKSPACE, ["git", "status"], env={"PATH": "./tools"})
    pty = _prepare(tmp_path, PermissionMode.WORKSPACE, ["git", "log"], pty=True)
    assert env.effective_effect is ToolEffect.SENSITIVE and env.network_requested is True
    assert pty.effective_effect is ToolEffect.SENSITIVE and pty.network_requested is True


def test_network_policy_requires_approval_for_remote_git_in_workspace(tmp_path):
    workspace = _prepare(tmp_path, PermissionMode.WORKSPACE, ["git", "ls-remote", "https://github.com/openai/codex.git"])
    full = _prepare(tmp_path, PermissionMode.FULL_ACCESS, ["git", "ls-remote", "https://github.com/openai/codex.git"])
    assert workspace.effective_effect is ToolEffect.READ_ONLY
    assert workspace.network_requested is True and workspace.decision is PermissionDecision.APPROVAL
    assert full.decision is PermissionDecision.ALLOW


def test_network_policy_permission_families():
    policy = NetworkPolicy()
    assert policy.evaluate(permission_snapshot(PermissionMode.READ_ONLY), requested=True).decision is PermissionDecision.DENY
    assert policy.evaluate(permission_snapshot(PermissionMode.WORKSPACE), requested=True).decision is PermissionDecision.APPROVAL
    assert policy.evaluate(permission_snapshot(PermissionMode.FULL_ACCESS), requested=True).decision is PermissionDecision.ALLOW
    assert policy.access_for(permission_snapshot(PermissionMode.READ_ONLY)) is NetworkAccess.DENY


def test_read_only_exec_effect_tightens_workspace_sandbox(tmp_path):
    workspace = tmp_path / "project"
    workspace.mkdir()
    manager = SandboxManager(policy=SandboxPolicy.AUTO, bubblewrap_executable="/usr/bin/bwrap", probe_backend=False, system_name="Linux")
    with exec_effect_scope(ToolEffect.READ_ONLY):
        prepared = manager.prepare(argv=("git", "status"), cwd=workspace, workspace=workspace, permission_mode=PermissionMode.APPROVAL)
    assert prepared.snapshot.mode is SandboxMode.READ_ONLY
    assert prepared.snapshot.effective_exec_effect == ToolEffect.READ_ONLY.value
    assert "--bind" not in prepared.argv
    assert "--unshare-net" in prepared.argv


def test_linux_network_denied_by_default_and_opened_only_in_grant_scope(tmp_path):
    workspace = tmp_path / "project"
    workspace.mkdir()
    manager = SandboxManager(policy=SandboxPolicy.AUTO, bubblewrap_executable="/usr/bin/bwrap", probe_backend=False, system_name="Linux")
    denied = manager.prepare(argv=("git", "status"), cwd=workspace, workspace=workspace, permission_mode=PermissionMode.WORKSPACE)
    assert "--unshare-net" in denied.argv and denied.snapshot.network_isolated is True
    with network_access_scope(True):
        allowed = manager.prepare(argv=("git", "fetch"), cwd=workspace, workspace=workspace, permission_mode=PermissionMode.WORKSPACE)
    assert "--unshare-net" not in allowed.argv
    assert allowed.snapshot.network_isolated is False and allowed.snapshot.network_access_granted is True


def test_windows_mxc_network_grant_changes_egress_only(tmp_path):
    workspace = tmp_path / "project"
    workspace.mkdir()
    manager = SandboxManager(policy=SandboxPolicy.AUTO, windows_mxc_executable=r"C:\loom-test\wxc-exec.exe", probe_backend=False, system_name="Windows")
    with network_access_scope(True):
        prepared = manager.prepare(argv=("git", "fetch"), cwd=workspace, workspace=workspace, permission_mode=PermissionMode.WORKSPACE, environment={"PATH": r"C:\Windows\System32"})
    config = json.loads(base64.b64decode(prepared.argv[2]).decode("utf-8"))
    assert config["network"]["egress"] == {"default": "allow"}
    assert config["network"]["ingress"] == {"default": "deny", "hostLoopback": "deny"}
    assert prepared.snapshot.network_access_granted is True


def test_classifier_remains_conservative_for_unknown_and_shell_commands():
    policy = ExecPolicy()
    assert policy.classify(["git", "status"]).effect is ToolEffect.READ_ONLY
    assert policy.classify(["mkdir", "generated"]).effect is ToolEffect.MUTATING
    shell = policy.classify(["bash", "-lc", "echo hi"])
    assert shell.effect is ToolEffect.SENSITIVE and shell.requires_network is True
    unknown = policy.classify(["some-new-tool", "inspect"])
    assert unknown.effect is ToolEffect.SENSITIVE and unknown.requires_network is True
