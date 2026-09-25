from __future__ import annotations

import ctypes
import io
import os
import platform
import threading
import time
import uuid
from collections import OrderedDict
from typing import Protocol

from .computer_semantics import (
    DEFAULT_BEST_EFFORT_DEADLINE_MS,
    DEFAULT_REQUIRED_DEADLINE_MS,
    MAX_BEST_EFFORT_DEADLINE_MS,
    MAX_REQUIRED_DEADLINE_MS,
    SemanticLayer,
    SemanticLayerProvider,
    SemanticState,
)
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


#: Semantic-layer request modes accepted by :meth:`observe_layered`.
SEMANTICS_REQUIRED = "required"
SEMANTICS_BEST_EFFORT = "best_effort"
SEMANTICS_SKIP = "skip"

#: Budget for a single native UIA operation on one already-resolved wrapper.
#: Physical input is always available as a fallback, so waiting longer than this
#: buys nothing: it only delays the route that was going to work anyway.
NATIVE_INVOKE_DEADLINE_MS = 1500


def _initialize_com_apartment() -> None:
    """Pre-initialize COM on the semantic worker thread.

    comtypes initializes the apartment lazily on first use, so this is an
    optimization rather than a requirement; the provider treats a failure here
    as non-fatal.
    """

    import comtypes

    comtypes.CoInitializeEx()


#: How long to wait for a window to acknowledge a null message before treating
#: it as not pumping. Activation is interactive, so this is a perceptibility
#: budget rather than a generous timeout.
WINDOW_LIVENESS_TIMEOUT_MS = 300

#: Modifier keys that must never be left logically held after an action. A
#: modifier stuck down makes every later click do something other than click,
#: which from the user's side is indistinguishable from a dead mouse.
_MODIFIER_KEYS = ("alt", "ctrl", "shift", "win", "winleft", "winright", "altleft", "altright")
_KEY_ALIASES = {"control": "ctrl", "ctl": "ctrl", "windows": "win", "return": "enter", "esc": "escape"}
_CONSOLE_PROCESSES = {"cmd.exe", "conhost.exe", "powershell.exe", "pwsh.exe", "windowsterminal.exe"}


def _window_responds(hwnd: int, timeout_ms: int = WINDOW_LIVENESS_TIMEOUT_MS) -> bool | None:
    """Whether this window's thread is still pumping messages.

    Returns True (responds), False (hung) or None (could not tell).

    This matters far more than it looks. Window activation is built out of
    synchronous cross-process calls - ShowWindow, BringWindowToTop,
    SetForegroundWindow - which block until the target's message loop answers,
    and AttachThreadInput additionally *merges Loom's input queue with the
    target's*. Attaching to an application that is not pumping, then blocking on
    it, wedges the merged queue: the user's own mouse and keyboard stop working
    desktop-wide until the call returns. So liveness is checked before touching
    a window, and "could not tell" is never treated as "yes".
    """

    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        is_hung = getattr(user32, "IsHungAppWindow", None)
        if is_hung is not None:
            is_hung.argtypes = [wintypes.HWND]
            is_hung.restype = wintypes.BOOL
            if bool(is_hung(wintypes.HWND(hwnd))):
                return False

        send = user32.SendMessageTimeoutW
        send.argtypes = [
            wintypes.HWND,
            wintypes.UINT,
            wintypes.WPARAM,
            wintypes.LPARAM,
            wintypes.UINT,
            wintypes.UINT,
            ctypes.POINTER(ctypes.c_size_t),
        ]
        send.restype = wintypes.LPARAM
        result = ctypes.c_size_t(0)
        WM_NULL = 0x0000
        SMTO_ABORTIFHUNG = 0x0002
        ok = send(
            wintypes.HWND(hwnd),
            WM_NULL,
            0,
            0,
            SMTO_ABORTIFHUNG,
            int(timeout_ms),
            ctypes.byref(result),
        )
        return bool(ok)
    except Exception:
        return None


def _host_process_ids() -> frozenset[int]:
    """Processes whose windows are Loom's own user interface.

    The desktop app passes its pid when it launches the agent server. Without it
    this returns empty and self-observation is simply not detected, which is the
    pre-existing behaviour rather than a failure.
    """

    raw = str(os.environ.get("LOOM_DESKTOP_HOST_PID") or "").strip()
    pids: set[int] = set()
    for part in raw.replace(";", ",").split(","):
        part = part.strip()
        if part.isdigit():
            pids.add(int(part))
    return frozenset(pids)


def _warm_uia_client() -> None:
    """Build the UI Automation client once, before any caller is waiting.

    Importing pywinauto pulls in comtypes and the UIAutomationClient typelib and
    constructs the UIA COM client: a few hundred milliseconds, paid once per
    process, and long enough to blow an observation budget and be mistaken for an
    unresponsive window.

    This deliberately touches nothing on the desktop. Warmup owns the single
    worker while it runs, so any real work here would be paid by the first real
    caller instead of saving it - and UIA calls that look harmless are not:
    enumerating the eleven top-level windows of an ordinary desktop through
    ``Desktop.windows()`` measured 70 seconds on the machine this was written on.
    """

    from pywinauto import Desktop

    Desktop(backend="uia")


class ComputerOperator(Protocol):
    name: str

    def status(self) -> dict[str, object]:
        ...

    def observe(self) -> ComputerObservation:
        ...

    #: Operators may additionally implement
    #: ``observe_layered(*, semantics: str, deadline_ms: float)`` to let callers
    #: say how much they need the expensive semantic layer. It is intentionally
    #: not part of this Protocol: the store detects it and falls back to
    #: ``observe`` so embedders with a simpler operator keep working.

    def execute(self, action: ComputerAction, observation: ComputerObservation) -> ComputerExecution:
        ...

    def close(self) -> None:
        ...


def _resampling_filter():
    from PIL import Image

    # Pillow 10 moved the constants onto Image.Resampling; older installs keep
    # them on Image itself and Loom supports both.
    resampling = getattr(Image, "Resampling", Image)
    return resampling.LANCZOS


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


#: Screenshot encodings, cheapest first. A grounding model downsamples whatever
#: it receives to a few thousand image tokens, so lossless capture buys nothing
#: it can use while costing upload time in direct proportion to the byte count.
#: Measured on a 2560x1600 desktop showing a photographic wallpaper, which is the
#: case that hurts: 5.5 MB as lossless PNG against 0.55 MB at "balanced", or ~27s
#: of upload against ~3s at the rate fitted from a production trace.
#:
#: JPEG stays at 4:4:4 (subsampling=0). Chroma subsampling smears exactly the
#: thing the policy has to read, the coloured text on small UI controls.
CAPTURE_PROFILES: dict[str, dict[str, object]] = {
    "fast": {"media_type": "image/jpeg", "quality": 60, "max_pixels": 1_440_000},
    "balanced": {"media_type": "image/jpeg", "quality": 72, "max_pixels": 2_500_000},
    "high": {"media_type": "image/jpeg", "quality": 88, "max_pixels": 5_000_000},
    "lossless": {"media_type": "image/png", "quality": 0, "max_pixels": 0},
}
DEFAULT_CAPTURE_PROFILE = "balanced"


class PyWinAutoWindowsOperator:
    """Windows UIA-first operator with virtual-desktop coordinate fallback.

    UI Automation is used for semantic control discovery and native Invoke/Edit
    operations where available. Pointer fallbacks use Win32 virtual-desktop screen
    coordinates, avoiding pyautogui's historical primary-monitor assumptions.
    pyautogui is retained for keyboard shortcuts only.
    """

    name = "windows-uia"

    def __init__(
        self,
        *,
        # Sized to what consumers actually render: the single-loop prompt shows
        # 40 advisory hints and the observe tool exposes at most 80. Walking 300
        # controls to display 40 was paying a cross-process round trip per node
        # for results nobody reads.
        max_controls: int = 80,
        max_windows: int = 48,
        capture_profile: str = DEFAULT_CAPTURE_PROFILE,
        semantics: SemanticLayerProvider | None = None,
    ) -> None:
        if not windows_computer_available():
            raise RuntimeError(
                "Windows Computer Use dependencies are unavailable; install Loom with the computer extra on Windows"
            )
        self.max_controls = max(1, int(max_controls))
        self.max_windows = max(1, int(max_windows))
        self.dpi_awareness = enable_per_monitor_v2_dpi_awareness()
        self._lock = threading.RLock()
        self._control_maps: OrderedDict[str, dict[str, object]] = OrderedDict()
        # Every UI Automation call, including native invokes on wrappers, is
        # dispatched here: one COM apartment, one stuck thread at worst, and a
        # deadline on a boundary whose latency belongs to the app being driven.
        self.host_pids = _host_process_ids()
        self.semantics = semantics or SemanticLayerProvider(initializer=_initialize_com_apartment)
        self.semantics.warmup(_warm_uia_client)
        self.capture_profile = DEFAULT_CAPTURE_PROFILE
        self.set_capture_profile(capture_profile)

    def set_capture_profile(self, profile: str) -> str:
        """Select the screenshot encoding used by later observations."""

        name = str(profile or "").strip().casefold()
        if name not in CAPTURE_PROFILES:
            raise ValueError(
                f"unknown computer capture profile: {profile!r}; expected one of "
                + ", ".join(sorted(CAPTURE_PROFILES))
            )
        with self._lock:
            self.capture_profile = name
        return name

    def status(self) -> dict[str, object]:
        profile = CAPTURE_PROFILES[self.capture_profile]
        return {
            "backend": self.name,
            "platform": platform.system(),
            "dpi_awareness": self.dpi_awareness,
            "observation": "active-window screenshot + window list, with a deadline-bounded UI Automation layer",
            "pointer_fallback": "Win32 virtual-desktop coordinates",
            "keyboard_fallback": "explicit key chords + console virtual keys + SendInput Unicode text",
            "secure_desktop": False,
            "elevated_window_access": "subject to Windows UIPI/integrity boundaries",
            "capture_profile": self.capture_profile,
            "capture_media_type": profile["media_type"],
            "semantic_layer": (
                provider.status() if (provider := getattr(self, "semantics", None)) is not None else {}
            ),
        }

    def _encode(self, image) -> tuple[bytes, str, dict[str, object]]:
        """Encode one captured frame for upload to a grounding model.

        Downscaling happens before encoding so the cost is paid once. The frame
        geometry is untouched: model coordinates are normalized against the frame,
        never against the pixel size of the image, so a resized screenshot still
        maps back to the same screen point.
        """

        profile = CAPTURE_PROFILES[self.capture_profile]
        max_pixels = int(profile["max_pixels"] or 0)
        scale = 1.0
        if max_pixels and image.width * image.height > max_pixels:
            scale = (max_pixels / (image.width * image.height)) ** 0.5
            image = image.resize(
                (max(1, round(image.width * scale)), max(1, round(image.height * scale))),
                _resampling_filter(),
            )
        media_type = str(profile["media_type"])
        output = io.BytesIO()
        if media_type == "image/jpeg":
            image.convert("RGB").save(
                output, format="JPEG", quality=int(profile["quality"]), optimize=True, subsampling=0
            )
        else:
            image.save(output, format="PNG")
        return (
            output.getvalue(),
            media_type,
            {
                "image_width": int(image.width),
                "image_height": int(image.height),
                "downscale": round(scale, 4),
            },
        )

    def observe(self) -> ComputerObservation:
        """Observe the desktop with the default (best-effort) semantic layer."""

        return self.observe_layered()

    def observe_layered(
        self,
        *,
        semantics: str = SEMANTICS_BEST_EFFORT,
        deadline_ms: float = 0.0,
    ) -> ComputerObservation:
        """Capture the desktop in cost-ordered layers.

        The frame (geometry plus screenshot) and the top-level window list are
        cheap, bounded by Loom's own code, and are what every caller actually
        needs; they are always produced. The semantic layer is expensive, its
        latency belongs to the application being driven rather than to Loom, and
        every model-facing path treats it as advisory - so it is requested with
        a deadline and skipped rather than waited on.

        ``semantics`` selects the mode: ``skip`` never asks, ``best_effort``
        asks with a UI-latency budget, and ``required`` (the legacy grounder
        path, which cannot promote a click without a control map) waits longer
        but is still bounded.
        """

        import win32api
        import win32gui
        from PIL import ImageGrab

        with self._lock:
            observe_started = time.perf_counter()
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
            geometry_ms = round((time.perf_counter() - observe_started) * 1000.0, 3)
            capture_started = time.perf_counter()
            image = ImageGrab.grab(bbox=(left, top, right, bottom), all_screens=True)
            captured_width, captured_height = int(image.width), int(image.height)
            screenshot, media_type, encoding = self._encode(image)
            capture_ms = round((time.perf_counter() - capture_started) * 1000.0, 3)

            windows_started = time.perf_counter()
            windows = self._enumerate_windows(hwnd)
            window_enumeration_ms = round((time.perf_counter() - windows_started) * 1000.0, 3)
            active = next((item for item in windows if item.foreground), None)
            if active is None:
                active = ComputerWindow(
                    window_id=self._window_id(hwnd),
                    title=str(win32gui.GetWindowText(hwnd) or ""),
                    rect=ComputerRect(left, top, right, bottom),
                    foreground=True,
                )

            observation_id = uuid.uuid4().hex
            self_window = self._is_host_window(hwnd)
            uia_started = time.perf_counter()
            # Loom's own window is the one case where semantics are guaranteed
            # worthless: it is Loom's UI, not the task's, and its renderer is
            # busiest exactly while a turn is streaming - which is when this runs.
            layer = self._semantic_layer(
                hwnd,
                semantics=SEMANTICS_SKIP if self_window else semantics,
                deadline_ms=deadline_ms,
                skip_reason="loom_own_window" if self_window else "not_requested",
            )
            uia_enumeration_ms = round((time.perf_counter() - uia_started) * 1000.0, 3)
            if layer.mapping:
                self._control_maps[observation_id] = layer.mapping
                while len(self._control_maps) > 4:
                    self._control_maps.popitem(last=False)

            return ComputerObservation(
                observation_id=observation_id,
                frame=frame,
                image_data=screenshot,
                image_media_type=media_type,
                active_window=active,
                windows=windows,
                controls=layer.controls,
                semantics={**layer.to_dict(), "requested": str(semantics)},
                metadata={
                    "dpi_awareness": self.dpi_awareness,
                    "control_backend": "uia",
                    "self_window": self_window,
                    "timings_ms": {
                        "geometry": geometry_ms,
                        "screenshot_capture_and_encode": capture_ms,
                        "window_enumeration": window_enumeration_ms,
                        "uia_enumeration": uia_enumeration_ms,
                        "total": round((time.perf_counter() - observe_started) * 1000.0, 3),
                    },
                    "capture": {
                        "profile": self.capture_profile,
                        "media_type": media_type,
                        "image_mode": str(image.mode),
                        "captured_width": captured_width,
                        "captured_height": captured_height,
                        "encoded_bytes": len(screenshot),
                        **encoding,
                    },
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
                process_name = str(
                    observation.active_window.process_name if observation.active_window is not None else ""
                ).strip().casefold()
                if process_name in _CONSOLE_PROCESSES:
                    self._send_console_text(action.text)
                    fallback_name = "virtual-key console fallback"
                else:
                    self._send_unicode_text(action.text)
                    fallback_name = "Unicode SendInput fallback"
                return ComputerExecution(
                    ok=True,
                    message=f"text input completed through {fallback_name}",
                    action=action,
                    native=False,
                    fallback_used=True,
                )

            if action.type is ComputerActionType.CLEAR_TEXT:
                if wrapper is not None and self._native_type(wrapper, ""):
                    return ComputerExecution(
                        ok=True,
                        message="text cleared through UI Automation",
                        action=action,
                        native=True,
                    )
                if wrapper is not None:
                    if not self._native_click(wrapper, double=False, right=False):
                        point = self._wrapper_center(wrapper, observation.frame)
                        if point is not None:
                            self._click_point(observation.frame, point)
                elif action.point is not None:
                    self._click_point(observation.frame, action.point)
                import pyautogui

                try:
                    self._send_key_chord(("ctrl", "a"))
                    pyautogui.press("delete")
                finally:
                    self._release_modifiers()
                return ComputerExecution(
                    ok=True,
                    message="clear-text shortcut injected; re-observation is required",
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
        provider = getattr(self, "semantics", None)
        if provider is not None:
            provider.close()

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
            # pyautogui presses modifiers down, then the key, then releases in
            # reverse. An exception anywhere in the middle - or a chord Windows
            # swallows - leaves a modifier logically held, and from then on every
            # click the user makes is a Win-click or Alt-click instead of a
            # click. That reads as a dead mouse, so the release is unconditional.
            try:
                self._send_key_chord(action.keys)
            finally:
                self._release_modifiers()
        elif action.type is ComputerActionType.KEY:
            try:
                if len(action.keys) > 1:
                    self._send_key_chord(action.keys)
                else:
                    pyautogui.press(self._normalize_key(action.keys[0]))
            finally:
                self._release_modifiers()
        else:
            raise ValueError(f"unsupported Windows coordinate action: {action.type.value}")

        return ComputerExecution(
            ok=True,
            message=f"{action.type.value} completed",
            action=action,
            native=False,
            fallback_used=fallback,
        )

    @staticmethod
    def _normalize_key(key: str) -> str:
        normalized = str(key or "").strip().casefold()
        return _KEY_ALIASES.get(normalized, normalized)

    def _send_key_chord(self, keys: tuple[str, ...]) -> None:
        """Inject a chord with explicit down/up ordering.

        ``key`` historically accepted multiple keys but pressed each one
        independently, turning Ctrl+A into a literal ``a`` and Shift+Home into
        Home. Keep both model dialects (``key`` and ``hotkey``) correct.
        """

        import pyautogui

        normalized = tuple(self._normalize_key(key) for key in keys if str(key or "").strip())
        if not normalized:
            raise ValueError("keyboard chord requires at least one key")
        held = normalized[:-1]
        pressed: list[str] = []
        base_pressed = False
        try:
            for key in held:
                pyautogui.keyDown(key)
                pressed.append(key)
            pyautogui.keyDown(normalized[-1])
            base_pressed = True
        finally:
            try:
                if base_pressed:
                    pyautogui.keyUp(normalized[-1])
            finally:
                for key in reversed(pressed):
                    try:
                        pyautogui.keyUp(key)
                    except Exception:
                        continue

    def _send_console_text(self, text: str) -> None:
        """Use physical virtual-key events for console-hosted applications.

        Classic conhost ignores KEYEVENTF_UNICODE/VK_PACKET input even though
        GUI editors accept it. PyAutoGUI's write/press path produces ordinary
        virtual-key events, which both classic cmd and Windows Terminal accept.
        Non-ASCII characters retain the Unicode fallback rather than being
        silently corrupted.
        """

        import pyautogui

        ascii_run: list[str] = []

        def flush_ascii() -> None:
            if ascii_run:
                pyautogui.write("".join(ascii_run), interval=0)
                ascii_run.clear()

        try:
            for char in text:
                if char == "\r":
                    continue
                if char == "\n":
                    flush_ascii()
                    pyautogui.press("enter")
                elif char == "\t":
                    flush_ascii()
                    pyautogui.press("tab")
                elif 0x20 <= ord(char) <= 0x7E:
                    ascii_run.append(char)
                else:
                    flush_ascii()
                    self._send_unicode_text(char)
            flush_ascii()
        finally:
            self._release_modifiers()

    def _release_modifiers(self) -> None:
        """Force every modifier key up, whatever state the last action left.

        Releasing a key that was not held is a no-op at the Windows level, so
        this is safe to call unconditionally and cheap enough to always do.
        """

        try:
            import pyautogui
        except Exception:
            return
        for key in _MODIFIER_KEYS:
            try:
                pyautogui.keyUp(key)
            except Exception:
                continue

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
        import win32gui

        screen_point = frame.to_screen(point)
        # A screenshot is only authority over the window it captured.  A modal,
        # toast, or failed window switch can cover the same coordinates between
        # observation and execution; clicking through would act on an application
        # the model never saw.  Fail closed and make the caller observe again.
        if frame.window_id:
            expected = self._parse_window_id(frame.window_id)
            actual = int(win32gui.WindowFromPoint(screen_point) or 0)
            get_ancestor = getattr(win32gui, "GetAncestor", None)
            ga_root = int(getattr(win32con, "GA_ROOT", 2))
            if callable(get_ancestor):
                expected = int(get_ancestor(expected, ga_root) or expected)
                actual = int(get_ancestor(actual, ga_root) or actual)
            if actual and actual != expected:
                raise RuntimeError(
                    "computer target changed after the screenshot; refresh computer_observe before clicking "
                    f"(expected {self._window_id(expected)}, found {self._window_id(actual)})"
                )
        win32api.SetCursorPos(screen_point)
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

    def _dispatch_uia(self, call, *, deadline_ms: float, default=None):
        """Run one wrapper operation on the semantic worker, or give up quickly.

        Native invokes are cross-process COM calls just like enumeration, so they
        can hang for exactly the same reason and must share the same apartment
        and the same deadline. When no provider is configured (direct unit-test
        construction of the operator) the call runs inline, because the point of
        the indirection is the deadline, not the indirection itself.
        """

        provider = getattr(self, "semantics", None)
        if provider is None:
            try:
                return call()
            except Exception:
                return default
        result = provider.run(call, deadline_ms=deadline_ms)
        return result.value if result.ok else default

    def _native_click(self, wrapper, *, double: bool, right: bool) -> bool:
        if double or right:
            return False

        def invoke_once() -> bool:
            invoke = getattr(wrapper, "invoke", None)
            if callable(invoke):
                invoke()
                return True
            return False

        return bool(
            self._dispatch_uia(
                invoke_once,
                deadline_ms=NATIVE_INVOKE_DEADLINE_MS,
                default=False,
            )
        )

    def _native_type(self, wrapper, text: str) -> bool:
        def set_text_once() -> bool:
            for name in ("set_edit_text", "set_text"):
                try:
                    method = getattr(wrapper, name, None)
                    if callable(method):
                        method(text)
                        return True
                except Exception:
                    continue
            return False

        return bool(
            self._dispatch_uia(
                set_text_once,
                deadline_ms=NATIVE_INVOKE_DEADLINE_MS,
                default=False,
            )
        )

    def _wrapper_center(self, wrapper, frame: ComputerFrame) -> ComputerPoint | None:
        def measure() -> ComputerPoint:
            rect = wrapper.rectangle()
            return ComputerRect(
                int(rect.left), int(rect.top), int(rect.right), int(rect.bottom)
            ).center_in(frame)

        return self._dispatch_uia(measure, deadline_ms=NATIVE_INVOKE_DEADLINE_MS, default=None)

    def _switch_window(self, action: ComputerAction) -> ComputerExecution:
        import pywintypes
        import win32api
        import win32con
        import win32gui
        import win32process

        hwnd = self._parse_window_id(action.window_id)
        if not win32gui.IsWindow(hwnd):
            raise RuntimeError(f"Windows window no longer exists: {action.window_id}")

        # Everything below is a synchronous call into the target's message loop.
        # Refusing here costs one failed action; proceeding against a window that
        # is not pumping costs the user their mouse and keyboard.
        responds = _window_responds(hwnd)
        if responds is False:
            raise RuntimeError(
                f"Windows window {action.window_id} is not responding, so Loom did not try to activate it; "
                "activating an unresponsive window can freeze desktop input. Pick another window, or ask "
                "the user to bring this application back themselves."
            )

        was_visible = bool(win32gui.IsWindowVisible(hwnd))
        was_iconic = bool(win32gui.IsIconic(hwnd))
        if not was_visible:
            win32gui.ShowWindow(hwnd, win32con.SW_SHOW)
        if was_iconic:
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        methods: list[str] = []
        activation_error: Exception | None = None
        try:
            win32gui.SetForegroundWindow(hwnd)
            methods.append("SetForegroundWindow")
        except pywintypes.error as exc:
            activation_error = exc

        # The AttachThreadInput escalation is the one step that can take the
        # user's input down with it, so it is only attempted against a window
        # that has just proven it is pumping. "Unknown" is not good enough: if
        # the liveness probe itself could not run, Loom gives up the escalation
        # rather than gamble the desktop on it.
        if int(win32gui.GetForegroundWindow()) != hwnd and _window_responds(hwnd) is True:
            foreground_hwnd = int(win32gui.GetForegroundWindow() or 0)
            foreground_thread = (
                int(win32process.GetWindowThreadProcessId(foreground_hwnd)[0])
                if foreground_hwnd
                else 0
            )
            current_thread = int(win32api.GetCurrentThreadId())
            attached = False
            retry_allowed = bool(current_thread and foreground_thread == current_thread)
            try:
                # SetForegroundWindow runs on Loom's current worker thread. Join
                # that caller to the existing foreground input queue; attaching
                # the foreground thread to the target application's thread uses
                # the wrong participants and can return ERROR_INVALID_PARAMETER.
                if foreground_thread and foreground_thread != current_thread:
                    try:
                        win32process.AttachThreadInput(current_thread, foreground_thread, True)
                        attached = True
                        retry_allowed = True
                    except pywintypes.error as exc:
                        # Either thread can disappear between discovery and the
                        # attach. This is an ordinary activation race, not an
                        # internal tool crash; foreground verification below
                        # still fails closed.
                        activation_error = exc
                # BringWindowToTop without foreground permission can reorder the
                # pixels while GetForegroundWindow still names the old app. That
                # split-brain state produced screenshots with cmd visually above
                # Claude while keyboard authority remained with Claude. Never
                # mutate Z-order unless the input queues are joined (or already
                # belong to the same thread).
                if retry_allowed:
                    win32gui.BringWindowToTop(hwnd)
                    try:
                        win32gui.SetForegroundWindow(hwnd)
                        methods.append("AttachThreadInput")
                    except pywintypes.error as exc:
                        activation_error = exc
            finally:
                if attached:
                    try:
                        win32process.AttachThreadInput(current_thread, foreground_thread, False)
                    except pywintypes.error as exc:
                        # A thread that exits while attached can make Windows
                        # return ERROR_INVALID_PARAMETER. Preserve the failure
                        # without leaking a raw pywin32 exception to the model.
                        activation_error = exc

        if int(win32gui.GetForegroundWindow()) != hwnd:
            # Do not leave a hidden helper visible or a minimized app restored
            # after a failed attempt.  The old HWND_TOP fallback created the
            # exact invisible-overlay/click-through incident this guard fixes.
            if not was_visible:
                win32gui.ShowWindow(hwnd, win32con.SW_HIDE)
            elif was_iconic:
                win32gui.ShowWindow(hwnd, win32con.SW_MINIMIZE)
            detail = f" ({activation_error})" if activation_error else ""
            raise RuntimeError(
                f"Windows refused to activate window {action.window_id}{detail}; "
                "choose another listed window or ask the user to foreground it"
            )
        return ComputerExecution(ok=True, message=f"window switched via {methods[-1]}", action=action, native=True)

    def _is_host_window(self, hwnd: int) -> bool:
        """Whether this window is part of Loom's own user interface."""

        if not getattr(self, "host_pids", frozenset()):
            return False
        try:
            import win32process

            return int(win32process.GetWindowThreadProcessId(hwnd)[1]) in self.host_pids
        except Exception:
            return False

    def _semantic_layer(
        self,
        hwnd: int,
        *,
        semantics: str,
        deadline_ms: float,
        skip_reason: str = "not_requested",
    ) -> SemanticLayer:
        """Ask for this window's controls without letting the loop hang on them."""

        mode = str(semantics or SEMANTICS_BEST_EFFORT).strip().casefold()
        if mode == SEMANTICS_SKIP:
            return SemanticLayer(state=SemanticState.SKIPPED, reason=skip_reason)
        if mode == SEMANTICS_REQUIRED:
            budget = float(deadline_ms or DEFAULT_REQUIRED_DEADLINE_MS)
            ceiling = float(MAX_REQUIRED_DEADLINE_MS)
        else:
            mode = SEMANTICS_BEST_EFFORT
            budget = float(deadline_ms or DEFAULT_BEST_EFFORT_DEADLINE_MS)
            ceiling = float(MAX_BEST_EFFORT_DEADLINE_MS)

        window_key = self._window_id(hwnd)
        # The walk's own budget is whatever the provider decided to wait, so a
        # window that earned a wider deadline also gets to use it rather than
        # truncating itself at the default.
        effective = self.semantics.deadline_for(window_key, budget, ceiling_ms=ceiling)
        return self.semantics.collect(
            lambda: self._walk_uia_controls(hwnd, budget_ms=effective),
            deadline_ms=budget,
            ceiling_ms=ceiling,
            window_key=window_key,
        )

    def _walk_uia_controls(
        self,
        hwnd: int,
        *,
        budget_ms: float,
    ) -> tuple[tuple[ComputerControl, ...], dict[str, object], bool]:
        """Walk one window's UIA subtree, stopping when the budget runs out.

        Every property read here is a cross-process COM round trip whose cost is
        set by the target application, so the walk checks its own budget between
        controls and returns what it has. Cheap discriminators (visibility, then
        geometry) are read before the expensive string properties so a truncated
        walk still yields usable controls rather than half-filled ones.

        The returned flag reports truncation, which the caller surfaces as a
        ``partial`` semantic state rather than silently shipping a short list.
        """

        from pywinauto import Desktop

        started = time.perf_counter()

        def exhausted() -> bool:
            return (time.perf_counter() - started) * 1000.0 >= budget_ms

        controls: list[ComputerControl] = []
        mapping: dict[str, object] = {}
        try:
            window = Desktop(backend="uia").window(handle=hwnd)
            descendants = window.descendants()
        except Exception:
            return (), {}, False

        truncated = False
        for index, wrapper in enumerate(descendants):
            if len(controls) >= self.max_controls:
                truncated = index + 1 < len(descendants)
                break
            if exhausted():
                truncated = True
                break
            try:
                if not wrapper.is_visible():
                    continue
                rect = wrapper.rectangle()
                candidate = ComputerRect(int(rect.left), int(rect.top), int(rect.right), int(rect.bottom))
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
        return tuple(controls), mapping, truncated

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
