"""System-browser selection for a native Windows x86 Loom Host.

There is no Chromium win-x86 bundle in the x64 desktop packaging pipeline.
On the 32-bit Host, prefer the user's installed Edge / Chrome (which is
already compatible with their OS) instead of trying to launch an unavailable
x64 bundled Chromium. Browser permissions and URL policies are unchanged.
"""
from __future__ import annotations

import os
import platform
import struct
from pathlib import Path
from typing import Callable, Mapping


def installed_browser_channel(
    *,
    system_name: str | None = None,
    bits: int | None = None,
    environ: Mapping[str, str] | None = None,
    file_exists: Callable[[Path], bool] | None = None,
) -> str:
    system = platform.system() if system_name is None else system_name
    pointer_bits = struct.calcsize("P") * 8 if bits is None else bits
    if system != "Windows" or pointer_bits != 32:
        return ""

    env = os.environ if environ is None else environ
    exists = Path.is_file if file_exists is None else file_exists
    locations = [
        env.get("ProgramFiles", ""),
        env.get("PROGRAMFILES", ""),
        env.get("ProgramFiles(x86)", ""),
        env.get("PROGRAMFILES(X86)", ""),
        env.get("LOCALAPPDATA", ""),
    ]
    bases = [Path(folder) for folder in locations if folder.strip()]
    # System Edge is preferred because it is normally already installed and
    # updated with Windows; no non-system executable or remote CDP port is used.
    candidates = [
        ("msedge", Path("Microsoft/Edge/Application/msedge.exe")),
        ("chrome", Path("Google/Chrome/Application/chrome.exe")),
    ]
    for channel, suffix in candidates:
        for base in bases:
            if exists(base / suffix):
                return channel
    return ""


__all__ = ["installed_browser_channel"]
