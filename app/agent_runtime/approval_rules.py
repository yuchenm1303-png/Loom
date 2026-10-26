"""Scoped approval rules: command prefixes remembered for a session or a project.

An approval used to be one yes or no for one call. Two wider grants are added:

* a **session rule** lives in memory next to the exact-command cache and ends
  with the session: a command prefix for ``exec``, a tool name for every other
  tool;
* a **project rule** is a command prefix saved under the project's root
  directory in ``<home>/approval-rules.json``, so it survives restarts.

Prefix rules are matched on the command the approval layer already
canonicalizes (``canonicalize_command_for_approval``). They are never taken from
the client: the runtime derives the prefix from the exact, binding-checked call
that is waiting for review and offers it back, so a response can only choose
whether to remember what the user was shown.

Safety limits that keep a remembered prefix from becoming a blank cheque:

* shells, interpreters, ``env`` and similar launchers are never offered as a
  prefix (ported from Codex's ``BANNED_PREFIX_SUGGESTIONS``, extended with
  Windows launchers), because ``powershell`` or ``python`` as a prefix means
  "any code";
* a rule keeps the sandbox mode it was granted in: ``policy`` (whatever the
  ambient sandbox does) or ``unsandboxed`` (run outside the sandbox, granted by
  approving an escalation), and an ``unsandboxed`` rule is ignored where the
  permission policy would not allow an escalation;
* a bare program name matches by name only, a program given as a path matches
  only that path, and a call with explicit environment overrides matches
  nothing (``PATH`` could otherwise swap the executable under a saved rule).
"""

from __future__ import annotations

import json
import os
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Iterable, Sequence

from .command_approval import canonicalize_command_for_approval


MAX_PREFIX_TOKENS = 8
MAX_TOKEN_CHARS = 240
MAX_PROJECT_RULES = 200
RULES_FILE = "approval-rules.json"
_FILE_VERSION = 1
_OPAQUE_PREFIX = "__codex_"
_EXECUTABLE_SUFFIXES = (".exe", ".cmd", ".bat", ".com")


class RuleSandbox(str, Enum):
    POLICY = "policy"
    UNSANDBOXED = "unsandboxed"


class ApprovalRuleError(RuntimeError):
    """The saved rules could not be read or written."""


def executable_key(token: str) -> str:
    """One spelling for a program: ``Git.EXE`` and ``git`` are the same program.

    A token with a path separator is a specific file, not a name looked up on
    ``PATH``, so it keeps its (case-normalized) path and never equals a bare name.
    """

    text = str(token).strip()
    if "/" in text or "\\" in text:
        return os.path.normcase(os.path.normpath(text))
    lowered = text.casefold()
    for suffix in _EXECUTABLE_SUFFIXES:
        if lowered.endswith(suffix) and len(lowered) > len(suffix):
            return lowered[: -len(suffix)]
    return lowered


def normalized_command(argv: Sequence[str]) -> tuple[str, ...]:
    """The canonical approval form of ``argv`` with a normalized program name."""

    canonical = canonicalize_command_for_approval(tuple(str(value) for value in argv))
    if not canonical:
        return ()
    if canonical[0].startswith(_OPAQUE_PREFIX):
        return canonical
    return (executable_key(canonical[0]), *canonical[1:])


def is_opaque(command: Sequence[str]) -> bool:
    """A shell/PowerShell script kept verbatim: only an exact rule may match it."""

    return bool(command) and str(command[0]).startswith(_OPAQUE_PREFIX)


def _normalize_banned(tokens: Sequence[str]) -> tuple[str, ...]:
    return (executable_key(tokens[0]), *(token.casefold() for token in tokens[1:]))


# Ported from Codex's BANNED_PREFIX_SUGGESTIONS, plus Windows launchers. A prefix
# on this list would approve running arbitrary code or arbitrary subcommands.
_BANNED_SOURCE: tuple[tuple[str, ...], ...] = (
    ("/bin/bash",), ("/bin/bash", "-c"), ("/bin/bash", "-lc"),
    ("/bin/sh",), ("/bin/sh", "-c"), ("/bin/sh", "-lc"),
    ("/bin/zsh",), ("/bin/zsh", "-c"), ("/bin/zsh", "-lc"),
    ("Rscript",),
    ("bash",), ("bash", "-c"), ("bash", "-lc"),
    ("bun",), ("bun", "-e"), ("bun", "run"),
    ("cmd",), ("cmd", "/c"), ("cmd", "/k"),
    ("dash",), ("dash", "-c"),
    ("deno",), ("deno", "eval"),
    ("env",),
    ("fish",), ("fish", "-c"),
    ("git",),
    ("julia",), ("julia", "-e"),
    ("ksh",), ("ksh", "-c"),
    ("lua",), ("lua", "-e"),
    ("node",), ("node", "-e"),
    ("nodejs",), ("nodejs", "-e"),
    ("npm", "run"),
    ("osascript",),
    ("perl",), ("perl", "-e"),
    ("php",), ("php", "-r"),
    ("pnpm", "run"),
    ("powershell",), ("powershell", "-Command"), ("powershell", "-EncodedCommand"),
    ("powershell", "-File"), ("powershell", "-c"),
    ("pwsh",), ("pwsh", "-Command"), ("pwsh", "-EncodedCommand"), ("pwsh", "-File"),
    ("pwsh", "-c"), ("pwsh", "-e"), ("pwsh", "-ec"), ("pwsh", "-f"),
    ("py",), ("py", "-3"),
    ("pypy",), ("pypy3",),
    ("python",), ("python", "-"), ("python", "-c"),
    ("python3",), ("python3", "-"), ("python3", "-c"),
    ("pythonw",), ("pyw",),
    ("rm",),
    ("ruby",), ("ruby", "-e"),
    ("sh",), ("sh", "-c"), ("sh", "-lc"),
    # Windows launchers that run whatever they are handed.
    ("wsl",), ("wscript",), ("cscript",), ("mshta",), ("rundll32",), ("regsvr32",),
    ("msiexec",), ("start",), ("runas",), ("schtasks",), ("reg",), ("sc",), ("net",),
    ("netsh",), ("takeown",), ("icacls",), ("del",), ("erase",), ("rmdir",), ("rd",),
    ("format",), ("diskpart",), ("bcdedit",), ("xargs",), ("sudo",), ("su",),
)
BANNED_PREFIXES: frozenset[tuple[str, ...]] = frozenset(_normalize_banned(item) for item in _BANNED_SOURCE)


def is_banned_prefix(prefix: Sequence[str]) -> bool:
    return _normalize_banned(tuple(prefix)) in BANNED_PREFIXES if prefix else True


def _is_word(token: str) -> bool:
    """A subcommand-looking token: ``status`` yes, ``-m``, ``a.txt``, ``x=1`` no."""

    if not token or not token[0].isalpha():
        return False
    return all(char.isalnum() or char in "_-" for char in token)


def validate_prefix(prefix: Sequence[str], *, exact: bool = False) -> tuple[str, ...]:
    """Return the normalized prefix or raise ``ValueError``; never trust a caller."""

    tokens = tuple(str(value) for value in prefix)
    if not tokens or any(not token for token in tokens):
        raise ValueError("a prefix rule needs at least one non-empty token")
    if len(tokens) > MAX_PREFIX_TOKENS or any(len(token) > MAX_TOKEN_CHARS for token in tokens):
        raise ValueError("prefix rule is too long")
    if any("\x00" in token for token in tokens):
        raise ValueError("prefix rule contains a NUL character")
    normalized = (executable_key(tokens[0]), *tokens[1:])
    if is_opaque(tokens):
        if not exact:
            raise ValueError("a shell script can only be remembered as an exact command")
        return tokens
    if not exact and is_banned_prefix(normalized):
        raise ValueError(f"{' '.join(tokens)!r} is too broad to remember as a command prefix")
    return normalized


def derive_prefix_rule(
    argv: Sequence[str],
    *,
    proposed: Sequence[str] = (),
) -> tuple[str, ...] | None:
    """The prefix worth offering for ``argv``, or ``None`` when none is safe.

    A prefix the model proposed (``prefix_rule``) is used when it is a real
    prefix of the command and not banned. Otherwise the program plus up to two
    leading subcommand-looking words, the shortest of those that is not banned:
    ``git status -s`` -> ``git status``; ``npm run build`` -> ``npm run build``;
    ``python -m pytest`` and ``node x.js`` -> nothing.
    """

    command = normalized_command(argv)
    if not command or is_opaque(command):
        return None
    wanted = tuple(str(value) for value in proposed)
    if wanted:
        try:
            candidate = validate_prefix(wanted)
        except ValueError:
            candidate = ()
        if candidate and command[: len(candidate)] == candidate:
            return candidate
    words: list[str] = []
    for token in command[1:3]:
        if not _is_word(token):
            break
        words.append(token)
    minimum = 2 if words else 1
    for length in range(minimum, len(words) + 2):
        candidate = command[:length]
        if not is_banned_prefix(candidate):
            return candidate
    return None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


@dataclass(frozen=True, slots=True)
class PrefixRule:
    prefix: tuple[str, ...]
    sandbox: RuleSandbox = RuleSandbox.POLICY
    exact: bool = False
    created_at: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "prefix", validate_prefix(self.prefix, exact=self.exact))
        object.__setattr__(self, "sandbox", RuleSandbox(self.sandbox))
        object.__setattr__(self, "exact", bool(self.exact))

    @property
    def key(self) -> tuple[tuple[str, ...], str, bool]:
        return (self.prefix, self.sandbox.value, self.exact)

    def matches(self, command: Sequence[str]) -> bool:
        tokens = tuple(command)
        if self.exact:
            return tokens == self.prefix
        if is_opaque(tokens):
            return False
        return len(tokens) >= len(self.prefix) and tokens[: len(self.prefix)] == self.prefix

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "prefix": list(self.prefix),
            "sandbox": self.sandbox.value,
            "createdAt": self.created_at,
        }
        if self.exact:
            payload["exact"] = True
        return payload

    @classmethod
    def from_dict(cls, payload: object) -> "PrefixRule":
        if not isinstance(payload, dict):
            raise ValueError("rule entry must be an object")
        prefix = payload.get("prefix")
        if not isinstance(prefix, list) or any(not isinstance(item, str) for item in prefix):
            raise ValueError("rule prefix must be an array of strings")
        return cls(
            prefix=tuple(prefix),
            sandbox=RuleSandbox(str(payload.get("sandbox") or RuleSandbox.POLICY.value)),
            exact=bool(payload.get("exact")),
            created_at=str(payload.get("createdAt") or ""),
        )


def best_rule(
    rules: Iterable[PrefixRule],
    command: Sequence[str],
    *,
    escalated_request: bool,
    allow_unsandboxed: bool,
) -> PrefixRule | None:
    """The rule that decides ``command``: unsandboxed beats policy, longer beats shorter.

    A call that asks to leave the sandbox is only decided by an unsandboxed rule.
    An ordinary call is decided by either kind, but an unsandboxed rule counts
    only where ``allow_unsandboxed`` says an escalation is permitted at all.
    """

    chosen: PrefixRule | None = None
    for rule in rules:
        if not rule.matches(command):
            continue
        if rule.sandbox is RuleSandbox.UNSANDBOXED and not allow_unsandboxed:
            continue
        if escalated_request and rule.sandbox is not RuleSandbox.UNSANDBOXED:
            continue
        if chosen is None or _rank(rule) > _rank(chosen):
            chosen = rule
    return chosen


def _rank(rule: PrefixRule) -> tuple[int, int, int]:
    return (1 if rule.sandbox is RuleSandbox.UNSANDBOXED else 0, 1 if rule.exact else 0, len(rule.prefix))


def project_key(workspace: str | Path) -> str:
    root = Path(workspace).expanduser()
    try:
        root = root.resolve()
    except OSError:  # pragma: no cover - a path that cannot be resolved stays as typed
        root = root.absolute()
    return os.path.normcase(str(root))


class ProjectApprovalRules:
    """Prefix rules saved per project root, shared by every thread of that project."""

    def __init__(self, home: str | Path) -> None:
        self.home = Path(home).expanduser().resolve()
        self.path = self.home / RULES_FILE
        self._lock = threading.RLock()
        self._cached: tuple[tuple[int, int, int], dict[str, dict[str, object]]] | None = None

    # ---- persistence -----------------------------------------------------

    def _signature(self) -> tuple[int, int, int] | None:
        try:
            stat = self.path.stat()
        except OSError:
            return None
        return (stat.st_mtime_ns, stat.st_size, stat.st_ino)

    def _read(self) -> dict[str, dict[str, object]]:
        signature = self._signature()
        if signature is None:
            return {}
        cached = self._cached
        if cached is not None and cached[0] == signature:
            return cached[1]
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ApprovalRuleError(f"could not read saved approval rules: {exc}") from exc
        if not isinstance(payload, dict) or int(payload.get("version") or 0) != _FILE_VERSION:
            raise ApprovalRuleError("unsupported approval rules file")
        projects = payload.get("projects")
        if not isinstance(projects, dict):
            raise ApprovalRuleError("approval rules file has no projects object")
        self._cached = (signature, projects)
        return projects

    def _write(self, projects: dict[str, dict[str, object]]) -> None:
        payload = {"version": _FILE_VERSION, "projects": projects}
        text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        temporary = self.path.with_name(f".{self.path.name}.{uuid.uuid4().hex}.tmp")
        try:
            self.home.mkdir(parents=True, exist_ok=True)
            temporary.write_text(text, encoding="utf-8")
            os.replace(temporary, self.path)
            self._cached = None
        except OSError as exc:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
            raise ApprovalRuleError(f"could not save approval rules: {exc}") from exc

    @staticmethod
    def _parse(entry: object) -> list[PrefixRule]:
        raw = entry.get("rules") if isinstance(entry, dict) else None
        rules: list[PrefixRule] = []
        for item in raw if isinstance(raw, list) else []:
            try:
                rules.append(PrefixRule.from_dict(item))
            except ValueError:
                # One bad entry (hand-edited, or a prefix banned since it was
                # saved) must not take every other rule down with it.
                continue
        return rules

    # ---- API -------------------------------------------------------------

    def rules(self, workspace: str | Path) -> tuple[PrefixRule, ...]:
        """Rules for ``workspace``'s project. Unreadable storage means no rules."""

        with self._lock:
            try:
                entry = self._read().get(project_key(workspace))
            except ApprovalRuleError:
                return ()
            return tuple(self._parse(entry))

    def add(self, workspace: str | Path, rule: PrefixRule) -> bool:
        """Save ``rule``; returns False when an identical rule already exists."""

        with self._lock:
            projects = dict(self._read())
            key = project_key(workspace)
            entry = dict(projects.get(key) or {})
            existing = self._parse(entry)
            if any(item.key == rule.key for item in existing):
                return False
            if len(existing) >= MAX_PROJECT_RULES:
                raise ApprovalRuleError(f"a project can keep at most {MAX_PROJECT_RULES} approval rules")
            stamped = PrefixRule(rule.prefix, rule.sandbox, rule.exact, rule.created_at or _now())
            entry["root"] = str(Path(workspace).expanduser())
            entry["rules"] = [item.to_dict() for item in (*existing, stamped)]
            projects[key] = entry
            self._write(projects)
            return True

    def remove(self, workspace: str | Path, prefix: Sequence[str], sandbox: str, *, exact: bool = False) -> bool:
        with self._lock:
            projects = dict(self._read())
            key = project_key(workspace)
            entry = dict(projects.get(key) or {})
            # exact=True only skips the "too broad" check, so a rule that became
            # banned after it was saved can still be removed.
            wanted = (validate_prefix(prefix, exact=True), RuleSandbox(sandbox).value, bool(exact))
            existing = self._parse(entry)
            kept = [item for item in existing if item.key != wanted]
            if len(kept) == len(existing):
                return False
            if kept:
                entry["rules"] = [item.to_dict() for item in kept]
                projects[key] = entry
            else:
                projects.pop(key, None)
            self._write(projects)
            return True

    def snapshot(self, workspace: str | Path | None = None) -> list[dict[str, object]]:
        """Rules for one project, or for every project, as plain dictionaries."""

        with self._lock:
            projects = self._read()
            if workspace is not None:
                key = project_key(workspace)
                projects = {key: projects[key]} if key in projects else {}
            return [
                {
                    "root": str((entry or {}).get("root") or key),
                    "rules": [rule.to_dict() for rule in self._parse(entry)],
                }
                for key, entry in sorted(projects.items())
                if self._parse(entry)
            ]


__all__ = [
    "ApprovalRuleError",
    "BANNED_PREFIXES",
    "MAX_PROJECT_RULES",
    "PrefixRule",
    "ProjectApprovalRules",
    "RULES_FILE",
    "RuleSandbox",
    "best_rule",
    "derive_prefix_rule",
    "executable_key",
    "is_banned_prefix",
    "is_opaque",
    "normalized_command",
    "project_key",
    "validate_prefix",
]
