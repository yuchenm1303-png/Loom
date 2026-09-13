from __future__ import annotations

import os
import re
import shlex
from typing import Sequence


_CANONICAL_SHELL_SCRIPT_PREFIX = "__codex_shell_script__"
_CANONICAL_POWERSHELL_SCRIPT_PREFIX = "__codex_powershell_script__"
_BOURNE_SHELLS = frozenset({"bash", "zsh", "sh"})
_POWERSHELLS = frozenset({"powershell", "powershell.exe", "pwsh", "pwsh.exe"})
# Codex delegates this to codex-shell-command. Loom keeps a conservative Python
# subset: only scripts with no shell syntax are tokenized. Anything ambiguous is
# retained verbatim behind the same stable script sentinel used upstream.
_COMPLEX_SHELL_RE = re.compile(r"[\n\r;&|<>`$(){}*?\[\]]")


def _basename(program: str) -> str:
    # ntpath.basename also understands backslashes when Loom is running on POSIX
    # but approving a Windows-shaped command from an external executor.
    normalized = str(program or "").replace("\\", "/")
    return normalized.rsplit("/", 1)[-1].casefold()


def _plain_shell_command(script: str) -> tuple[str, ...] | None:
    if not script.strip() or _COMPLEX_SHELL_RE.search(script):
        return None
    try:
        values = tuple(shlex.split(script, posix=True))
    except ValueError:
        return None
    return values or None


def canonicalize_command_for_approval(command: Sequence[str]) -> tuple[str, ...]:
    """Canonicalize argv for Codex-style reusable approval matching.

    This ports the observable cases from Codex's
    ``command_canonicalization.rs``: simple Bourne-shell wrappers collapse to
    their inner argv, complex shell scripts retain exact script text behind a
    stable sentinel, PowerShell wrappers ignore executable/flag spelling, and
    ordinary direct argv is unchanged.
    """

    argv = tuple(str(value) for value in command)
    if not argv:
        return argv

    program = _basename(argv[0])
    if program in _BOURNE_SHELLS and len(argv) >= 3 and argv[1] in {"-c", "-lc"}:
        script = argv[2]
        plain = _plain_shell_command(script)
        if plain is not None:
            return plain
        return (_CANONICAL_SHELL_SCRIPT_PREFIX, argv[1], script)

    if program in _POWERSHELLS:
        for index, value in enumerate(argv[1:], start=1):
            if value.casefold() in {"-command", "-c"} and index + 1 < len(argv):
                return (_CANONICAL_POWERSHELL_SCRIPT_PREFIX, argv[index + 1])

    return argv


__all__ = ["canonicalize_command_for_approval"]
