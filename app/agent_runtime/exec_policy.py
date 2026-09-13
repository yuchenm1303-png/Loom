from __future__ import annotations

"""Command-aware policy classification and per-process execution effect."""

import re
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import PurePosixPath, PureWindowsPath
from typing import Sequence

from .contracts import ToolEffect


EXEC_POLICY_VERSION = 3
_EXEC_EFFECT: ContextVar[ToolEffect | None] = ContextVar("loom_exec_effect", default=None)

_URL_RE = re.compile(r"^[a-z][a-z0-9+.-]*://", re.IGNORECASE)
_WINDOWS_EXECUTABLE_SUFFIXES = (".exe", ".com", ".cmd", ".bat")
_ARBITRARY_CODE_PROGRAMS = frozenset({"bash", "cmd", "cscript", "fish", "node", "perl", "php", "powershell", "pwsh", "python", "python3", "py", "ruby", "sh", "wscript", "zsh"})
_NETWORK_PROGRAMS = frozenset({"aria2c", "curl", "ftp", "git-lfs", "nc", "ncat", "netcat", "scp", "sftp", "ssh", "telnet", "wget"})
_PACKAGE_PROGRAMS = frozenset({"bun", "cargo", "composer", "gem", "go", "npm", "npx", "pip", "pip3", "pnpm", "poetry", "uv", "yarn"})
_SIMPLE_READ_ONLY = frozenset({"date", "hostname", "pwd", "uname", "whoami"})
# First release intentionally keeps generic file readers sensitive. A workspace
# symlink can make an apparently relative `cat file` dereference host data that
# lies outside the project even when writes are sandboxed.
_WORKSPACE_READERS = frozenset()
# These mutations either create/remove directory entries or are stopped by the
# read-only host bind when a symlink targets outside the writable workspace.
_WORKSPACE_MUTATORS = frozenset({"mkdir", "rm", "rmdir", "touch"})
_VERSION_ONLY = frozenset({"cargo", "git", "go", "node", "npm", "pip", "pip3", "pnpm", "python", "python3", "py", "rustc", "uv", "yarn"})
_GIT_READ_ONLY = frozenset({"blame", "describe", "diff", "log", "rev-parse", "show", "status"})
_GIT_NETWORK = frozenset({"clone", "fetch", "ls-remote", "pull", "push"})
_GIT_MUTATING_WORKTREE = frozenset({"apply"})
_GIT_UNSAFE_READ_FLAG_PREFIXES = ("--ext-diff", "--textconv", "--output")


@dataclass(frozen=True, slots=True)
class ExecPolicyEvaluation:
    effect: ToolEffect
    requires_network: bool
    matched_rule: str
    reason: str


@contextmanager
def exec_effect_scope(effect: ToolEffect):
    token = _EXEC_EFFECT.set(ToolEffect(effect))
    try:
        yield
    finally:
        _EXEC_EFFECT.reset(token)


def current_exec_effect() -> ToolEffect | None:
    return _EXEC_EFFECT.get()


def _is_bare_program(raw: str) -> bool:
    value = str(raw or "").strip()
    return bool(value and "/" not in value and "\\" not in value and value not in {".", ".."})


def _program_name(raw: str) -> str:
    value = str(raw or "").strip().casefold()
    for suffix in _WINDOWS_EXECUTABLE_SUFFIXES:
        if value.endswith(suffix):
            value = value[: -len(suffix)]
            break
    return value


def _path_escapes_workspace_shape(value: str) -> bool:
    raw = str(value or "").strip()
    if not raw or raw == "-" or _URL_RE.match(raw):
        return False
    posix = PurePosixPath(raw)
    windows = PureWindowsPath(raw)
    if posix.is_absolute() or windows.is_absolute() or bool(windows.drive):
        return True
    normalized = raw.replace("\\", "/")
    return any(part == ".." for part in normalized.split("/"))


def _looks_absolute_or_parent_path(token: str) -> bool:
    value = str(token or "").strip()
    if not value:
        return False
    if value.startswith("-"):
        if "=" not in value:
            return False
        _, embedded = value.split("=", 1)
        return _path_escapes_workspace_shape(embedded)
    return _path_escapes_workspace_shape(value)


def _workspace_relative_arguments(arguments: Sequence[str]) -> bool:
    return not any(_looks_absolute_or_parent_path(value) for value in arguments)


def _contains_network_target(arguments: Sequence[str]) -> bool:
    return any(_URL_RE.match(str(value or "").strip()) for value in arguments)


def _version_only(program: str, arguments: tuple[str, ...]) -> bool:
    return bool(program in _VERSION_ONLY and len(arguments) == 1 and arguments[0].casefold() in {"-v", "--version", "version"})


def _git_subcommand(arguments: tuple[str, ...]) -> tuple[str, tuple[str, ...]]:
    if not arguments:
        return "", ()
    index = 0
    while index < len(arguments) and arguments[index] in {"--no-pager", "--paginate"}:
        index += 1
    if index >= len(arguments) or arguments[index].startswith("-"):
        return "", ()
    return arguments[index].casefold(), arguments[index + 1 :]


def _git_has_unsafe_read_flag(arguments: tuple[str, ...]) -> bool:
    for value in arguments:
        lowered = value.casefold()
        if any(lowered == prefix or lowered.startswith(prefix + "=") for prefix in _GIT_UNSAFE_READ_FLAG_PREFIXES):
            return True
    return False


def _git_policy(arguments: tuple[str, ...]) -> ExecPolicyEvaluation:
    subcommand, rest = _git_subcommand(arguments)
    if not subcommand:
        return ExecPolicyEvaluation(ToolEffect.SENSITIVE, True, "git:ambiguous", "Git invocation uses global options or an ambiguous command shape.")
    if subcommand in _GIT_NETWORK:
        effect = ToolEffect.READ_ONLY if subcommand == "ls-remote" else ToolEffect.SENSITIVE
        return ExecPolicyEvaluation(effect, True, f"git:{subcommand}", f"git {subcommand} can access a remote network endpoint.")
    if subcommand in _GIT_READ_ONLY:
        if _git_has_unsafe_read_flag(rest):
            return ExecPolicyEvaluation(ToolEffect.SENSITIVE, True, f"git:{subcommand}:extended", f"git {subcommand} enables an external helper or output-file side effect.")
        return ExecPolicyEvaluation(ToolEffect.READ_ONLY, False, f"git:{subcommand}", f"git {subcommand} is a recognized repository inspection command.")
    if subcommand in _GIT_MUTATING_WORKTREE and _workspace_relative_arguments(rest):
        return ExecPolicyEvaluation(ToolEffect.MUTATING, False, f"git:{subcommand}", f"git {subcommand} changes workspace files without requiring remote access.")
    return ExecPolicyEvaluation(ToolEffect.SENSITIVE, True, f"git:{subcommand}", f"git {subcommand} is not in Loom's auto-classified command set.")


class ExecPolicy:
    """Conservatively derive effective file/network capabilities for one argv."""

    version = EXEC_POLICY_VERSION

    def classify(self, raw_argv: object) -> ExecPolicyEvaluation:
        if not isinstance(raw_argv, (list, tuple)) or not raw_argv:
            return ExecPolicyEvaluation(ToolEffect.SENSITIVE, True, "invalid", "Command argv is missing or invalid; keep the request sensitive.")
        argv = tuple(str(value) for value in raw_argv)
        if not _is_bare_program(argv[0]):
            return ExecPolicyEvaluation(ToolEffect.SENSITIVE, True, "path-qualified-program", "Path-qualified executables are not trusted by name and may execute arbitrary code.")
        program = _program_name(argv[0])
        arguments = argv[1:]
        network_target = _contains_network_target(arguments)
        if _version_only(program, arguments):
            return ExecPolicyEvaluation(ToolEffect.READ_ONLY, False, f"{program}:version", f"{program} version query is read-only.")
        if program == "git":
            result = _git_policy(arguments)
            if network_target and not result.requires_network:
                return ExecPolicyEvaluation(ToolEffect.SENSITIVE, True, f"{result.matched_rule}:url", "A URL appears in an otherwise local Git command; require explicit network approval.")
            return result
        if program in _NETWORK_PROGRAMS:
            return ExecPolicyEvaluation(ToolEffect.SENSITIVE, True, f"network:{program}", f"{program} is a network-capable executable.")
        if program in _PACKAGE_PROGRAMS:
            return ExecPolicyEvaluation(ToolEffect.SENSITIVE, True, f"package:{program}", f"{program} can execute package hooks and access package registries.")
        if program in _ARBITRARY_CODE_PROGRAMS:
            return ExecPolicyEvaluation(ToolEffect.SENSITIVE, True, f"interpreter:{program}", f"{program} can execute arbitrary code and open network connections.")
        if program in _SIMPLE_READ_ONLY and not arguments:
            return ExecPolicyEvaluation(ToolEffect.READ_ONLY, False, f"inspect:{program}", f"{program} is a recognized host inspection command with no operands.")
        if program in _WORKSPACE_READERS and _workspace_relative_arguments(arguments):
            return ExecPolicyEvaluation(ToolEffect.READ_ONLY, network_target, f"read:{program}", f"{program} is read-only and all path-like operands stay workspace-relative.")
        if program in _WORKSPACE_MUTATORS and _workspace_relative_arguments(arguments):
            return ExecPolicyEvaluation(ToolEffect.MUTATING, network_target, f"mutate:{program}", f"{program} mutates files but all path-like operands stay workspace-relative.")
        return ExecPolicyEvaluation(ToolEffect.SENSITIVE, True, f"unknown:{program or 'program'}", "Command is not safely classifiable and may execute code or access the network.")


__all__ = ["EXEC_POLICY_VERSION", "ExecPolicy", "ExecPolicyEvaluation", "current_exec_effect", "exec_effect_scope"]
