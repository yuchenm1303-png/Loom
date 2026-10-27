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
        trusted = getattr(Quartz, "AXIsProcessTrusted", None)
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
        trusted = getattr(Quartz, "AXIsProcessTrusted", None)
        return {
            "backend": self.name,
            "platform": "Darwin",
            "screen_recording_granted": bool(preflight()) if callable(preflight) else False,
            "accessibility_granted": bool(trusted()) if callable(trusted) else False,
            "capture_profile": self.capture_profile,
            "observation": "primary-display screenshot (visual only; no accessibility-tree controls)",
            "semantic_layer": {"state": "unavailable", "reason": "macos_visual_backend"},
            "multi_monitor": "primary-display only",
            "window_switch": "unsupported",
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
            return ComputerObservation(
                observation_id=uuid.uuid4().hex,
                frame=ComputerFrame(
                    frame_id=uuid.uuid4().hex, origin_x=0, origin_y=0,
                    width=width, height=height, source="primary_display",
                ),
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

        if action.control_id or action.window_id:
            raise RuntimeError("macOS visual backend cannot target control_id/window_id; use screenshot coordinates")
        with self._lock:
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
                # pyautogui.write cannot reliably type non-ASCII on macOS.
                # Refuse rather than silently corrupting user text or clipboard.
                if not action.text.isascii():
                    raise RuntimeError("macOS Unicode text insertion is not yet supported by this backend")
                pyautogui.write(action.text, interval=0.002)
            else:
                raise RuntimeError(f"unsupported macOS Computer Use action: {action.type.value}")
            return ComputerExecution(
                ok=True, message="macOS desktop action injected; verify with a new observation",
                action=action, native=False, fallback_used=True,
                details={"input_backend": "pyautogui-macos", "verification": "visual_reobserve"},
            )

    def close(self) -> None:
        pass
