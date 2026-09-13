"""Resolved shell environment policy, shared by exec and approval identity.

The derivation mirrors Codex's ``populate_env``
(``codex-rs/protocol/src/shell_environment.rs``) step for step, so a command
Loom spawns sees the environment Codex would have built for it:

1. seed the map from ``inherit`` (``all`` / ``core`` / ``none``)
2. unless ``ignore_default_excludes``, drop ``*KEY*`` / ``*SECRET*`` / ``*TOKEN*``
3. drop ``exclude`` matches
4. apply the operator's ``set`` entries
5. when ``include_only`` is non-empty, keep only what matches it
6. strip Loom's own launch-context credentials, unconditionally

Codex defaults ``ignore_default_excludes`` to ``True``: out of the box it
inherits the full parent environment and does **not** filter secret-shaped
names. Loom matches that default, which is what lets ``GH_TOKEN`` reach ``git``
and ``gh`` in a spawned process. Set ``environment.ignoreDefaultExcludes`` to
``false`` to turn the denylist back on.

Two behaviours here have no Codex counterpart, because Codex has no equivalent
channel:

``overrides``
    The ``env`` object from the model's ``exec`` tool call. Codex's shell tool
    exposes no ``env`` parameter at all, so nothing in Codex corresponds to
    this. It is applied after step 5 and screened against
    ``_INJECTION_GUARD_MARKERS`` so a model cannot introduce a credential the
    host did not already expose.

``allow_secrets``
    Named holes in the step-2 excludes, so an operator can keep the denylist on
    and still pass specific names through.
"""
from __future__ import annotations

import fnmatch
import os
from dataclasses import dataclass, field
from typing import Mapping


_INHERIT_MODES = frozenset({"all", "core", "none"})

# Codex's default exclude patterns, verbatim. Narrower than the marker list
# below on purpose: this one decides what the *host* hands down, and widening it
# past Codex is what previously stripped GitHub credentials.
_DEFAULT_EXCLUDE_PATTERNS = ("*KEY*", "*SECRET*", "*TOKEN*")

# Screens the model-supplied override channel only. Deliberately broader than
# the host-inheritance excludes: the risk there is a model smuggling a
# credential into a child process, not an operator's own environment leaking.
_INJECTION_GUARD_MARKERS = ("API_KEY", "TOKEN", "SECRET", "PASSWORD", "PASSWD", "PRIVATE_KEY")

# Loom's analogue of Codex's NON_INHERITABLE_ENV_VARS: credentials that exist
# because Loom is running. A model-reachable child has no reason to see them, so
# they are removed after every other step, including `set`.
#
# Scope follows Codex, which lists its own CODEX_*/identity tokens but pointedly
# leaves OPENAI_API_KEY alone. Generic provider keys a user provisioned for
# themselves -- OPENAI_API_KEY, DASHSCOPE_API_KEY, TAVILY_API_KEY,
# BRAVE_SEARCH_API_KEY, AI_API_KEY -- stay inheritable even though Loom also
# reads them, because a script in the workspace may legitimately need one and
# nothing here can be overridden: `set` is applied before this strip and the
# exec override channel refuses secret-shaped names. An over-broad entry is a
# hole with no escape hatch, so only Loom's own namespace belongs here.
NON_INHERITABLE_ENV_VARS = (
    "LOOM_API_KEY",
    "LOOM_BROWSER_EXTENSION_TOKEN",
    "LOOM_COMPUTER_API_KEY",
    "LOOM_UFO_API_KEY",
    "LOOM_WEB_SEARCH_API_KEY",
)

_UNIX_CORE_ENV_VARS = (
    "PATH", "SHELL", "TMPDIR", "TEMP", "TMP", "HOME", "LANG", "LC_ALL",
    "LC_CTYPE", "LOGNAME", "USER",
)

_WINDOWS_CORE_ENV_VARS = (
    # Core path resolution
    "PATH", "PATHEXT",
    # Shell and system roots
    "SHELL", "COMSPEC", "SYSTEMROOT", "WINDIR", "SYSTEMDRIVE",
    # User context and profiles
    "USERNAME", "USERDOMAIN", "USERPROFILE", "HOMEDRIVE", "HOMEPATH",
    # Program locations
    "PROGRAMFILES", "PROGRAMFILES(X86)", "PROGRAMW6432", "PROGRAMDATA",
    # App data and caches
    "LOCALAPPDATA", "APPDATA",
    # Temp locations
    "TEMP", "TMP", "TMPDIR",
    # Common shells/pwsh hints
    "POWERSHELL", "PWSH",
)

_WINDOWS_DEFAULT_PATHEXT = ".COM;.EXE;.BAT;.CMD"


def _core_env_vars() -> tuple[str, ...]:
    return _WINDOWS_CORE_ENV_VARS if os.name == "nt" else _UNIX_CORE_ENV_VARS


def _normalize_names(values: object, *, upper: bool = True) -> tuple[str, ...]:
    """Coerce a settings payload into a deduped tuple of names or patterns.

    Accepts a list of strings or a comma-separated string; empty / ``None``
    yields an empty tuple. Used for ``allow_secrets``, ``exclude`` and
    ``include_only``, all of which match case-insensitively.
    """

    if not values:
        return ()
    if isinstance(values, str):
        parts = [segment.strip() for segment in values.split(",")]
    elif isinstance(values, (list, tuple)):
        parts = [str(item).strip() for item in values]
    else:
        raise ValueError("expected a list of names or a comma-separated string")
    out: list[str] = []
    seen: set[str] = set()
    for name in parts:
        if not name:
            continue
        candidate = name.upper() if upper else name
        marker = candidate.upper()
        if marker in seen:
            continue
        seen.add(marker)
        out.append(candidate)
    return tuple(out)


def _normalize_set(values: object) -> tuple[tuple[str, str], ...]:
    """Coerce the operator ``set`` mapping into hashable pairs.

    Keys keep the case the operator wrote, matching Codex: ``set = {gh_host =
    "..."}`` produces a ``gh_host`` entry even when the inherited environment
    spelled it ``GH_HOST``.
    """

    if not values:
        return ()
    if isinstance(values, Mapping):
        items = list(values.items())
    elif isinstance(values, (list, tuple)):
        # The normalized form is itself a sequence of pairs, so a policy has to
        # round-trip through its own field: `replace(policy, ...)` and settings
        # sync both rebuild from an existing `set_vars`.
        items = []
        for entry in values:
            if not isinstance(entry, (list, tuple)) or len(entry) != 2:
                raise ValueError("set entries must be (name, value) pairs")
            items.append((entry[0], entry[1]))
    else:
        raise ValueError("set must be a mapping of environment names to values")
    out: list[tuple[str, str]] = []
    for key, value in items:
        name = str(key).strip()
        if not name:
            continue
        text = str(value)
        if "=" in name or "\0" in name or "\0" in text or len(name) > 256:
            raise ValueError(f"invalid environment set entry: {key!r}")
        out.append((name, text))
    return tuple(out)


def _matches_any(name: str, patterns: tuple[str, ...]) -> bool:
    upper = name.upper()
    return any(fnmatch.fnmatchcase(upper, pattern.upper()) for pattern in patterns)


def is_non_inheritable_env_var(name: str) -> bool:
    upper = str(name or "").upper()
    return any(upper == restricted for restricted in NON_INHERITABLE_ENV_VARS)


@dataclass(frozen=True, slots=True)
class ShellEnvironmentPolicy:
    """Codex-equivalent shell environment policy.

    Field defaults match ``ShellEnvironmentPolicy::default()`` in Codex:
    inherit everything, filter nothing.
    """

    inherit: str = "all"
    # Codex defaults this to True, i.e. the *KEY*/*SECRET*/*TOKEN* denylist is
    # off unless the operator asks for it.
    ignore_default_excludes: bool = True
    exclude: tuple[str, ...] = ()
    set_vars: tuple[tuple[str, str], ...] = field(default_factory=tuple)
    include_only: tuple[str, ...] = ()
    # Loom extension: names that survive step 2 when the denylist is on.
    allow_secrets: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self):
        if self.inherit not in _INHERIT_MODES:
            raise ValueError("environment inherit must be all, core, or none")
        object.__setattr__(self, "ignore_default_excludes", bool(self.ignore_default_excludes))
        object.__setattr__(self, "exclude", _normalize_names(self.exclude))
        object.__setattr__(self, "include_only", _normalize_names(self.include_only))
        object.__setattr__(self, "allow_secrets", _normalize_names(self.allow_secrets))
        object.__setattr__(self, "set_vars", _normalize_set(self.set_vars))

    def _default_excluded(self, name: str) -> bool:
        if name.upper() in self.allow_secrets:
            return False
        return _matches_any(name, _DEFAULT_EXCLUDE_PATTERNS)

    def _injection_blocked(self, name: str) -> bool:
        """Screen a model-supplied override key.

        ``allow_secrets`` does not open this channel: opting a name in means the
        host's own value may pass down, not that the model may choose one.
        """

        upper = str(name or "").upper()
        if not upper:
            return False
        return any(marker in upper for marker in _INJECTION_GUARD_MARKERS)

    @staticmethod
    def _assign(env: dict[str, str], key: str, value: str) -> None:
        # Windows environment variable identity is case-insensitive, so a `set`
        # of `gh_host` must displace an inherited `GH_HOST` rather than sit
        # beside it.
        if os.name == "nt":
            for existing in [name for name in env if name.casefold() == key.casefold()]:
                del env[existing]
        env[key] = value

    def _seed(self) -> dict[str, str]:
        if self.inherit == "none":
            return {}
        if self.inherit == "core":
            core = _core_env_vars()
            return {
                name: value
                for name, value in os.environ.items()
                if any(name.upper() == allowed for allowed in core)
            }
        return dict(os.environ)

    def build(self, overrides: Mapping[str, object] | None = None) -> dict[str, str]:
        # Step 1 - seed from the inherit strategy.
        env = self._seed()

        # Step 2 - default secret excludes, unless disabled (Codex disables them
        # by default).
        if not self.ignore_default_excludes:
            env = {name: value for name, value in env.items() if not self._default_excluded(name)}

        # Step 3 - operator excludes.
        if self.exclude:
            env = {
                name: value
                for name, value in env.items()
                if not _matches_any(name, self.exclude)
            }

        # Step 4 - operator set entries.
        for key, value in self.set_vars:
            self._assign(env, key, value)

        # Step 5 - include_only narrows whatever survived.
        if self.include_only:
            env = {
                name: value
                for name, value in env.items()
                if _matches_any(name, self.include_only)
            }

        # Loom-only: the model's `exec` env argument. Applied last and rejected
        # loudly rather than dropped, so a denied key surfaces as a tool error
        # instead of a command that silently ran without it.
        for key, value in (overrides or {}).items():
            key, value = str(key), str(value)
            if not key or "=" in key or "\0" in key or "\0" in value or len(key) > 1024 or len(value) > 64_000:
                raise ValueError("invalid environment override")
            if self._injection_blocked(key):
                raise ValueError(f"secret-like environment override is not allowed: {key}")
            if _matches_any(key, self.exclude) or (
                self.include_only and not _matches_any(key, self.include_only)
            ):
                raise ValueError(f"environment override denied by policy: {key}")
            self._assign(env, key, value)

        # Step 6 - Loom's own launch-context credentials never reach a child,
        # whatever the rest of the policy said.
        for name in [name for name in env if is_non_inheritable_env_var(name)]:
            del env[name]

        # Codex applies the same Windows fallback: a child launched without
        # PATHEXT cannot resolve .cmd/.bat shims, which is how `gh` ships.
        if os.name == "nt" and not any(name.upper() == "PATHEXT" for name in env):
            env["PATHEXT"] = _WINDOWS_DEFAULT_PATHEXT

        return env


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
    """Construct a policy from a settings payload.

    A malformed field falls back to that field's default rather than raising:
    settings sync runs on every settings change and on app start, and a bad
    ``exclude`` entry must not take the runtime down with it.
    """

    env_section: object = None
    if isinstance(settings, Mapping):
        env_section = settings.get("environment")
    if not isinstance(env_section, Mapping):
        env_section = {}

    fallback = base or ShellEnvironmentPolicy()

    inherit = str(env_section.get("inherit") or fallback.inherit).strip().lower()
    if inherit not in _INHERIT_MODES:
        inherit = fallback.inherit

    raw_ignore = env_section.get("ignoreDefaultExcludes")
    ignore_default_excludes = (
        bool(raw_ignore) if isinstance(raw_ignore, bool) else fallback.ignore_default_excludes
    )

    def _names(key: str, current: tuple[str, ...]) -> tuple[str, ...]:
        if key not in env_section:
            return current
        try:
            return _normalize_names(env_section.get(key))
        except ValueError:
            return current

    # Pass the raw ``set`` mapping through; ShellEnvironmentPolicy.__post_init__
    # is the single normalization point. Pre-normalizing here would re-enter
    # _normalize_set on the resulting tuple-of-tuples, which it rejects as not
    # being a Mapping.
    raw_set = env_section.get("set")
    if "set" not in env_section or not isinstance(raw_set, Mapping):
        set_vars: object = fallback.set_vars
    else:
        set_vars = raw_set

    return ShellEnvironmentPolicy(
        inherit=inherit,
        ignore_default_excludes=ignore_default_excludes,
        exclude=_names("exclude", fallback.exclude),
        set_vars=set_vars,
        include_only=_names("includeOnly", fallback.include_only),
        allow_secrets=_names("passThroughEnvVars", fallback.allow_secrets),
    )
