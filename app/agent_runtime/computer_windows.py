from __future__ import annotations

import ctypes
import io
import platform
import threading
import time
import uuid
from collections import OrderedDict
from typing import Protocol

from .computer_types import (
    ComputerAction,
    ComputerActionType,
    ComputerControl,
    ComputerExecution,
    ComputerFrame,
    ComputerObservation,
    ComputerPoint,
    ComputerRect,
    ComputerWindow,
)


class ComputerOperator(Protocol):
    name: str

    def status(self) -> dict[str, object]:
        ...

    def observe(self) -> ComputerObservation:
        ...

    def execute(self, action: ComputerAction, observation: ComputerObservation) -> ComputerExecution:
        ...

    def close(self) -> None:
        ...


def windows_computer_available() -> bool:
    if platform.system() != "Windows":
        return False
    try:
        import PIL.ImageGrab  # noqa: F401
        import pyautogui  # noqa: F401
        import pywinauto  # noqa: F401
        import win32api  # noqa: F401
        import win32con  # noqa: F401
        import win32gui  # noqa: F401
    except Exception:
        return False
    return True


def enable_per_monitor_v2_dpi_awareness() -> str:
    """Best-effort process DPI setup before Computer Use reads screen geometry."""

    if platform.system() != "Windows":
        return "unsupported"
    user32 = ctypes.windll.user32
    try:
        # DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 == (HANDLE)-4
        if user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):
            return "per-monitor-v2"
    except Exception:
        pass
    try:
        shcore = ctypes.windll.shcore
        # PROCESS_PER_MONITOR_DPI_AWARE == 2
        result = int(shcore.SetProcessDpiAwareness(2))
        if result in {0, 0x80070005}:  # success or already set by host process
            return "per-monitor"
    except Exception:
        pass
    try:
        user32.SetProcessDPIAware()
        return "system-aware"
    except Exception:
        return "unknown"


class PyWinAutoWindowsOperator:
    """Windows UIA-first operator with virtual-desktop coordinate fallback.

    UI Automation is used for semantic control discovery and native Invoke/Edit
    operations where available. Pointer fallbacks use Win32 virtual-desktop screen
    coordinates, avoiding pyautogui's historical primary-monitor assumptions.
    pyautogui is retained for keyboard shortcuts only.
    """

    name = "windows-uia"

    def __init__(self, *, max_controls: int = 300, max_windows: int = 48) -> None:
        if not windows_computer_available():
            raise RuntimeError(
                "Windows Computer Use dependencies are unavailable; install Loom with the computer extra on Windows"
            )
        self.max_controls = max(1, int(max_controls))
        self.max_windows = max(1, int(max_windows))
        self.dpi_awareness = enable_per_monitor_v2_dpi_awareness()
        self._lock = threading.RLock()
        self._control_maps: OrderedDict[str, dict[str, object]] = OrderedDict()

    def status(self) -> dict[str, object]:
        return {
            "backend": self.name,
            "platform": platform.system(),
            "dpi_awareness": self.dpi_awareness,
            "observation": "active-window screenshot + window list + UI Automation controls",
            "pointer_fallback": "Win32 virtual-desktop coordinates",
            "keyboard_fallback": "pyautogui hotkeys + SendInput Unicode text",
            "secure_desktop": False,
            "elevated_window_access": "subject to Windows UIPI/integrity boundaries",
        }

    def observe(self) -> ComputerObservation:
        import win32api
        import win32gui
        from PIL import ImageGrab

        with self._lock:
            hwnd = int(win32gui.GetForegroundWindow())
            if not hwnd or not win32gui.IsWindow(hwnd):
                raise RuntimeError("Windows foreground window is unavailable")
            left, top, right, bottom = map(int, win32gui.GetWindowRect(hwnd))
            if right <= left or bottom <= top:
                raise RuntimeError("Windows foreground window has an invalid rectangle")

            dpi = self._dpi_for_window(hwnd)
            monitor_id = ""
            try:
                monitor = win32api.MonitorFromWindow(hwnd, 2)
                info = win32api.GetMonitorInfo(monitor)
                monitor_id = str(info.get("Device") or monitor)
            except Exception:
                pass

            frame = ComputerFrame(
                frame_id=uuid.uuid4().hex,
                origin_x=left,
                origin_y=top,
                width=right - left,
                height=bottom - top,
                source="active_window",
                window_id=self._window_id(hwnd),
                monitor_id=monitor_id,
                dpi_x=dpi,
                dpi_y=dpi,
            )
            image = ImageGrab.grab(bbox=(left, top, right, bottom), all_screens=True)
            output = io.BytesIO()
            image.save(output, format="PNG")
            screenshot = output.getvalue()

            windows = self._enumerate_windows(hwnd)
            active = next((item for item in windows if item.foreground), None)
            if active is None:
                active = ComputerWindow(
                    window_id=self._window_id(hwnd),
                    title=str(win32gui.GetWindowText(hwnd) or ""),
                    rect=ComputerRect(left, top, right, bottom),
                    foreground=True,
                )

            observation_id = uuid.uuid4().hex
            controls, mapping = self._collect_uia_controls(hwnd, observation_id)
            self._control_maps[observation_id] = mapping
            while len(self._control_maps) > 4:
                self._control_maps.popitem(last=False)

            return ComputerObservation(
                observation_id=observation_id,
                frame=frame,
                image_png=screenshot,
                active_window=active,
                windows=windows,
                controls=controls,
                metadata={
                    "dpi_awareness": self.dpi_awareness,
                    "control_backend": "uia",
                },
            )

    def execute(self, action: ComputerAction, observation: ComputerObservation) -> ComputerExecution:
        with self._lock:
            if action.type in {ComputerActionType.FINISH, ComputerActionType.CALL_USER}:
                return ComputerExecution(ok=True, message=action.type.value, action=action, native=True)
            if action.type is ComputerActionType.WAIT:
                time.sleep(max(0.05, action.duration_ms / 1000.0 if action.duration_ms else 1.0))
                return ComputerExecution(ok=True, message="wait completed", action=action, native=True)
            if action.type is ComputerActionType.SWITCH_WINDOW:
                return self._switch_window(action)

            wrapper = None
            if action.control_id:
                wrapper = self._control_maps.get(observation.observation_id, {}).get(action.control_id)
                if wrapper is None:
                    raise RuntimeError(
                        "computer control target is stale; refresh computer_observe and use its latest state_revision"
                    )

            if action.type is ComputerActionType.TYPE:
                if wrapper is not None:
                    native = self._native_type(wrapper, action.text)
                    if native:
                        return ComputerExecution(
                            ok=True,
                            message="text input completed through UI Automation",
                            action=action,
                            native=True,
                        )
                    if not self._native_click(wrapper, double=False, right=False):
                        point = self._wrapper_center(wrapper, observation.frame)
                        if point is not None:
                            self._click_point(observation.frame, point)
                elif action.point is not None:
                    self._click_point(observation.frame, action.point)
                self._send_unicode_text(action.text)
                return ComputerExecution(
                    ok=True,
                    message="text input completed through Unicode SendInput fallback",
                    action=action,
                    native=False,
                    fallback_used=True,
                )

            if wrapper is not None and action.type in {
                ComputerActionType.CLICK,
                ComputerActionType.DOUBLE_CLICK,
                ComputerActionType.RIGHT_CLICK,
            }:
                if self._native_click(
                    wrapper,
                    double=action.type is ComputerActionType.DOUBLE_CLICK,
                    right=action.type is ComputerActionType.RIGHT_CLICK,
                ):
                    return ComputerExecution(
                        ok=True,
                        message="control action completed through UI Automation",
                        action=action,
                        native=True,
                    )
                point = self._wrapper_center(wrapper, observation.frame)
                if point is not None:
                    return self._coordinate_action(action, observation.frame, point_override=point, fallback=True)

            return self._coordinate_action(action, observation.frame, fallback=wrapper is not None)

    def close(self) -> None:
        with self._lock:
            self._control_maps.clear()

    def _coordinate_action(
        self,
        action: ComputerAction,
        frame: ComputerFrame,
        *,
        point_override: ComputerPoint | None = None,
        fallback: bool = False,
    ) -> ComputerExecution:
        import pyautogui
        import win32api
        import win32con

        point = point_override or action.point
        if action.type is ComputerActionType.MOVE:
            if point is None:
                raise ValueError("move requires point")
            win32api.SetCursorPos(frame.to_screen(point))
        elif action.type in {
            ComputerActionType.CLICK,
            ComputerActionType.DOUBLE_CLICK,
            ComputerActionType.RIGHT_CLICK,
        }:
            if point is None:
                raise ValueError(f"{action.type.value} requires point")
            self._click_point(
                frame,
                point,
                double=action.type is ComputerActionType.DOUBLE_CLICK,
                right=action.type is ComputerActionType.RIGHT_CLICK,
            )
        elif action.type is ComputerActionType.DRAG:
            if action.point is None or action.end_point is None:
                raise ValueError("drag requires point and end_point")
            start = frame.to_screen(action.point)
            end = frame.to_screen(action.end_point)
            win32api.SetCursorPos(start)
            win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
            try:
                if action.duration_ms:
                    time.sleep(action.duration_ms / 1000.0)
                win32api.SetCursorPos(end)
            finally:
                win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
        elif action.type is ComputerActionType.SCROLL:
            if point is not None:
                win32api.SetCursorPos(frame.to_screen(point))
            ticks = max(1, min(30, action.amount // 120 if action.amount >= 120 else 1)) * 120
            if action.direction == "down":
                delta = -ticks
                flag = win32con.MOUSEEVENTF_WHEEL
            elif action.direction == "up":
                delta = ticks
                flag = win32con.MOUSEEVENTF_WHEEL
            elif action.direction == "right":
                delta = ticks
                flag = getattr(win32con, "MOUSEEVENTF_HWHEEL", 0x01000)
            else:
                delta = -ticks
                flag = getattr(win32con, "MOUSEEVENTF_HWHEEL", 0x01000)
            win32api.mouse_event(flag, 0, 0, delta, 0)
        elif action.type is ComputerActionType.HOTKEY:
            pyautogui.hotkey(*action.keys)
        elif action.type is ComputerActionType.KEY:
            for key in action.keys:
                pyautogui.press(key)
        else:
            raise ValueError(f"unsupported Windows coordinate action: {action.type.value}")

        return ComputerExecution(
            ok=True,
            message=f"{action.type.value} completed",
            action=action,
            native=False,
            fallback_used=fallback,
        )

    def _click_point(
        self,
        frame: ComputerFrame,
        point: ComputerPoint,
        *,
        double: bool = False,
        right: bool = False,
    ) -> None:
        import win32api
        import win32con

        win32api.SetCursorPos(frame.to_screen(point))
        if right:
            down = win32con.MOUSEEVENTF_RIGHTDOWN
            up = win32con.MOUSEEVENTF_RIGHTUP
        else:
            down = win32con.MOUSEEVENTF_LEFTDOWN
            up = win32con.MOUSEEVENTF_LEFTUP
        count = 2 if double else 1
        for index in range(count):
            win32api.mouse_event(down, 0, 0, 0, 0)
            win32api.mouse_event(up, 0, 0, 0, 0)
            if index + 1 < count:
                time.sleep(0.08)

    def _native_click(self, wrapper, *, double: bool, right: bool) -> bool:
        if double or right:
            return False
        try:
            invoke = getattr(wrapper, "invoke", None)
            if callable(invoke):
                invoke()
                return True
        except Exception:
            pass
        return False

    def _native_type(self, wrapper, text: str) -> bool:
        for name in ("set_edit_text", "set_text"):
            try:
                method = getattr(wrapper, name, None)
                if callable(method):
                    method(text)
                    return True
            except Exception:
                continue
        return False

    def _wrapper_center(self, wrapper, frame: ComputerFrame) -> ComputerPoint | None:
        try:
            rect = wrapper.rectangle()
            return ComputerRect(int(rect.left), int(rect.top), int(rect.right), int(rect.bottom)).center_in(frame)
        except Exception:
            return None

    def _switch_window(self, action: ComputerAction) -> ComputerExecution:
        import win32con
        import win32gui

        hwnd = self._parse_window_id(action.window_id)
        if not win32gui.IsWindow(hwnd):
            raise RuntimeError(f"Windows window no longer exists: {action.window_id}")
        if win32gui.IsIconic(hwnd):
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        win32gui.SetForegroundWindow(hwnd)
        return ComputerExecution(ok=True, message="window switched", action=action, native=True)

    def _collect_uia_controls(self, hwnd: int, observation_id: str) -> tuple[tuple[ComputerControl, ...], dict[str, object]]:
        from pywinauto import Desktop

        controls: list[ComputerControl] = []
        mapping: dict[str, object] = {}
        try:
            window = Desktop(backend="uia").window(handle=hwnd)
            descendants = window.descendants()
        except Exception:
            return (), {}

        for index, wrapper in enumerate(descendants[: self.max_controls]):
            try:
                rect = wrapper.rectangle()
                candidate = ComputerRect(int(rect.left), int(rect.top), int(rect.right), int(rect.bottom))
                if not wrapper.is_visible():
                    continue
                info = getattr(wrapper, "element_info", None)
                name = str(getattr(info, "name", "") or getattr(wrapper, "window_text", lambda: "")())
                control_type = str(getattr(info, "control_type", "") or wrapper.friendly_class_name())
                automation_id = str(getattr(info, "automation_id", "") or "")
                enabled = bool(wrapper.is_enabled())
            except Exception:
                continue
            control_id = f"uia:{index}"
            controls.append(
                ComputerControl(
                    control_id=control_id,
                    name=name,
                    control_type=control_type,
                    automation_id=automation_id,
                    rect=candidate,
                    enabled=enabled,
                )
            )
            mapping[control_id] = wrapper
        return tuple(controls), mapping

    def _enumerate_windows(self, foreground_hwnd: int) -> tuple[ComputerWindow, ...]:
        import win32gui

        items: list[ComputerWindow] = []

        def callback(hwnd: int, _extra) -> None:
            if len(items) >= self.max_windows:
                return
            try:
                if not win32gui.IsWindowVisible(hwnd):
                    return
                title = str(win32gui.GetWindowText(hwnd) or "")
                if not title:
                    return
                left, top, right, bottom = map(int, win32gui.GetWindowRect(hwnd))
                if right <= left or bottom <= top:
                    return
                items.append(
                    ComputerWindow(
                        window_id=self._window_id(hwnd),
                        title=title,
                        rect=ComputerRect(left, top, right, bottom),
                        foreground=hwnd == foreground_hwnd,
                    )
                )
            except Exception:
                return

        win32gui.EnumWindows(callback, None)
        items.sort(key=lambda item: (not item.foreground, item.title.casefold()))
        return tuple(items[: self.max_windows])

    @staticmethod
    def _dpi_for_window(hwnd: int) -> int:
        try:
            return max(1, int(ctypes.windll.user32.GetDpiForWindow(hwnd)))
        except Exception:
            return 96

    @staticmethod
    def _window_id(hwnd: int) -> str:
        return f"0x{int(hwnd):x}"

    @staticmethod
    def _parse_window_id(value: str) -> int:
        text = str(value or "").strip().casefold()
        return int(text, 16) if text.startswith("0x") else int(text)

    @staticmethod
    def _send_unicode_text(text: str) -> None:
        if not text:
            return

        user32 = ctypes.windll.user32
        ULONG_PTR = ctypes.c_size_t

        class MOUSEINPUT(ctypes.Structure):
            _fields_ = [
                ("dx", ctypes.c_long),
                ("dy", ctypes.c_long),
                ("mouseData", ctypes.c_ulong),
                ("dwFlags", ctypes.c_ulong),
                ("time", ctypes.c_ulong),
                ("dwExtraInfo", ULONG_PTR),
            ]

        class KEYBDINPUT(ctypes.Structure):
            _fields_ = [
                ("wVk", ctypes.c_ushort),
                ("wScan", ctypes.c_ushort),
                ("dwFlags", ctypes.c_ulong),
                ("time", ctypes.c_ulong),
                ("dwExtraInfo", ULONG_PTR),
            ]

        class HARDWAREINPUT(ctypes.Structure):
            _fields_ = [
                ("uMsg", ctypes.c_ulong),
                ("wParamL", ctypes.c_ushort),
                ("wParamH", ctypes.c_ushort),
            ]

        class INPUT_UNION(ctypes.Union):
            _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]

        class INPUT(ctypes.Structure):
            _anonymous_ = ("union",)
            _fields_ = [("type", ctypes.c_ulong), ("union", INPUT_UNION)]

        KEYEVENTF_KEYUP = 0x0002
        KEYEVENTF_UNICODE = 0x0004
        INPUT_KEYBOARD = 1

        events: list[INPUT] = []
        encoded = text.encode("utf-16-le")
        for index in range(0, len(encoded), 2):
            code_unit = int.from_bytes(encoded[index : index + 2], "little")
            events.append(
                INPUT(
                    type=INPUT_KEYBOARD,
                    ki=KEYBDINPUT(0, code_unit, KEYEVENTF_UNICODE, 0, 0),
                )
            )
            events.append(
                INPUT(
                    type=INPUT_KEYBOARD,
                    ki=KEYBDINPUT(0, code_unit, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP, 0, 0),
                )
            )
        array_type = INPUT * len(events)
        sent = int(user32.SendInput(len(events), array_type(*events), ctypes.sizeof(INPUT)))
        if sent != len(events):
            raise RuntimeError(f"Windows SendInput accepted {sent}/{len(events)} Unicode key events")


__all__ = [
    "ComputerOperator",
    "PyWinAutoWindowsOperator",
    "enable_per_monitor_v2_dpi_awareness",
    "windows_computer_available",
]
