"""Native Windows ConPTY backend without pywinpty.

Windows 10 1809+ exposes CreatePseudoConsole in kernel32.dll on both x86
and x64. Loom's 32-bit Python wheel cannot use the 64-bit pywinpty DLLs,
so call the documented Windows APIs directly rather than losing full PTY
semantics. This module is imported only by the Windows ia32 Host.

Process permissions, command validation, sandbox planning and kill-tree
rules remain in process_runtime. No shell is added to the command line.
"""
from __future__ import annotations

import codecs
import ctypes
import os
import signal
import subprocess
import threading
from ctypes import wintypes
from pathlib import Path
from typing import Mapping


class ConPtyUnavailableError(RuntimeError):
    """The Windows OS does not expose the required native ConPTY APIs."""


class _Coord(ctypes.Structure):
    _fields_ = [("X", ctypes.c_short), ("Y", ctypes.c_short)]


class _StartupInfo(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("lpReserved", wintypes.LPWSTR),
        ("lpDesktop", wintypes.LPWSTR),
        ("lpTitle", wintypes.LPWSTR),
        ("dwX", wintypes.DWORD),
        ("dwY", wintypes.DWORD),
        ("dwXSize", wintypes.DWORD),
        ("dwYSize", wintypes.DWORD),
        ("dwXCountChars", wintypes.DWORD),
        ("dwYCountChars", wintypes.DWORD),
        ("dwFillAttribute", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("wShowWindow", wintypes.WORD),
        ("cbReserved2", wintypes.WORD),
        ("lpReserved2", ctypes.POINTER(ctypes.c_byte)),
        ("hStdInput", wintypes.HANDLE),
        ("hStdOutput", wintypes.HANDLE),
        ("hStdError", wintypes.HANDLE),
    ]


class _StartupInfoEx(ctypes.Structure):
    _fields_ = [
        ("StartupInfo", _StartupInfo),
        ("lpAttributeList", ctypes.c_void_p),
    ]


class _ProcessInformation(ctypes.Structure):
    _fields_ = [
        ("hProcess", wintypes.HANDLE),
        ("hThread", wintypes.HANDLE),
        ("dwProcessId", wintypes.DWORD),
        ("dwThreadId", wintypes.DWORD),
    ]


def _win_api():
    if os.name != "nt":
        raise ConPtyUnavailableError("Windows ConPTY requires Windows")
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    required = (
        "CreatePseudoConsole", "ResizePseudoConsole", "ClosePseudoConsole",
        "InitializeProcThreadAttributeList", "UpdateProcThreadAttribute",
        "DeleteProcThreadAttributeList", "CreateProcessW", "CreatePipe",
    )
    if any(not hasattr(k, name) for name in required):
        raise ConPtyUnavailableError(
            "This version of Windows does not support native ConPTY (Windows 10 1809+ required)"
        )
    k.CreatePipe.argtypes = [
        ctypes.POINTER(wintypes.HANDLE), ctypes.POINTER(wintypes.HANDLE),
        ctypes.c_void_p, wintypes.DWORD,
    ]
    k.CreatePipe.restype = wintypes.BOOL
    k.CreatePseudoConsole.argtypes = [
        _Coord, wintypes.HANDLE, wintypes.HANDLE, wintypes.DWORD,
        ctypes.POINTER(wintypes.HANDLE),
    ]
    k.CreatePseudoConsole.restype = ctypes.c_long
    k.ResizePseudoConsole.argtypes = [wintypes.HANDLE, _Coord]
    k.ResizePseudoConsole.restype = ctypes.c_long
    k.ClosePseudoConsole.argtypes = [wintypes.HANDLE]
    k.ClosePseudoConsole.restype = None
    k.InitializeProcThreadAttributeList.argtypes = [
        ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD,
        ctypes.POINTER(ctypes.c_size_t),
    ]
    k.InitializeProcThreadAttributeList.restype = wintypes.BOOL
    k.UpdateProcThreadAttribute.argtypes = [
        ctypes.c_void_p, wintypes.DWORD, ctypes.c_size_t, ctypes.c_void_p,
        ctypes.c_size_t, ctypes.c_void_p, ctypes.c_void_p,
    ]
    k.UpdateProcThreadAttribute.restype = wintypes.BOOL
    k.DeleteProcThreadAttributeList.argtypes = [ctypes.c_void_p]
    k.CloseHandle.argtypes = [wintypes.HANDLE]
    k.CloseHandle.restype = wintypes.BOOL
    k.CreateProcessW.argtypes = [
        wintypes.LPCWSTR, wintypes.LPWSTR, ctypes.c_void_p,
        ctypes.c_void_p, wintypes.BOOL, wintypes.DWORD,
        ctypes.c_void_p, wintypes.LPCWSTR,
        ctypes.POINTER(_StartupInfo), ctypes.POINTER(_ProcessInformation),
    ]
    k.CreateProcessW.restype = wintypes.BOOL
    k.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    k.WaitForSingleObject.restype = wintypes.DWORD
    k.GetExitCodeProcess.argtypes = [
        wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD),
    ]
    k.GetExitCodeProcess.restype = wintypes.BOOL
    k.PeekNamedPipe.argtypes = [
        wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p,
        ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p,
    ]
    k.PeekNamedPipe.restype = wintypes.BOOL
    k.ReadFile.argtypes = [
        wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p,
    ]
    k.ReadFile.restype = wintypes.BOOL
    k.WriteFile.argtypes = [
        wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p,
    ]
    k.WriteFile.restype = wintypes.BOOL
    return k


def _last_error(operation: str) -> OSError:
    code = ctypes.get_last_error()
    return OSError(code, f"{operation}: {ctypes.FormatError(code)}")


class WindowsNativeConPtyProcessBackend:
    """Real interactive Windows PTY for x86 without third-party native DLLs."""

    name = "windows-native-conpty"
    pty = True

    def __init__(
        self, *, argv: tuple[str, ...], cwd: Path,
        env: Mapping[str, str], rows: int, cols: int,
    ) -> None:
        self._kernel = _win_api()
        self._input: wintypes.HANDLE | None = None
        self._output: wintypes.HANDLE | None = None
        self._console: wintypes.HANDLE | None = None
        self._proc: wintypes.HANDLE | None = None
        self._pid = 0
        self._exit: int | None = None
        self._closed = False
        self._lock = threading.RLock()
        self._decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        read_input = wintypes.HANDLE()
        write_input = wintypes.HANDLE()
        read_output = wintypes.HANDLE()
        write_output = wintypes.HANDLE()
        attrs = None
        attrs_initialized = False
        try:
            if not self._kernel.CreatePipe(
                ctypes.byref(read_input), ctypes.byref(write_input), None, 0
            ):
                raise _last_error("CreatePipe stdin")
            if not self._kernel.CreatePipe(
                ctypes.byref(read_output), ctypes.byref(write_output), None, 0
            ):
                raise _last_error("CreatePipe stdout")

            console = wintypes.HANDLE()
            hr = self._kernel.CreatePseudoConsole(
                _Coord(cols, rows), read_input, write_output, 0,
                ctypes.byref(console),
            )
            if hr < 0:
                raise OSError(hr, f"CreatePseudoConsole failed: HRESULT 0x{hr & 0xffffffff:08x}")
            self._console = console
            self._input = write_input
            self._output = read_output
            write_input = wintypes.HANDLE()
            read_output = wintypes.HANDLE()
            # The ConPTY object owns its side of both anonymous pipes now.
            self._kernel.CloseHandle(read_input)
            self._kernel.CloseHandle(write_output)
            read_input = wintypes.HANDLE()
            write_output = wintypes.HANDLE()

            needed = ctypes.c_size_t()
            self._kernel.InitializeProcThreadAttributeList(None, 1, 0, ctypes.byref(needed))
            if not needed.value:
                raise _last_error("InitializeProcThreadAttributeList sizing")
            attrs = ctypes.create_string_buffer(needed.value)
            if not self._kernel.InitializeProcThreadAttributeList(
                ctypes.cast(attrs, ctypes.c_void_p), 1, 0, ctypes.byref(needed)
            ):
                raise _last_error("InitializeProcThreadAttributeList")
            attrs_initialized = True
            # PROC_THREAD_ATTRIBUTE_PSEUDOCONSOLE, documented in winconpty.h
            if not self._kernel.UpdateProcThreadAttribute(
                ctypes.cast(attrs, ctypes.c_void_p), 0, 0x00020016,
                ctypes.byref(console), ctypes.sizeof(console), None, None,
            ):
                raise _last_error("UpdateProcThreadAttribute pseudoconsole")
            startup = _StartupInfoEx()
            startup.StartupInfo.cb = ctypes.sizeof(_StartupInfoEx)
            startup.lpAttributeList = ctypes.cast(attrs, ctypes.c_void_p)
            process_info = _ProcessInformation()
            command = ctypes.create_unicode_buffer(subprocess.list2cmdline(argv))
            environment = "\0".join(
                f"{name}={value}" for name, value in
                sorted(dict(env).items(), key=lambda p: p[0].upper())
            ) + "\0\0"
            environment_buffer = ctypes.create_unicode_buffer(environment)
            # EXTENDED_STARTUPINFO_PRESENT | CREATE_UNICODE_ENVIRONMENT
            flags = 0x00080000 | 0x00000400
            if not self._kernel.CreateProcessW(
                None, command, None, None, False, flags,
                ctypes.cast(environment_buffer, ctypes.c_void_p), str(cwd),
                ctypes.byref(startup.StartupInfo), ctypes.byref(process_info),
            ):
                raise _last_error("CreateProcessW ConPTY child")
            self._proc = process_info.hProcess
            self._pid = int(process_info.dwProcessId)
            self._kernel.CloseHandle(process_info.hThread)
        except Exception:
            self.close()
            raise
        finally:
            if attrs_initialized:
                self._kernel.DeleteProcThreadAttributeList(
                    ctypes.cast(attrs, ctypes.c_void_p)
                )
            # Some setup stages fail before the ConPTY takes ownership.
            for handle in (read_input, write_input, read_output, write_output):
                if handle.value:
                    self._kernel.CloseHandle(handle)

    @property
    def pid(self) -> int:
        return self._pid

    def poll(self) -> int | None:
        with self._lock:
            if self._exit is not None:
                return self._exit
            if not self._proc:
                return None
            if self._kernel.WaitForSingleObject(self._proc, 0) != 0:
                return None
            code = wintypes.DWORD()
            if self._kernel.GetExitCodeProcess(self._proc, ctypes.byref(code)):
                self._exit = ctypes.c_int32(code.value).value
            return self._exit

    def read_stdout(self) -> str:
        if not self._output:
            return ""
        available = wintypes.DWORD()
        if not self._kernel.PeekNamedPipe(
            self._output, None, 0, None, ctypes.byref(available), None
        ):
            return ""
        if not available.value:
            return ""
        size = min(int(available.value), 4096)
        buf = ctypes.create_string_buffer(size)
        read = wintypes.DWORD()
        if not self._kernel.ReadFile(
            self._output, buf, size, ctypes.byref(read), None
        ):
            return ""
        return self._decoder.decode(buf.raw[:read.value])

    def read_stderr(self) -> str:
        # A true ConPTY merges stdout and stderr into its terminal screen.
        return ""

    def write(self, text: str) -> None:
        if self.poll() is not None or not self._input:
            raise RuntimeError("process PTY input is not available")
        data = str(text).encode("utf-8")
        buffer = ctypes.create_string_buffer(data)
        written = wintypes.DWORD()
        if not self._kernel.WriteFile(
            self._input, buffer, len(data), ctypes.byref(written), None
        ):
            raise _last_error("WriteFile ConPTY input")

    def close_stdin(self) -> None:
        # ConPTY sends Ctrl-Z as console EOF; closing the input pipe also
        # disconnects the console and would prevent continued output reads.
        if self.poll() is None:
            try:
                self.write("\x1a\r")
            except (OSError, RuntimeError):
                pass

    def send_control(self, control: str) -> None:
        letter = str(control).lower()
        if len(letter) != 1 or not "a" <= letter <= "z":
            raise ValueError("control must be one ASCII letter")
        self.write(chr(ord(letter) - ord("a") + 1))

    def resize(self, rows: int, cols: int) -> None:
        if not self._console:
            raise RuntimeError("PTY is already closed")
        hr = self._kernel.ResizePseudoConsole(self._console, _Coord(cols, rows))
        if hr < 0:
            raise OSError(hr, "ResizePseudoConsole failed")

    def interrupt(self) -> None:
        if self.poll() is None:
            self.write("\x03")

    def terminate_tree(self, *, grace_seconds: float = 1.0) -> None:
        if self.poll() is not None:
            return
        subprocess.run(
            ["taskkill", "/PID", str(self.pid), "/T", "/F"],
            capture_output=True, text=False, check=False,
        )

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            # Save the exit status before handles are closed.
            if self._proc:
                self.poll()
            for handle in (self._input, self._output):
                if handle:
                    self._kernel.CloseHandle(handle)
            self._input = None
            self._output = None
            if self._console:
                self._kernel.ClosePseudoConsole(self._console)
                self._console = None
            if self._proc:
                self._kernel.CloseHandle(self._proc)
                self._proc = None


__all__ = ["ConPtyUnavailableError", "WindowsNativeConPtyProcessBackend"]
