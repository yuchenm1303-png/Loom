"""Resolved shell environment policy, shared by exec and approval identity."""
from __future__ import annotations

import fnmatch
import os
from dataclasses import dataclass, field
from typing import Mapping


_BASE = {"PATH", "PATHEXT", "SYSTEMROOT", "WINDIR", "COMSPEC", "HOME", "USERPROFILE", "TEMP", "TMP", "LANG"}
_SECRETS = ("API_KEY", "TOKEN", "SECRET", "PASSWORD", "PASSWD", "PRIVATE_KEY")


def _normalize_allowlist(values: object) -> tuple[str, ...]:
    """Coerce settings payload into a tuple of uppercase, deduped allowlist names.

    Accepts either a list of strings or a comma-separated string. Empty / None
    returns an empty tuple. The result is used to bypass the secret-name denylist
    for the env vars the operator has explicitly opted in to.
    """

    if not values:
        return ()
    if isinstance(values, str):
        parts = [segment.strip() for segment in values.split(",")]
    elif isinstance(values, (list, tuple)):
        parts = [str(item).strip() for item in values]
    else:
        raise ValueError("allow_secrets must be a list of names or a comma-separated string")
    out: list[str] = []
    seen: set[str] = set()
    for name in parts:
        if not name:
            continue
        upper = name.upper()
        if upper in seen:
            continue
        seen.add(upper)
        out.append(upper)
    return tuple(out)


@dataclass(frozen=True, slots=True)
class ShellEnvironmentPolicy:
    inherit: str = "all"
    include_only: tuple[str, ...] = ()
    exclude: tuple[str, ...] = ()
    # Explicit operator allowlist that bypasses the secret-name denylist.
    # Only the names listed here (case-insensitive) are passed through to the
    # child process; every other secret-shaped env var remains stripped. This
    # is how GitHub / Git auth tokens reach the agent sandbox without
    # weakening the broader secret-leak protection.
    allow_secrets: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self):
        if self.inherit not in {"all", "core", "none"}:
            raise ValueError("environment inherit must be all, core, or none")
        object.__setattr__(self, "include_only", tuple(self.include_only))
        object.__setattr__(self, "exclude", tuple(self.exclude))
        object.__setattr__(self, "allow_secrets", _normalize_allowlist(self.allow_secrets))

    def _secret_blocked(self, name: str) -> bool:
        upper = str(name or "").upper()
        if not upper:
            return False
        if any(marker in upper for marker in _SECRETS):
            return upper not in self.allow_secrets
        return False

    def build(self, overrides: Mapping[str, object] | None = None) -> dict[str, str]:
        def matches(name, patterns):
            return any(fnmatch.fnmatchcase(name.upper(), p.upper()) for p in patterns)
        output = {}
        for name, value in os.environ.items():
            if self.inherit == "none" or (self.inherit == "core" and name.upper() not in _BASE):
                continue
            if self._secret_blocked(name) or matches(name, self.exclude):
                continue
            if self.include_only and not matches(name, self.include_only):
                continue
            output[name] = value
        for key, value in (overrides or {}).items():
            key, value = str(key), str(value)
            if not key or "=" in key or "\0" in key or "\0" in value or len(key) > 1024 or len(value) > 64_000:
                raise ValueError("invalid environment override")
            if self._secret_blocked(key):
                raise ValueError(f"secret-like environment override is not allowed: {key}")
            if matches(key, self.exclude) or (self.include_only and not matches(key, self.include_only)):
                raise ValueError(f"environment override denied by policy: {key}")
            # Windows environment variable identity is case-insensitive.
            if os.name == "nt":
                output = {k: v for k, v in output.items() if k.casefold() != key.casefold()}
            output[key] = value
        return output


_DEFAULT_ENVIRONMENT_POLICY: ShellEnvironmentPolicy = ShellEnvironmentPolicy()


def get_default_environment_policy() -> ShellEnvironmentPolicy:
    """Return the process-wide default policy synced from settings."""

    return _DEFAULT_ENVIRONMENT_POLICY


def set_default_environment_policy(policy: ShellEnvironmentPolicy | None) -> None:
    """Replace the process-wide default policy. Pass ``None`` to reset."""

    global _DEFAULT_ENVIRONMENT_POLICY
    _DEFAULT_ENVIRONMENT_POLICY = policy or ShellEnvironmentPolicy()


def build_environment_from_settings(
    settings: Mapping[str, object] | None,
    *,
    base: ShellEnvironmentPolicy | None = None,
) -> ShellEnvironmentPolicy:
    """Construct a policy from a settings payload, falling back to ``base``/default."""

    env_section: object = None
    if isinstance(settings, Mapping):
        env_section = settings.get("environment")
    allow: object = None
    if isinstance(env_section, Mapping):
        allow = env_section.get("passThroughEnvVars")
    try:
        names = _normalize_allowlist(allow)
    except ValueError:
        names = ()
    base_policy = base or get_default_environment_policy()
    if not names and not getattr(base_policy, "allow_secrets", ()):
        return base_policy
    return ShellEnvironmentPolicy(
        inherit=base_policy.inherit,
        include_only=base_policy.include_only,
        exclude=base_policy.exclude,
        allow_secrets=names,
    )
