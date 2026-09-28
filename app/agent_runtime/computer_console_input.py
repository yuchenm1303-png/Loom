"""Isolated Windows console-input writer used by Computer Use.

The desktop app server may itself inherit a development console. A process can
only be attached to one console, so detaching that long-lived server just to
type into cmd would break its logging and stdio. This module is launched as a
short-lived, console-free helper and attaches only itself to the target.
"""

from __future__ import annotations

import ctypes
import json
import sys


def _result(ok: bool, stage: str, process_id: int, **extra: object) -> dict[str, object]:
    return {"ok": bool(ok), "stage": stage, "target_pid": int(process_id), **extra}


def write_console_input(process_id: int, text: str) -> dict[str, object]:
    if not process_id or not text:
        return _result(False, "invalid_input", process_id)
    if sys.platform != "win32":
        return _result(False, "unsupported_platform", process_id)

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.AttachConsole.argtypes = [ctypes.c_ulong]
    kernel32.AttachConsole.restype = ctypes.c_int
    kernel32.CreateFileW.argtypes = [
        ctypes.c_wchar_p,
        ctypes.c_ulong,
        ctypes.c_ulong,
        ctypes.c_void_p,
        ctypes.c_ulong,
        ctypes.c_ulong,
        ctypes.c_void_p,
    ]
    kernel32.CreateFileW.restype = ctypes.c_void_p
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel32.CloseHandle.restype = ctypes.c_int
    kernel32.FreeConsole.restype = ctypes.c_int
    if not bool(kernel32.AttachConsole(int(process_id))):
        return _result(
            False,
            "attach_console",
            process_id,
            win32_error=int(ctypes.get_last_error()),
        )

    invalid_handle = ctypes.c_void_p(-1).value
    handle = invalid_handle
    try:
        class CharUnion(ctypes.Union):
            _fields_ = [("UnicodeChar", ctypes.c_wchar), ("AsciiChar", ctypes.c_char)]

        class KeyEventRecord(ctypes.Structure):
            _fields_ = [
                ("bKeyDown", ctypes.c_int),
                ("wRepeatCount", ctypes.c_ushort),
                ("wVirtualKeyCode", ctypes.c_ushort),
                ("wVirtualScanCode", ctypes.c_ushort),
                ("uChar", CharUnion),
                ("dwControlKeyState", ctypes.c_ulong),
            ]

        class EventUnion(ctypes.Union):
            _fields_ = [("KeyEvent", KeyEventRecord), ("padding", ctypes.c_byte * 16)]

        class InputRecord(ctypes.Structure):
            _fields_ = [("EventType", ctypes.c_ushort), ("Event", EventUnion)]

        kernel32.WriteConsoleInputW.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(InputRecord),
            ctypes.c_ulong,
            ctypes.POINTER(ctypes.c_ulong),
        ]
        kernel32.WriteConsoleInputW.restype = ctypes.c_int
        handle = kernel32.CreateFileW(
            "CONIN$",
            0xC0000000,  # GENERIC_READ | GENERIC_WRITE
            0x00000003,  # FILE_SHARE_READ | FILE_SHARE_WRITE
            None,
            3,  # OPEN_EXISTING
            0,
            None,
        )
        if handle in (0, invalid_handle):
            return _result(
                False,
                "open_conin",
                process_id,
                win32_error=int(ctypes.get_last_error()),
            )

        records: list[InputRecord] = []
        encoded = text.encode("utf-16-le")
        for index in range(0, len(encoded), 2):
            char = chr(int.from_bytes(encoded[index : index + 2], "little"))
            for is_down in (True, False):
                record = InputRecord()
                record.EventType = 0x0001  # KEY_EVENT
                record.Event.KeyEvent = KeyEventRecord(
                    int(is_down), 1, 0, 0, CharUnion(UnicodeChar=char), 0
                )
                records.append(record)

        array_type = InputRecord * len(records)
        written = ctypes.c_ulong(0)
        ok = bool(
            kernel32.WriteConsoleInputW(
                handle,
                array_type(*records),
                len(records),
                ctypes.byref(written),
            )
        )
        if not ok or int(written.value) != len(records):
            return _result(
                False,
                "write_console_input",
                process_id,
                win32_error=int(ctypes.get_last_error()),
                records_written=int(written.value),
                records_requested=len(records),
            )
        return _result(
            True,
            "completed",
            process_id,
            records_written=int(written.value),
        )
    finally:
        if handle not in (0, invalid_handle):
            kernel32.CloseHandle(handle)
        kernel32.FreeConsole()


def main() -> int:
    try:
        payload = json.loads(sys.stdin.read())
        result = write_console_input(
            int(payload.get("process_id") or 0), str(payload.get("text") or "")
        )
    except Exception as exc:
        result = {"ok": False, "stage": "helper_exception", "error_type": type(exc).__name__}
    sys.stdout.write(json.dumps(result, ensure_ascii=True, separators=(",", ":")))
    return 0 if bool(result.get("ok")) else 1


if __name__ == "__main__":
    raise SystemExit(main())
