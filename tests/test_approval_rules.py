from __future__ import annotations

import json

import pytest

from app.agent_runtime.approval_rules import (
    MAX_PROJECT_RULES,
    ApprovalRuleError,
    PrefixRule,
    ProjectApprovalRules,
    RuleSandbox,
    best_rule,
    derive_prefix_rule,
    executable_key,
    is_banned_prefix,
    normalized_command,
    project_key,
    validate_prefix,
)


@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        (["git", "status", "--short"], ("git", "status")),
        (["Git.EXE", "status"], ("git", "status")),
        (["npm", "test"], ("npm", "test")),
        # "npm run" alone would run any script, so the script name is included.
        (["npm", "run", "build"], ("npm", "run", "build")),
        (["cargo", "build", "--release"], ("cargo", "build")),
        (["pytest", "-q"], ("pytest",)),
        (["pip", "install", "requests"], ("pip", "install")),
        # A simple Bourne wrapper collapses to the inner command, as for approvals.
        (["bash", "-lc", "git status"], ("git", "status")),
    ],
)
def test_prefix_is_derived_from_the_program_and_its_subcommand(argv, expected):
    assert derive_prefix_rule(argv) == expected


@pytest.mark.parametrize(
    "argv",
    [
        ["git"],
        ["git", "-c", "alias.x=!sh", "x"],
        ["python", "-m", "pytest"],
        ["python", "script.py"],
        ["node", "build.js"],
        ["cmd", "/c", "dir"],
        ["powershell", "-NoProfile", "-Command", "Get-Date"],
        ["rm", "-rf", "build"],
        # A script with shell syntax stays one opaque, exact-only command.
        ["bash", "-lc", "git status && rm -rf x"],
        [],
    ],
)
def test_no_prefix_is_offered_where_it_would_approve_arbitrary_code(argv):
    assert derive_prefix_rule(argv) is None


def test_a_model_proposed_prefix_is_used_only_when_it_is_safe_and_real():
    assert derive_prefix_rule(["git", "log", "--oneline"], proposed=["git", "log"]) == ("git", "log")
    # Not a prefix of this command.
    assert derive_prefix_rule(["git", "status"], proposed=["git", "push"]) == ("git", "status")
    # Banned, so derivation falls back to its own reading of the command.
    assert derive_prefix_rule(["git", "status"], proposed=["git"]) == ("git", "status")
    assert derive_prefix_rule(["python", "-m", "pytest"], proposed=["python"]) is None


def test_executable_names_compare_by_name_but_paths_stay_exact():
    assert executable_key("GIT.exe") == "git"
    assert executable_key("tool.CMD") == "tool"
    assert executable_key(r"C:\Tools\Git.exe") != executable_key("git")
    assert normalized_command([r"C:\Tools\Git.exe", "status"])[0] == executable_key(r"C:\Tools\Git.exe")
    assert not PrefixRule(("git", "status")).matches(normalized_command([r"C:\evil\git.exe", "status"]))


def test_banned_prefixes_cover_shells_interpreters_and_launchers():
    for banned in (["git"], ["cmd.exe", "/c"], ["PowerShell", "-Command"], ["python", "-c"], ["npm", "run"], ["wsl"]):
        assert is_banned_prefix(banned)
    assert not is_banned_prefix(["git", "status"])
    with pytest.raises(ValueError, match="too broad"):
        validate_prefix(["git"])
    with pytest.raises(ValueError, match="at least one"):
        validate_prefix([])
    with pytest.raises(ValueError, match="too long"):
        validate_prefix(["tool"] + ["x"] * 20)
    with pytest.raises(ValueError, match="exact command"):
        validate_prefix(["__codex_shell_script__", "-lc", "echo hi"])


def test_prefix_rules_match_by_prefix_and_exact_rules_only_by_equality():
    rule = PrefixRule(("git", "status"))
    assert rule.matches(("git", "status"))
    assert rule.matches(("git", "status", "--short"))
    assert not rule.matches(("git", "push"))
    assert not rule.matches(("git",))
    # An opaque script is never matched by a prefix, only by an identical exact rule.
    script = normalized_command(["bash", "-lc", "echo a && echo b"])
    assert not rule.matches(script)
    assert PrefixRule(script, exact=True).matches(script)
    exact = PrefixRule(("node", "build.js"), exact=True)
    assert exact.matches(("node", "build.js"))
    assert not exact.matches(("node", "build.js", "--watch"))


def test_the_strongest_matching_rule_wins_and_escalation_is_gated():
    policy = PrefixRule(("git", "status"), RuleSandbox.POLICY)
    outside = PrefixRule(("git", "status"), RuleSandbox.UNSANDBOXED)
    command = ("git", "status", "-s")

    assert best_rule([policy], command, escalated_request=False, allow_unsandboxed=True) is policy
    assert best_rule([policy, outside], command, escalated_request=False, allow_unsandboxed=True) is outside
    # Where the policy forbids leaving the sandbox, an unsandboxed rule is inert.
    assert best_rule([policy, outside], command, escalated_request=False, allow_unsandboxed=False) is policy
    assert best_rule([outside], command, escalated_request=False, allow_unsandboxed=False) is None
    # A call that asks to leave the sandbox is decided only by an unsandboxed rule.
    assert best_rule([policy], command, escalated_request=True, allow_unsandboxed=True) is None
    assert best_rule([policy, outside], command, escalated_request=True, allow_unsandboxed=True) is outside
    # The longer prefix beats the shorter one within a kind.
    short = PrefixRule(("pnpm", "test"))
    longer = PrefixRule(("pnpm", "test", "unit"))
    assert best_rule([short, longer], ("pnpm", "test", "unit"), escalated_request=False, allow_unsandboxed=True) is longer


def test_project_rules_survive_a_new_instance_and_are_keyed_by_project_root(tmp_path):
    home = tmp_path / "home"
    workspace = tmp_path / "work"
    workspace.mkdir()
    rules = ProjectApprovalRules(home)

    assert rules.rules(workspace) == ()
    assert rules.add(workspace, PrefixRule(("git", "status"))) is True
    assert rules.add(workspace, PrefixRule(("git", "status"))) is False
    assert rules.add(workspace, PrefixRule(("git", "push"), RuleSandbox.UNSANDBOXED)) is True

    again = ProjectApprovalRules(home)
    saved = again.rules(workspace)
    assert [(rule.prefix, rule.sandbox) for rule in saved] == [
        (("git", "status"), RuleSandbox.POLICY),
        (("git", "push"), RuleSandbox.UNSANDBOXED),
    ]
    assert all(rule.created_at.endswith("Z") for rule in saved)
    # Same directory, spelled differently, is the same project.
    assert project_key(workspace / ".." / "work") == project_key(workspace)
    assert len(again.rules(workspace / ".." / "work")) == 2
    # Another project sees nothing.
    other = tmp_path / "other"
    other.mkdir()
    assert again.rules(other) == ()

    assert again.remove(workspace, ["git", "status"], "policy") is True
    assert again.remove(workspace, ["git", "status"], "policy") is False
    assert [rule.prefix for rule in ProjectApprovalRules(home).rules(workspace)] == [("git", "push")]
    assert again.remove(workspace, ["git", "push"], "unsandboxed") is True
    assert json.loads((home / "approval-rules.json").read_text(encoding="utf-8"))["projects"] == {}


def test_snapshot_lists_projects_and_a_stale_banned_rule_can_still_be_removed(tmp_path):
    home = tmp_path / "home"
    workspace = tmp_path / "work"
    workspace.mkdir()
    rules = ProjectApprovalRules(home)
    rules.add(workspace, PrefixRule(("cargo", "build")))
    # Hand-edit a rule that is too broad (or became banned after it was saved).
    path = home / "approval-rules.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    entry = payload["projects"][project_key(workspace)]
    entry["rules"].append({"prefix": ["python"], "sandbox": "policy", "createdAt": ""})
    path.write_text(json.dumps(payload), encoding="utf-8")

    fresh = ProjectApprovalRules(home)
    # The broad entry is ignored when matching and when listing...
    assert [rule.prefix for rule in fresh.rules(workspace)] == [("cargo", "build")]
    listing = fresh.snapshot()
    assert len(listing) == 1
    assert listing[0]["root"] == str(workspace)
    assert [rule["prefix"] for rule in listing[0]["rules"]] == [["cargo", "build"]]
    assert fresh.snapshot(tmp_path / "elsewhere") == []
    # ...and removing the last good rule still works.
    assert fresh.remove(workspace, ["cargo", "build"], "policy") is True


def test_unreadable_storage_means_no_rules_and_a_clear_error_on_write(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    workspace = tmp_path / "work"
    workspace.mkdir()
    (home / "approval-rules.json").write_text("{not json", encoding="utf-8")
    rules = ProjectApprovalRules(home)

    # Matching must never crash a tool call: a bad file just prompts as before.
    assert rules.rules(workspace) == ()
    with pytest.raises(ApprovalRuleError, match="could not read"):
        rules.add(workspace, PrefixRule(("git", "status")))


def test_a_project_keeps_a_bounded_number_of_rules(tmp_path):
    home = tmp_path / "home"
    workspace = tmp_path / "work"
    workspace.mkdir()
    rules = ProjectApprovalRules(home)
    for index in range(MAX_PROJECT_RULES):
        assert rules.add(workspace, PrefixRule(("tool", f"sub{index}"))) is True
    with pytest.raises(ApprovalRuleError, match="at most"):
        rules.add(workspace, PrefixRule(("tool", "one-too-many")))
