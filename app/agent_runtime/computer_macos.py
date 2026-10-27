from __future__ import annotations

"""macOS screenshot/coordinate Computer Use backend.

This is deliberately less capable than Windows UIA: macOS accessibility-tree
controls and per-window targeting are not implied by a screenshot-only backend.
Screen Recording and Accessibility consent are both required before an action.
"""

import io
import platform
import threading
import time
import uuid

from .computer_types import (
    ComputerAction,
    ComputerActionType,
    ComputerExecution,
    ComputerFrame,
    ComputerObservation,
    ComputerWindow,
    ComputerRect,
)

CAPTURE_PROFILES = {
    "fast": (60, 1_440_000),
    "balanced": (72, 2_500_000),
    "high": (88, 5_000_000),
    "lossless": (0, 0),
}


def macos_computer_available() -> bool:
    if platform.system() != "Darwin":
        return False
    try:
        import Quartz  # noqa: F401
        import pyautogui  # noqa: F401
        from PIL import Image  # noqa: F401
    except ImportError:
        return False
    return True


def _require_permission(*, screen: bool = False, input_: bool = False) -> None:
    import Quartz

    if screen:
        preflight = getattr(Quartz, "CGPreflightScreenCaptureAccess", None)
        if not callable(preflight) or not preflight():
            raise PermissionError(
                "Loom needs Screen Recording access: macOS System Settings > "
                "Privacy & Security > Screen & System Audio Recording. "
                "Grant access to Loom and restart the application."
            )
    if input_:
        import ApplicationServices
        trusted = getattr(ApplicationServices, "AXIsProcessTrusted", None)
        if not callable(trusted) or not trusted():
            raise PermissionError(
                "Loom needs Accessibility access: macOS System Settings > "
                "Privacy & Security > Accessibility. Grant access to Loom "
                "and restart the application."
            )


def _key(value: str) -> str:
    aliases = {
        "ctrl": "command", "cmd": "command", "win": "command",
        "super": "command", "meta": "command", "option": "alt",
        "return": "enter", "esc": "escape", "del": "delete",
    }
    return aliases.get(value.lower(), value.lower())


def _unicode_units(text: str, *, max_units: int = 20):
    """Yield UTF-16 units without splitting a surrogate pair across events."""
    chunk: list[int] = []
    for char in text:
        data = char.encode("utf-16-le")
        units = [int.from_bytes(data[i:i + 2], "little") for i in range(0, len(data), 2)]
        if len(chunk) + len(units) > max_units and chunk:
            yield tuple(chunk)
            chunk = []
        chunk.extend(units)
    if chunk:
        yield tuple(chunk)


def _insert_unicode(text: str) -> None:
    """Send Unicode via Quartz without touching another app's clipboard.

    Some native controls ignore the CGEvent Unicode payload. The caller must
    re-observe the UI; event injection is not proof the editor accepted text.
    """
    import Quartz

    for units in _unicode_units(text):
        down = Quartz.CGEventCreateKeyboardEvent(None, 0, True)
        up = Quartz.CGEventCreateKeyboardEvent(None, 0, False)
        if down is None or up is None:
            raise RuntimeError("macOS could not create Unicode keyboard events")
        Quartz.CGEventKeyboardSetUnicodeString(down, len(units), units)
        Quartz.CGEventKeyboardSetUnicodeString(up, len(units), units)
        try:
            Quartz.CGEventPost(Quartz.kCGHIDEventTap, down)
        finally:
            # Release even if the event post fails; never strand a key down.
            Quartz.CGEventPost(Quartz.kCGHIDEventTap, up)
        time.sleep(0.003)


def _visible_applications(width: int, height: int):
    """Best-effort app-level window list; IDs identify apps, not AX controls."""
    import AppKit
    import Quartz

    front = AppKit.NSWorkspace.sharedWorkspace().frontmostApplication()
    foreground_pid = int(front.processIdentifier()) if front else 0
    windows = []
    seen_pids = set()
    options = Quartz.kCGWindowListOptionOnScreenOnly | Quartz.kCGWindowListExcludeDesktopElements
    for info in Quartz.CGWindowListCopyWindowInfo(options, Quartz.kCGNullWindowID) or []:
        pid = int(info.get("kCGWindowOwnerPID", 0) or 0)
        if pid <= 0 or pid in seen_pids:
            continue
        if int(info.get("kCGWindowLayer", 0) or 0) != 0:
            continue
        bounds = info.get("kCGWindowBounds") or {}
        x = round(float(bounds.get("X", 0)))
        y = round(float(bounds.get("Y", 0)))
        w = round(float(bounds.get("Width", 0)))
        h = round(float(bounds.get("Height", 0)))
        if w <= 0 or h <= 0 or x >= width or y >= height or x + w <= 0 or y + h <= 0:
            continue
        seen_pids.add(pid)
        app_name = str(info.get("kCGWindowOwnerName") or "")
        window_title = str(info.get("kCGWindowName") or "")
        windows.append(ComputerWindow(
            window_id=f"macpid:{pid}", title=window_title or app_name,
            process_name=app_name,
            rect=ComputerRect(x, y, x + w, y + h),
            foreground=(pid == foreground_pid),
        ))
        if len(windows) >= 32:
            break
    active = next((window for window in windows if window.foreground), None)
    if active is None and foreground_pid:
        # The frontmost app may not expose a conventional on-screen window.
        name = str(front.localizedName() or "") if front else ""
        active = ComputerWindow(window_id=f"macpid:{foreground_pid}",
                                title=name, process_name=name, foreground=True)
    return active, tuple(windows)


def _activate_visible_app(window_id: str, observation: ComputerObservation) -> None:
    import AppKit

    if window_id not in {window.window_id for window in observation.windows}:
        raise RuntimeError("macOS application target is stale; observe again before switching")
    if not window_id.startswith("macpid:"):
        raise RuntimeError("unsupported macOS application target")
    pid = int(window_id.split(":", 1)[1])
    application = AppKit.NSRunningApplication.runningApplicationWithProcessIdentifier_(pid)
    if application is None or application.isTerminated():
        raise RuntimeError("macOS target application is no longer running")
    if not application.activateWithOptions_(AppKit.NSApplicationActivateIgnoringOtherApps):
        raise RuntimeError("macOS could not activate the selected application")


class MacOSComputerOperator:
    name = "macos-quartz"

    def __init__(self, *, capture_profile: str = "balanced") -> None:
        if not macos_computer_available():
            raise RuntimeError("macOS Computer Use requires Quartz, Pillow and pyautogui")
        self._lock = threading.RLock()
        self.capture_profile = "balanced"
        self.set_capture_profile(capture_profile)

    def set_capture_profile(self, profile: str) -> str:
        value = str(profile or "").strip().lower()
        if value not in CAPTURE_PROFILES:
            raise ValueError("unknown macOS screenshot capture profile")
        self.capture_profile = value
        return value

    def status(self) -> dict[str, object]:
        import Quartz

        preflight = getattr(Quartz, "CGPreflightScreenCaptureAccess", None)
        import ApplicationServices
        trusted = getattr(ApplicationServices, "AXIsProcessTrusted", None)
        return {
            "backend": self.name,
            "platform": "Darwin",
            "screen_recording_granted": bool(preflight()) if callable(preflight) else False,
            "accessibility_granted": bool(trusted()) if callable(trusted) else False,
            "capture_profile": self.capture_profile,
            "observation": "primary-display screenshot + visible application list, no accessibility-tree controls",
            "semantic_layer": {"state": "unavailable", "reason": "macos_visual_backend"},
            "multi_monitor": "primary-display only",
            "window_switch": "app_activation_only",
            "unicode_input": "Quartz keyboard Unicode events; clipboard remains unchanged",
        }

    def observe(self) -> ComputerObservation:
        return self.observe_layered()

    def observe_layered(self, *, semantics: str = "best_effort", deadline_ms: float = 0.0) -> ComputerObservation:
        del deadline_ms
        _require_permission(screen=True)
        import pyautogui
        from PIL import Image

        with self._lock:
            size = pyautogui.size()
            width, height = int(size.width), int(size.height)
            if width < 1 or height < 1:
                raise RuntimeError("macOS primary-display geometry is unavailable")
            image = pyautogui.screenshot()
            # On Retina screens the screenshot may use backing pixels while
            # pyautogui coordinates use points. Both describe the same display;
            # normalized image points map to the logical coordinate frame.
            quality, max_pixels = CAPTURE_PROFILES[self.capture_profile]
            if max_pixels and image.width * image.height > max_pixels:
                ratio = (max_pixels / (image.width * image.height)) ** 0.5
                image = image.resize(
                    (max(1, round(image.width * ratio)), max(1, round(image.height * ratio))),
                    Image.Resampling.LANCZOS,
                )
            output = io.BytesIO()
            if quality:
                image.convert("RGB").save(output, format="JPEG", quality=quality, subsampling=0)
                media_type = "image/jpeg"
            else:
                image.save(output, format="PNG")
                media_type = "image/png"
            try:
                active, windows = _visible_applications(width, height)
            except (ImportError, OSError, ValueError, AttributeError):
                active, windows = None, ()
            return ComputerObservation(
                observation_id=uuid.uuid4().hex,
                frame=ComputerFrame(
                    frame_id=uuid.uuid4().hex, origin_x=0, origin_y=0,
                    width=width, height=height, source="primary_display",
                    window_id=active.window_id if active else "",
                ),
                active_window=active,
                windows=windows,
                image_data=output.getvalue(),
                image_media_type=media_type,
                semantics={"state": "unavailable", "requested": semantics, "reason": "macos_visual_backend"},
                metadata={
                    "backend": self.name,
                    "capture_profile": self.capture_profile,
                    "screenshot_pixels": [image.width, image.height],
                    "coordinate_space": "primary_display_logical_points",
                },
            )

    def execute(self, action: ComputerAction, observation: ComputerObservation) -> ComputerExecution:
        if action.type in {ComputerActionType.FINISH, ComputerActionType.CALL_USER}:
            return ComputerExecution(ok=True, message=action.type.value, action=action, native=True)
        if action.type is ComputerActionType.WAIT:
            time.sleep(max(0.05, min(30.0, action.duration_ms / 1000.0 or 1.0)))
            return ComputerExecution(ok=True, message="wait completed", action=action, native=True)
        _require_permission(screen=True, input_=True)
        import pyautogui

        if action.control_id:
            raise RuntimeError("macOS visual backend cannot target control_id; use screenshot coordinates")
        with self._lock:
            if action.type is ComputerActionType.SWITCH_WINDOW:
                _activate_visible_app(action.window_id, observation)
                return ComputerExecution(
                    ok=True, message="macOS application activation requested; verify foreground app",
                    action=action, native=True,
                    details={"input_backend": "appkit", "verification": "foreground_app"},
                )
            if action.window_id:
                raise RuntimeError("macOS coordinate actions do not accept window_id")
            if observation.frame.window_id:
                try:
                    import AppKit
                    front = AppKit.NSWorkspace.sharedWorkspace().frontmostApplication()
                    actual = f"macpid:{int(front.processIdentifier())}" if front else ""
                except (ImportError, AttributeError) as exc:
                    raise RuntimeError("macOS foreground could not be verified; observe again") from exc
                if actual != observation.frame.window_id:
                    raise RuntimeError("macOS foreground changed; observe again before input")
            size = pyautogui.size()
            if (size.width, size.height) != (observation.frame.width, observation.frame.height):
                raise RuntimeError("macOS display geometry changed; observe again before acting")
            point = observation.frame.to_screen(action.point) if action.point else None
            if action.type in {
                ComputerActionType.CLICK, ComputerActionType.DOUBLE_CLICK,
                ComputerActionType.RIGHT_CLICK, ComputerActionType.MOVE,
            }:
                if point is None:
                    raise ValueError("pointer action needs a screenshot-relative point")
                if action.type is ComputerActionType.MOVE:
                    pyautogui.moveTo(*point)
                else:
                    pyautogui.click(*point,
                                    clicks=2 if action.type is ComputerActionType.DOUBLE_CLICK else 1,
                                    interval=0.1,
                                    button="right" if action.type is ComputerActionType.RIGHT_CLICK else "left")
            elif action.type is ComputerActionType.DRAG:
                start = observation.frame.to_screen(action.point)
                finish = observation.frame.to_screen(action.end_point)
                pyautogui.moveTo(*start)
                pyautogui.dragTo(*finish, duration=max(0.15, action.duration_ms / 1000.0), button="left")
            elif action.type is ComputerActionType.SCROLL:
                if point:
                    pyautogui.moveTo(*point)
                amount = max(1, min(30, round(action.amount / 120)))
                if action.direction in {"up", "down"}:
                    pyautogui.scroll(amount if action.direction == "up" else -amount)
                else:
                    pyautogui.hscroll(amount if action.direction == "right" else -amount)
            elif action.type is ComputerActionType.HOTKEY:
                pyautogui.hotkey(*(_key(key) for key in action.keys))
            elif action.type is ComputerActionType.KEY:
                for key in action.keys:
                    pyautogui.press(_key(key))
            elif action.type is ComputerActionType.CLEAR_TEXT:
                if point:
                    pyautogui.click(*point)
                pyautogui.hotkey("command", "a")
                pyautogui.press("backspace")
            elif action.type is ComputerActionType.TYPE:
                if point:
                    pyautogui.click(*point)
                _insert_unicode(action.text)
            else:
                raise RuntimeError(f"unsupported macOS Computer Use action: {action.type.value}")
            return ComputerExecution(
                ok=True, message="macOS desktop action injected; verify with a new observation",
                action=action, native=False, fallback_used=True,
                details={
                    "input_backend": "quartz-unicode" if action.type is ComputerActionType.TYPE
                    else "pyautogui-macos",
                    "verification": "visual_reobserve",
                    "typed_text_length": len(action.text) if action.type is ComputerActionType.TYPE else 0,
                },
            )

    def close(self) -> None:
        pass
