"""A sitecustomize for Python started inside the Windows MXC sandbox.

Since CPython 3.12.4 (CVE-2024-4030), os.mkdir(path, 0o700) on Windows gives the new
directory a protected DACL for the owner only. An AppContainer process also needs its
container or capability SID in that DACL, so it cannot write into the directory it just
made: every tempfile.mkdtemp / TemporaryDirectory in a sandboxed command failed
(measured on the real backend: plain makedirs ok, 0o700 and mkdtemp PermissionError,
in the workspace and in the container's own Temp). The hook maps 0o700 to the default
mode, so the directory inherits its parent's DACL - already limited to the user and the
sandbox - and then runs the project's own sitecustomize, if it has one.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path

HOOK_SOURCE = '''"""Loom sandbox: keep 0o700 directories usable by this AppContainer process."""
import os as _os
import sys as _sys

if _os.name == "nt" and not getattr(_os.mkdir, "_loom_sandbox", False):
    _mkdir = _os.mkdir

    def _loom_mkdir(path, mode=0o777, *, dir_fd=None):
        return _mkdir(path, 0o777 if mode == 0o700 else mode, dir_fd=dir_fd)

    _loom_mkdir._loom_sandbox = True
    _os.mkdir = _loom_mkdir


def _chain():
    here = _os.path.dirname(_os.path.abspath(__file__))
    for entry in list(_sys.path):
        folder = _os.path.abspath(entry or _os.curdir)
        candidate = _os.path.join(folder, "sitecustomize.py")
        if folder != here and _os.path.isfile(candidate):
            with open(candidate, "rb") as handle:
                code = compile(handle.read(), candidate, "exec")
            exec(code, {"__name__": "sitecustomize", "__file__": candidate})
            return


_chain()
'''


def hook_directory(base: Path | None = None) -> Path:
    """A content-addressed folder holding the hook; written once, never edited."""
    root = base or Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / "Loom" / "sandbox-python"
    folder = root / hashlib.sha256(HOOK_SOURCE.encode("utf-8")).hexdigest()[:16]
    target = folder / "sitecustomize.py"
    if not target.is_file() or target.read_text(encoding="utf-8") != HOOK_SOURCE:
        folder.mkdir(parents=True, exist_ok=True)
        temporary = folder / f".sitecustomize.{os.getpid()}.tmp"
        temporary.write_text(HOOK_SOURCE, encoding="utf-8")
        os.replace(temporary, target)
    return folder


def python_hook_enabled() -> bool:
    value = str(os.environ.get("LOOM_WINDOWS_SANDBOX_PYTHON_HOOK") or "").strip().casefold()
    return value not in {"off", "disabled", "0", "false", "no"}
