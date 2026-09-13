"""Unit tests for the resolved shell environment policy.

Mirrors ``ShellEnvironmentPolicy::populate_env`` in Codex. The default must
inherit the full parent environment and skip the secret-name denylist; turning
the denylist back on is a per-operator decision expressed in settings.
"""

from __future__ import annotations

import os

import pytest

from app.agent_runtime.shell_environment import (
    NON_INHERITABLE_ENV_VARS,
    ShellEnvironmentPolicy,
    build_environment_from_settings,
    get_default_environment_policy,
    set_default_environment_policy,
)


@pytest.fixture(autouse=True)
def _restore_default_policy() -> None:
    """Reset the module-level default before and after each test."""

    original = get_default_environment_policy()
    set_default_environment_policy(None)
    try:
        yield
    finally:
        set_default_environment_policy(original)


# ---------- Default policy (Codex-aligned) ----------


def test_default_policy_inherits_full_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Out of the box, the default policy is a transparent passthrough."""

    monkeypatch.setenv("PLAIN_PATH", "harmless")
    monkeypatch.setenv("GH_TOKEN", "ghp_pass")
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_pass2")

    env = ShellEnvironmentPolicy().build()

    assert env["PLAIN_PATH"] == "harmless"
    assert env["GH_TOKEN"] == "ghp_pass"
    assert env["GITHUB_TOKEN"] == "ghp_pass2"


def test_default_policy_strips_only_non_inheritable_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    """Loom's own subsystem credentials never reach a child, even by default."""

    monkeypatch.setenv("LOOM_COMPUTER_API_KEY", "loomy")
    monkeypatch.setenv("LOOM_API_KEY", "loomy")
    monkeypatch.setenv("GH_TOKEN", "ghp_pass")
    # A provider key the user provisioned for themselves. Loom reads it too, but
    # Codex leaves the exact analogue (OPENAI_API_KEY) inheritable, and this
    # strip has no escape hatch -- see NON_INHERITABLE_ENV_VARS.
    monkeypatch.setenv("DASHSCOPE_API_KEY", "user-provisioned")

    env = ShellEnvironmentPolicy().build()

    assert env["GH_TOKEN"] == "ghp_pass"
    assert env["DASHSCOPE_API_KEY"] == "user-provisioned"
    assert "LOOM_COMPUTER_API_KEY" not in env
    assert "LOOM_API_KEY" not in env
    for name in NON_INHERITABLE_ENV_VARS:
        assert name not in env


# ---------- Turning the denylist back on ----------


def test_ignore_default_excludes_false_strips_secret_shaped_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_block")
    monkeypatch.setenv("GH_TOKEN", "ghp_block")
    monkeypatch.setenv("MY_OTHER_SECRET", "should_stay_out")
    monkeypatch.setenv("PLAIN_PATH", "harmless")

    env = ShellEnvironmentPolicy(ignore_default_excludes=False).build()

    assert "GITHUB_TOKEN" not in env
    assert "GH_TOKEN" not in env
    assert "MY_OTHER_SECRET" not in env
    assert env["PLAIN_PATH"] == "harmless"


def test_allow_secrets_bypasses_denylist_for_listed_names(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_pass")
    monkeypatch.setenv("STRIPE_API_KEY", "sk_keepmeout")

    policy = ShellEnvironmentPolicy(
        ignore_default_excludes=False,
        allow_secrets=("GH_TOKEN", "github_token"),
    )
    env = policy.build()

    assert env["GITHUB_TOKEN"] == "ghp_pass"
    assert "STRIPE_API_KEY" not in env


def test_allow_secrets_only_affects_listed_names(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_pass")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "still_secret")

    policy = ShellEnvironmentPolicy(
        ignore_default_excludes=False,
        allow_secrets=("GITHUB_TOKEN",),
    )
    env = policy.build()

    assert env["GITHUB_TOKEN"] == "ghp_pass"
    assert "AWS_SECRET_ACCESS_KEY" not in env


def test_allow_secrets_accepts_comma_string(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GIT_TOKEN", "t1")

    policy = ShellEnvironmentPolicy(
        ignore_default_excludes=False,
        allow_secrets="git_token, GIT_AUTHOR_NAME",
    )
    env = policy.build()

    assert env["GIT_TOKEN"] == "t1"


# ---------- Operator `exclude` ----------


def test_exclude_patterns_drop_named_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GH_HOST", "github.com")
    monkeypatch.setenv("MY_OWN_VAR", "keep")

    policy = ShellEnvironmentPolicy(exclude=("GH_*",))
    env = policy.build()

    assert "GH_HOST" not in env
    assert env["MY_OWN_VAR"] == "keep"


# ---------- Operator `set` ----------


def test_set_injects_new_values(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LOOM_TEST_SET", raising=False)

    policy = ShellEnvironmentPolicy(set_vars={"LOOM_TEST_SET": "hello"})
    env = policy.build()

    assert env["LOOM_TEST_SET"] == "hello"


def test_set_overrides_inherited_values_case_insensitively_on_windows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if os.name != "nt":
        pytest.skip("Windows-only case-folding behaviour")
    monkeypatch.setenv("GH_HOST", "old.example")

    policy = ShellEnvironmentPolicy(set_vars={"gh_host": "new.example"})
    env = policy.build()

    matched = [key for key in env if key.upper() == "GH_HOST"]
    assert matched
    assert env[matched[0]] == "new.example"
    assert len(matched) == 1


def test_set_rejects_invalid_keys() -> None:
    with pytest.raises(ValueError):
        ShellEnvironmentPolicy(set_vars={"BAD=NAME": "x"})
    with pytest.raises(ValueError):
        ShellEnvironmentPolicy(set_vars={"A" * 300: "x"})


# ---------- `include_only` ----------


def test_include_only_narrows_surviving_env(monkeypatch: pytest.MonkeyPatch) -> None:
    # GH_HOST is intentionally used here (not GH_TOKEN) because the Codex-style
    # denylist strips *TOKEN* in step 2, before include_only ever sees it.
    monkeypatch.setenv("PATH", "/usr/bin")
    monkeypatch.setenv("GH_HOST", "github.com")
    monkeypatch.setenv("UNRELATED", "drop")

    policy = ShellEnvironmentPolicy(
        ignore_default_excludes=False,
        include_only=("PATH", "GH_*"),
    )
    env = policy.build()

    assert env["PATH"] == "/usr/bin"
    assert env["GH_HOST"] == "github.com"
    assert "UNRELATED" not in env


# ---------- inherit ----------


def test_inherit_none_yields_empty_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GH_TOKEN", "ghp_pass")

    env = ShellEnvironmentPolicy(inherit="none", ignore_default_excludes=False).build()

    assert "GH_TOKEN" not in env
    # Windows adds a PATHEXT fallback so `gh`/`.cmd` shims resolve; non-Windows
    # gets an empty dict.
    if os.name != "nt":
        assert env == {}


def test_inherit_core_yields_platform_core_only(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PATH", "/usr/bin")
    monkeypatch.setenv("GH_TOKEN", "ghp_pass")
    monkeypatch.setenv("UNRELATED", "drop")

    env = ShellEnvironmentPolicy(inherit="core", ignore_default_excludes=False).build()

    assert env["PATH"] == "/usr/bin"
    assert "GH_TOKEN" not in env
    assert "UNRELATED" not in env


# ---------- Model-supplied `env` override ----------


def test_override_rejects_secret_shaped_keys_even_with_denylist_off() -> None:
    """``_INJECTION_GUARD_MARKERS`` is independent of ``ignore_default_excludes``."""

    policy = ShellEnvironmentPolicy()  # denylist OFF
    with pytest.raises(ValueError, match="secret-like"):
        policy.build({"STRIPE_API_KEY": "injected"})


def test_override_accepts_plain_keys() -> None:
    policy = ShellEnvironmentPolicy()
    env = policy.build({"MY_FLAG": "1"})
    assert env["MY_FLAG"] == "1"


def test_override_rejects_invalid_payload() -> None:
    policy = ShellEnvironmentPolicy()
    with pytest.raises(ValueError):
        policy.build({"BAD=NAME": "x"})


# ---------- Windows PATHEXT fallback ----------


def test_pathext_fallback_on_windows_when_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    if os.name != "nt":
        pytest.skip("Windows-only behaviour")
    monkeypatch.delenv("PATHEXT", raising=False)

    env = ShellEnvironmentPolicy(inherit="none").build()

    assert env["PATHEXT"]


# ---------- Settings plumbing ----------


def test_build_environment_from_settings_maps_all_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GH_TOKEN", "ghp_pass")
    monkeypatch.setenv("UNRELATED", "drop")

    policy = build_environment_from_settings(
        {
            "environment": {
                "inherit": "all",
                "ignoreDefaultExcludes": False,
                "exclude": ["UNRELATED"],
                "set": {"GH_TOKEN_HOST": "github.com"},
                "includeOnly": ["GH_*"],
                "passThroughEnvVars": ["GH_TOKEN"],
            }
        }
    )

    env = policy.build()

    assert policy.ignore_default_excludes is False
    assert policy.exclude == ("UNRELATED",)
    assert policy.include_only == ("GH_*",)
    assert policy.allow_secrets == ("GH_TOKEN",)
    assert env["GH_TOKEN"] == "ghp_pass"
    assert env["GH_TOKEN_HOST"] == "github.com"
    assert "UNRELATED" not in env


def test_build_environment_from_settings_falls_back_on_bad_payload() -> None:
    """A malformed field must not take settings sync down."""

    policy = build_environment_from_settings(
        {
            "environment": {
                "inherit": "bogus",
                "ignoreDefaultExcludes": "not-a-bool",
                "exclude": 42,           # _normalize_names rejects non-str/list
                "set": "not-a-dict",
                "includeOnly": object(), # _normalize_names rejects non-str/list
            }
        }
    )

    assert policy.inherit == "all"
    assert policy.ignore_default_excludes is True
    assert policy.exclude == ()
    assert policy.include_only == ()


def test_build_environment_from_settings_handles_missing_section() -> None:
    policy = build_environment_from_settings({})
    assert policy.inherit == "all"
    assert policy.ignore_default_excludes is True
    assert policy.exclude == ()


def test_build_environment_from_settings_partial_payload() -> None:
    """Only one field overrides; others stay at their Codex defaults."""

    policy = build_environment_from_settings(
        {"environment": {"ignoreDefaultExcludes": False}}
    )

    assert policy.ignore_default_excludes is False
    assert policy.inherit == "all"
    assert policy.exclude == ()


# ---------- Module-level singleton ----------


def test_set_default_environment_policy_round_trip(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GH_TOKEN", "t1")
    policy = ShellEnvironmentPolicy(ignore_default_excludes=False, allow_secrets=("GH_TOKEN",))
    set_default_environment_policy(policy)
    assert get_default_environment_policy() is policy
    env = get_default_environment_policy().build()
    assert env["GH_TOKEN"] == "t1"


def test_set_default_environment_policy_none_resets() -> None:
    set_default_environment_policy(None)
    assert get_default_environment_policy() == ShellEnvironmentPolicy()
