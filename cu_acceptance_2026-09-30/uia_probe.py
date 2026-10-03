"""UIA semantic probes used during Computer Use acceptance tests.

Reads the focused edit / document control text, the active window title and the
top-level UIA children for a window we already know by hwnd. Returns plain
strings so the model can decide whether a Computer Use action really changed
the target document.
"""
from __future__ import annotations

import argparse
import sys

import psutil
from pywinauto import Desktop
from pywinauto.findwindows import ElementNotFoundError


def active_hwnd() -> int:
    import win32gui

    return int(win32gui.GetForegroundWindow())


def _ctype(desc) -> str:
    info = getattr(desc, "element_info", None)
    if info is not None and getattr(info, "control_type", None):
        return str(info.control_type)
    try:
        return str(desc.friendly_class_name())
    except Exception:
        return ""


def focused_control_text(hwnd: int) -> dict[str, object]:
    try:
        window = Desktop(backend="uia").window(handle=hwnd)
    except ElementNotFoundError:
        return {"hwnd": int(hwnd), "available": False, "reason": "window_not_found"}

    try:
        from pywinauto import timings

        timings.wait_until_passes(0.4, 0.05, lambda: True)
    except Exception:
        pass

    candidates = [desc for desc in window.descendants() if desc.is_visible()]
    preferred = [desc for desc in candidates if _ctype(desc) in {"Edit", "Document", "Text"}]
    text_controls = preferred or candidates[:3]
    pieces: list[dict[str, object]] = []
    for desc in text_controls[:6]:
        try:
            info = desc.element_info
            pieces.append(
                {
                    "control_type": _ctype(desc),
                    "name": info.name,
                    "automation_id": info.automation_id,
                    "text": desc.window_text(),
                    "enabled": bool(desc.is_enabled()),
                    "rect": [
                        int(desc.rectangle().left),
                        int(desc.rectangle().top),
                        int(desc.rectangle().right),
                        int(desc.rectangle().bottom),
                    ],
                }
            )
        except Exception:
            continue
    focused_meta = None
    try:
        focused = window.set_focus()
        focused_meta = {
            "control_type": _ctype(focused),
            "name": focused.element_info.name,
            "text": focused.window_text(),
        }
    except Exception:
        focused_meta = None
    return {
        "hwnd": int(hwnd),
        "available": True,
        "focused": focused_meta,
        "text_controls": pieces,
    }


def window_meta(hwnd: int) -> dict[str, object]:
    import win32process

    if hwnd <= 0:
        return {"hwnd": int(hwnd), "available": False}
    try:
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
    except Exception:
        pid = 0
    title = ""
    process_name = ""
    try:
        from pywinauto import Desktop as _D

        w = _D(backend="uia").window(handle=hwnd)
        title = w.window_text()
        try:
            process_name = str(psutil.Process(int(w.process_id())).name())
        except Exception:
            process_name = ""
    except Exception:
        pass
    return {
        "hwnd": int(hwnd),
        "title": title,
        "pid": int(pid) if pid else 0,
        "process": process_name,
    }


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--hwnd", type=int, default=0, help="hwnd to probe; 0 = foreground")
    parser.add_argument(
        "--mode",
        choices=("active", "focused-text", "window"),
        default="focused-text",
    )
    args = parser.parse_args(argv)

    hwnd = int(args.hwnd) or active_hwnd()
    if args.mode == "active":
        print(window_meta(hwnd))
    elif args.mode == "window":
        print(window_meta(hwnd))
    else:
        print(focused_control_text(hwnd))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))