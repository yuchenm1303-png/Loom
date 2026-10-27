"""Native macOS bridge contract, intentionally does not capture screen or post input."""
from __future__ import annotations

import platform
import sys


def main() -> None:
    if platform.system() != "Darwin":
        raise RuntimeError("macOS bridge contract requires Darwin")
    import Quartz
    import AppKit
    from app.agent_runtime.computer_macos import _unicode_units

    assert AppKit.NSWorkspace is not None
    assert callable(Quartz.CGWindowListCopyWindowInfo)
    assert callable(Quartz.CGPreflightScreenCaptureAccess)
    assert callable(Quartz.AXIsProcessTrusted)

    text = "你好😀"
    units = next(iter(_unicode_units(text)))
    event = Quartz.CGEventCreateKeyboardEvent(None, 0, True)
    if event is None:
        raise RuntimeError("Quartz could not allocate a keyboard event")
    # Test PyObjC's UniChar* argument conversion, NOT posting to the user's input.
    Quartz.CGEventKeyboardSetUnicodeString(event, len(units), units)
    length, readback = Quartz.CGEventKeyboardGetUnicodeString(event, 20, None, None)
    assert length == len(units), (length, len(units))
    assert tuple(readback[:length]) == units, (readback, units)
    print("loom-macos-native-bridge-ok")


if __name__ == "__main__":
    main()
