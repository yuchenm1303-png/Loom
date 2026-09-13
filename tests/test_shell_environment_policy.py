"""Unit tests for the resolved shell environment policy."""

from __future__ import annotations

import pytest

from app.agent_runtime.shell_environment import (
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


def test_default_policy_strips_secret_shaped_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_keepmeout")
    monkeypatch.setenv("MY_OTHER_SECRET", "should_stay_out")
    monkeypatch.setenv("PLAIN_PATH", "harmless")

    env = ShellEnvironmentPolicy().build()

    assert "GITHUB_TOKEN" not in env
    assert "MY_OTHER_SECRET" not in env
    assert env["PLAIN_PATH"] == "harmless"


def test_allow_secrets_passes_through_listed_names(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_pass")
    monkeypatch.setenv("GH_TOKEN", "ghp_pass2")
    monkeypatch.setenv("STRIPE_API_KEY", "sk_keepmeout")

    policy = ShellEnvironmentPolicy(allow_secrets=("GH_TOKEN", "github_token"))
    env = policy.build()

    assert env["GITHUB_TOKEN"] == "ghp_pass"
    assert env["GH_TOKEN"] == "ghp_pass2"
    assert "STRIPE_API_KEY" not in env


def test_allow_secrets_only_affects_listed_names(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_pass")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "still_secret")

    policy = ShellEnvironmentPolicy(allow_secrets=("GITHUB_TOKEN",))
    env = policy.build()

    assert env["GITHUB_TOKEN"] == "ghp_pass"
    assert "AWS_SECRET_ACCESS_KEY" not in env


def test_allow_secrets_rejects_secret_overrides() -> None:
    policy = ShellEnvironmentPolicy(allow_secrets=("GITHUB_TOKEN",))
    with pytest.raises(ValueError, match="secret-like"):
        policy.build({"STRIPE_API_KEY": "injected"})


def test_allow_secrets_accepts_comma_string(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GIT_TOKEN", "t1")

    policy = ShellEnvironmentPolicy(allow_secrets="git_token, GIT_AUTHOR_NAME")
    env = policy.build()

    assert env["GIT_TOKEN"] == "t1"


def test_build_environment_from_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_pass")
    monkeypatch.setenv("STRIPE_API_KEY", "sk_out")

    policy = build_environment_from_settings(
        {"environment": {"passThroughEnvVars": ["github_token", "GH_TOKEN"]}}
    )
    env = policy.build()

    assert env["GITHUB_TOKEN"] == "ghp_pass"
    assert "STRIPE_API_KEY" not in env
    assert policy.allow_secrets == ("GITHUB_TOKEN", "GH_TOKEN")


def test_build_environment_from_settings_handles_missing_section() -> None:
    policy = build_environment_from_settings({})
    assert policy.allow_secrets == ()


def test_build_environment_from_settings_rejects_invalid_payload() -> None:
    policy = build_environment_from_settings({"environment": {"passThroughEnvVars": 42}})
    # Falls back to empty allowlist instead of crashing settings sync.
    assert policy.allow_secrets == ()


def test_set_default_environment_policy_round_trip(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GH_TOKEN", "t1")
    policy = ShellEnvironmentPolicy(allow_secrets=("GH_TOKEN",))
    set_default_environment_policy(policy)
    assert get_default_environment_policy() is policy
    env = get_default_environment_policy().build()
    assert env["GH_TOKEN"] == "t1"


def test_default_policy_remains_strict_when_settings_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    """Sanity: empty settings payload must not weaken the denylist."""

    monkeypatch.setenv("GITHUB_TOKEN", "ghp_block")
    set_default_environment_policy(build_environment_from_settings({}))
    env = get_default_environment_policy().build()
    assert "GITHUB_TOKEN" not in env
