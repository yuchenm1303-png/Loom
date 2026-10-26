"""Host preparation that Microsoft MXC's AppContainer + DACL tier depends on.

On Windows builds without the BaseContainer API, MXC runs commands in an
AppContainer and grants it filesystem access by editing DACLs. Three things on
an ordinary machine stay out of reach without an administrator:

* the NUL device, whose security descriptor the kernel resets at every boot to
  one that names no AppContainer SID (``git`` dies opening ``/dev/null``);
* the system-drive root's metadata (Node's ``realpath`` fails on ``lstat C:\\``);
* tool directories with a protected DACL, such as ``C:\\Program Files\\nodejs``,
  which MXC cannot edit and so refuses to declare.

``scripts/windows-sandbox-prep.ps1`` fixes all three once, elevated. This module
only *reads* security descriptors, so Loom can say exactly what is missing
without administrator rights, and can avoid declaring directories the sandbox
already reads (MXC would otherwise try, and fail, to rewrite their DACL).

The tool-directory grant goes to Loom's own capability SID rather than ALL
APPLICATION PACKAGES, so it opens those directories to Loom's sandbox only.
"""

from __future__ import annotations

import os
import platform
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path


# MXC adds this capability to every Loom sandbox token (processContainer
# .capabilities); the prep script grants its SID read access to tool folders.
LOOM_SANDBOX_TOOLS_CAPABILITY = "loomSandboxTools"
# DeriveCapabilitySidsFromName("loomSandboxTools"). The derivation is a fixed
# hash of the name; a Windows-only test re-derives it so the two cannot drift.
LOOM_SANDBOX_TOOLS_CAPABILITY_SID = (
    "S-1-15-3-1024-3969206430-1258273362-3479422002-2855359522-1761291178-1853497174-2915481160-3664497781"
)
ALL_APPLICATION_PACKAGES_SID = "S-1-15-2-1"
PREP_SCRIPT_NAME = "windows-sandbox-prep.ps1"

_SDDL_ALIASES = {
    "AC": ALL_APPLICATION_PACKAGES_SID,
    "WD": "S-1-1-0",
    "SY": "S-1-5-18",
    "BA": "S-1-5-32-544",
    "BU": "S-1-5-32-545",
    "AU": "S-1-5-11",
    "RC": "S-1-5-12",
    "OW": "S-1-3-4",
}

_FILE_READ_DATA = 0x1
_FILE_WRITE_DATA = 0x2
_FILE_READ_ATTRIBUTES = 0x80
_FILE_GENERIC_READ = 0x120089
_FILE_GENERIC_WRITE = 0x120116
_FILE_GENERIC_EXECUTE = 0x1200A0
_FILE_ALL_ACCESS = 0x1F01FF
_READ_AND_EXECUTE = _FILE_GENERIC_READ | _FILE_GENERIC_EXECUTE

_RIGHT_CODES = {
    "GA": _FILE_ALL_ACCESS,
    "GR": _FILE_GENERIC_READ,
    "GW": _FILE_GENERIC_WRITE,
    "GX": _FILE_GENERIC_EXECUTE,
    "FA": _FILE_ALL_ACCESS,
    "FR": _FILE_GENERIC_READ,
    "FW": _FILE_GENERIC_WRITE,
    "FX": _FILE_GENERIC_EXECUTE,
    "RC": 0x20000,
    "SD": 0x10000,
    "WD": 0x40000,
    "WO": 0x80000,
    # Directory-service spellings Windows also uses for the low file bits.
    "CC": 0x1,
    "DC": 0x2,
    "LC": 0x4,
    "SW": 0x8,
    "RP": 0x10,
    "WP": 0x20,
    "DT": 0x40,
    "LO": 0x80,
    "CR": 0x100,
}
_ACE_RE = re.compile(r"\(([^()]*)\)")


@dataclass(frozen=True, slots=True)
class SddlAce:
    kind: str
    flags: frozenset[str]
    mask: int
    sid: str

    @property
    def applies_to_object(self) -> bool:
        return "IO" not in self.flags


def _parse_rights(text: str) -> int:
    value = text.strip()
    if not value:
        return 0
    if value.lower().startswith("0x"):
        try:
            return int(value, 16)
        except ValueError:
            return 0
    mask = 0
    for index in range(0, len(value) - 1, 2):
        mask |= _RIGHT_CODES.get(value[index:index + 2].upper(), 0)
    return mask


def _parse_flags(text: str) -> frozenset[str]:
    value = text.strip().upper()
    return frozenset(value[index:index + 2] for index in range(0, len(value) - 1, 2))


def parse_dacl_aces(sddl: str) -> tuple[SddlAce, ...]:
    """Parse the ``D:`` part of an SDDL string into allow/deny ACEs."""

    text = str(sddl or "")
    start = text.find("D:")
    if start < 0:
        return ()
    end = text.find("S:", start)
    dacl = text[start:end if end >= 0 else len(text)]
    aces: list[SddlAce] = []
    for match in _ACE_RE.finditer(dacl):
        parts = match.group(1).split(";")
        if len(parts) < 6:
            continue
        kind = parts[0].strip().upper()
        if kind not in {"A", "D"}:
            continue
        account = parts[5].strip()
        sid = _SDDL_ALIASES.get(account.upper(), account.upper() if account.upper().startswith("S-") else account)
        aces.append(SddlAce(kind=kind, flags=_parse_flags(parts[1]), mask=_parse_rights(parts[2]), sid=sid))
    return tuple(aces)


def dacl_grants(sddl: str, sids: tuple[str, ...] | frozenset[str], required: int) -> bool:
    """Whether an allow ACE for one of ``sids`` grants ``required`` on the object.

    Deliberately conservative: any deny ACE for those SIDs that overlaps the
    required rights makes the answer False, and inherit-only ACEs (which only
    reach children) do not count for the object itself.
    """

    wanted = {str(sid).upper() for sid in sids}
    allowed = 0
    for ace in parse_dacl_aces(sddl):
        if ace.sid.upper() not in wanted or not ace.applies_to_object:
            continue
        if ace.kind == "D" and ace.mask & required:
            return False
        if ace.kind == "A":
            allowed |= ace.mask
    return allowed & required == required


_SANDBOX_READER_SIDS = (ALL_APPLICATION_PACKAGES_SID, LOOM_SANDBOX_TOOLS_CAPABILITY_SID)


def sddl_lets_sandbox_read(sddl: str) -> bool:
    return dacl_grants(sddl, _SANDBOX_READER_SIDS, _READ_AND_EXECUTE)


# Loom's sandbox is an ordinary (not less-privileged) AppContainer, so the
# ALL APPLICATION PACKAGES ACE is the one its access checks consult.
def sddl_lets_appcontainer_use_null_device(sddl: str) -> bool:
    return dacl_grants(sddl, (ALL_APPLICATION_PACKAGES_SID,), _FILE_READ_DATA | _FILE_WRITE_DATA)


def sddl_lets_appcontainer_stat_root(sddl: str) -> bool:
    return dacl_grants(sddl, (ALL_APPLICATION_PACKAGES_SID,), _FILE_READ_ATTRIBUTES)


def _is_windows() -> bool:
    return platform.system().casefold() == "windows"


def _read_sddl(target: str, *, device: bool = False) -> str | None:
    """Read a DACL as SDDL without asking for more than READ_CONTROL."""

    if not _is_windows():
        return None
    import ctypes
    from ctypes import wintypes

    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    dacl_information = 0x4
    descriptor = ctypes.c_void_p()
    try:
        if device:
            kernel32.CreateFileW.restype = wintypes.HANDLE
            kernel32.CreateFileW.argtypes = [
                wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
            ]
            handle = kernel32.CreateFileW(target, 0x00020000, 0x3, None, 3, 0x80, None)
            if not handle or handle == wintypes.HANDLE(-1).value:
                return None
            try:
                advapi32.GetSecurityInfo.argtypes = [
                    wintypes.HANDLE, ctypes.c_int, wintypes.DWORD, ctypes.c_void_p,
                    ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p),
                ]
                status = advapi32.GetSecurityInfo(
                    handle, 6, dacl_information, None, None, None, None, ctypes.byref(descriptor)
                )
            finally:
                kernel32.CloseHandle(handle)
        else:
            advapi32.GetNamedSecurityInfoW.argtypes = [
                wintypes.LPCWSTR, ctypes.c_int, wintypes.DWORD, ctypes.c_void_p,
                ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p),
            ]
            status = advapi32.GetNamedSecurityInfoW(
                target, 1, dacl_information, None, None, None, None, ctypes.byref(descriptor)
            )
        if status != 0 or not descriptor.value:
            return None
        text = wintypes.LPWSTR()
        advapi32.ConvertSecurityDescriptorToStringSecurityDescriptorW.argtypes = [
            ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD,
            ctypes.POINTER(wintypes.LPWSTR), ctypes.c_void_p,
        ]
        if not advapi32.ConvertSecurityDescriptorToStringSecurityDescriptorW(
            descriptor, 1, dacl_information, ctypes.byref(text), None
        ):
            return None
        try:
            return str(text.value or "")
        finally:
            kernel32.LocalFree(ctypes.cast(text, ctypes.c_void_p))
    except (OSError, AttributeError, ValueError):
        return None
    finally:
        if descriptor.value:
            kernel32.LocalFree(descriptor)


_CACHE_SECONDS = 30.0
_cache_guard = threading.Lock()
_read_cache: dict[str, tuple[float, bool]] = {}


def sandbox_can_read(path: str | Path) -> bool:
    """True when Loom's AppContainer reads ``path`` without any DACL edit.

    Unknown (unreadable descriptor, non-Windows) is False: callers then keep
    declaring the path, which is MXC's ordinary behavior.
    """

    key = os.path.normcase(str(path))
    now = time.monotonic()
    with _cache_guard:
        hit = _read_cache.get(key)
        if hit is not None and now - hit[0] < _CACHE_SECONDS:
            return hit[1]
    sddl = _read_sddl(str(path))
    readable = bool(sddl) and sddl_lets_sandbox_read(sddl)
    with _cache_guard:
        _read_cache[key] = (now, readable)
    return readable


@dataclass(frozen=True, slots=True)
class WindowsSandboxPrepStatus:
    null_device_ready: bool | None
    system_drive_ready: bool | None
    script: str = ""
    missing_tool_dirs: tuple[str, ...] = field(default_factory=tuple)

    @property
    def complete(self) -> bool:
        return bool(self.null_device_ready) and bool(self.system_drive_ready) and not self.missing_tool_dirs

    def missing(self) -> tuple[str, ...]:
        names: list[str] = []
        if not self.null_device_ready:
            names.append("NUL device")
        if not self.system_drive_ready:
            names.append("system drive root")
        if self.missing_tool_dirs:
            names.append("tool folders: " + ", ".join(self.missing_tool_dirs))
        return tuple(names)

    def command(self) -> str:
        if not self.script:
            return ""
        return f'powershell -NoProfile -ExecutionPolicy Bypass -File "{self.script}"'

    def to_dict(self) -> dict[str, object]:
        return {
            "nullDeviceReady": self.null_device_ready,
            "systemDriveReady": self.system_drive_ready,
            "missingToolDirs": list(self.missing_tool_dirs),
            "complete": self.complete,
            "command": self.command(),
        }


def locate_prep_script(sandbox_executable: str | Path | None = None) -> str:
    """The prep script: beside a packaged wxc-exec.exe, else in this checkout."""

    candidates: list[Path] = []
    if sandbox_executable:
        candidates.append(Path(sandbox_executable).expanduser().resolve().parent / PREP_SCRIPT_NAME)
    for parent in Path(__file__).resolve().parents:
        candidates.append(parent / "scripts" / PREP_SCRIPT_NAME)
        if (parent / ".git").exists():
            break
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    return ""


_status_guard = threading.Lock()
_status_cache: dict[tuple[str, tuple[str, ...]], tuple[float, WindowsSandboxPrepStatus]] = {}


def windows_sandbox_prep_status(
    *,
    sandbox_executable: str | Path | None = None,
    tool_dirs: tuple[str, ...] = (),
    force: bool = False,
) -> WindowsSandboxPrepStatus:
    """Read-only check of the host preparation; cached briefly.

    ``tool_dirs`` are folders Loom just failed to declare (protected DACLs);
    they are reported missing until the capability ACE shows up on them.
    """

    key = (str(sandbox_executable or ""), tuple(os.path.normcase(item) for item in tool_dirs))
    now = time.monotonic()
    if not force:
        with _status_guard:
            hit = _status_cache.get(key)
            if hit is not None and now - hit[0] < _CACHE_SECONDS:
                return hit[1]
    script = locate_prep_script(sandbox_executable)
    if not _is_windows():
        status = WindowsSandboxPrepStatus(None, None, script=script)
    else:
        null_sddl = _read_sddl(r"\\.\NUL", device=True)
        drive = (os.environ.get("SystemDrive") or "C:").rstrip("\\/") + "\\"
        root_sddl = _read_sddl(drive)
        missing_dirs = tuple(path for path in tool_dirs if not sandbox_can_read(path))
        status = WindowsSandboxPrepStatus(
            null_device_ready=None if null_sddl is None else sddl_lets_appcontainer_use_null_device(null_sddl),
            system_drive_ready=None if root_sddl is None else sddl_lets_appcontainer_stat_root(root_sddl),
            script=script,
            missing_tool_dirs=missing_dirs,
        )
    with _status_guard:
        _status_cache[key] = (now, status)
    return status


__all__ = [
    "ALL_APPLICATION_PACKAGES_SID",
    "LOOM_SANDBOX_TOOLS_CAPABILITY",
    "LOOM_SANDBOX_TOOLS_CAPABILITY_SID",
    "PREP_SCRIPT_NAME",
    "SddlAce",
    "WindowsSandboxPrepStatus",
    "dacl_grants",
    "locate_prep_script",
    "parse_dacl_aces",
    "sandbox_can_read",
    "sddl_lets_appcontainer_stat_root",
    "sddl_lets_appcontainer_use_null_device",
    "sddl_lets_sandbox_read",
    "windows_sandbox_prep_status",
]
