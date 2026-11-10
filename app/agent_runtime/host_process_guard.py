"""Refuse commands that would kill the Loom Host itself.

The Host is a python process. In a long goal on a CPU-bound task the model saw
two idle-looking ``python.exe`` processes (the Host) and ran
``taskkill /F /IM python.exe`` -- twice -- which ended the goal. A kill by
image name cannot tell the model's own children from the Host, so it is refused
for the Host's own image names, and a kill by PID is refused for the Host's own
PID and its ancestors. Killing a process this conversation started goes through
``exec_terminate`` (by process id), which only ever touches that process tree.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Sequence

_KILL_VERBS = re.compile(
    r"\btaskkill\b|\bstop-process\b|\bpkill\b|\bkillall\b|\bkill\s+-\w*9|"
    r"\bwmic\b[^|;&]*\b(delete|terminate)\b|\bterminate\b[^|;&]*\bwin32_process\b|"
    r"\bwin32_process\b[^|;&]*\bterminate\b|\bpskill\b|\bcim?instance\b[^|;&]*\bterminate\b",
    re.IGNORECASE,
)
# Image names the Host (or the desktop app around it) runs as.
_HOST_IMAGE = re.compile(
    r"(?<![\w.-])(pythonw?[\d.]*|python3[\d.]*|py|loom|loom-host|loom_app_server|electron)(\.exe)?(?![\w-])",
    re.IGNORECASE,
)
_PID_ARG = re.compile(r"(?:/pid|-id|--pid)\s+\"?(\d+)", re.IGNORECASE)
_KILL_PID_POSIX = re.compile(r"\bkill\b[^|;&]*?\s(\d{2,})\b", re.IGNORECASE)
_SCRIPT_SUFFIXES = {".bat", ".cmd", ".ps1", ".py"}
_MAX_SCRIPT_BYTES = 128_000


def _protected_pids() -> set[int]:
    pids = {os.getpid()}
    try:
        import psutil

        for parent in psutil.Process().parents():
            pids.add(parent.pid)
    except Exception:  # noqa: BLE001 - psutil is optional; own pid is still covered
        pids.add(os.getppid())
    return pids


def _scripts(argv: Sequence[str], cwd: Path | None) -> list[str]:
    texts: list[str] = []
    for item in argv:
        suffix = Path(item.strip('"')).suffix.lower()
        if suffix not in _SCRIPT_SUFFIXES:
            continue
        path = Path(item.strip('"'))
        if not path.is_absolute() and cwd is not None:
            path = cwd / path
        try:
            if path.is_file() and path.stat().st_size <= _MAX_SCRIPT_BYTES:
                texts.append(path.read_text(encoding="utf-8", errors="ignore"))
        except OSError:
            continue
    return texts


def host_kill_reason(argv: Sequence[str], cwd: Path | None = None) -> str | None:
    """A message when ``argv`` (or a script it runs) would kill the Host, else None."""

    pieces = [" ".join(argv), *_scripts(argv, cwd)]
    for text in pieces:
        for line in re.split(r"[\r\n]+", text):
            if not _KILL_VERBS.search(line):
                continue
            pids = {int(p) for p in _PID_ARG.findall(line)} | {
                int(p) for p in _KILL_PID_POSIX.findall(line)}
            if pids & _protected_pids():
                return _message("its PID")
            by_name = _HOST_IMAGE.search(line)
            # `taskkill /PID 123` that merely mentions python elsewhere is a PID kill.
            if by_name and not pids:
                return _message(f"the image name {by_name.group(1)}")
            # A wildcard image kill takes the Host with it.
            if re.search(r"/im\s+\"?\*", line, re.IGNORECASE):
                return _message("a wildcard image name")
    return None


def _message(what: str) -> str:
    return (
        f"Not executed: this command ends processes by {what}, which would also end the Loom Host that "
        "runs this conversation (the Host is a python process, so a python.exe in the process list may be "
        "it, not a stray job of yours). Stop what you started with exec_terminate on its process id "
        "(list them with exec_wait / the process ids from your own exec calls), or kill by the PID of a "
        "process you started. To stop a script from fighting for CPU, limit its threads "
        "(OMP_NUM_THREADS / torch.set_num_threads) instead of killing everything."
    )
