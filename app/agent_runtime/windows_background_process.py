"""Windows child-process visibility hardening for Loom runtimes.

Loom Desktop is a GUI process. Console children such as cmd.exe, powershell.exe,
taskkill.exe and the Windows MXC probe must therefore run without allocating a
visible console window. This module installs a module-local subprocess proxy so
only Agent Runtime process/sandbox launches are affected.
"""

from __future__ import annotations

import os
import subprocess as _subprocess
from typing import Any


class _BackgroundSubprocessProxy:
    """Delegate to subprocess while forcing CREATE_NO_WINDOW on Windows."""

    def __init__(self, module: Any, *, system_name: str | None = None) -> None:
        self._module = module
        self._system_name = os.name if system_name is None else str(system_name)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._module, name)

    def _kwargs(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        if self._system_name != "nt":
            return kwargs
        values = dict(kwargs)
        flags = int(values.get("creationflags", 0) or 0)
        flags |= int(getattr(self._module, "CREATE_NO_WINDOW", 0) or 0)
        values["creationflags"] = flags
        return values

    def Popen(self, *args: Any, **kwargs: Any):  # noqa: N802 - mirrors subprocess API
        return self._module.Popen(*args, **self._kwargs(kwargs))

    def run(self, *args: Any, **kwargs: Any):
        return self._module.run(*args, **self._kwargs(kwargs))


def install() -> bool:
    """Hide Agent Runtime console children on Windows; idempotent and local."""

    if os.name != "nt":
        return False

    from . import process_runtime, sandbox

    for module in (process_runtime, sandbox):
        current = module.subprocess
        if isinstance(current, _BackgroundSubprocessProxy):
            continue
        module.subprocess = _BackgroundSubprocessProxy(current, system_name="nt")
    return True


__all__ = ["install"]
