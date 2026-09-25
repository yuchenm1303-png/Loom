from __future__ import annotations

import ntpath

from .computer_types import ComputerAction, ComputerExecution, ComputerRect, ComputerWindow
from .computer_windows import PyWinAutoWindowsOperator


# Invisible shell infrastructure is not a useful activation target. Hidden
# top-level windows from ordinary user applications are retained, because tray
# applications such as chat clients commonly hide (rather than destroy) their
# main window.
_HIDDEN_PROCESS_DENYLIST = frozenset(
    {
        "applicationframehost.exe",
        "dwm.exe",
        "explorer.exe",
        "runtimebroker.exe",
        "searchhost.exe",
        "shellexperiencehost.exe",
        "startmenuexperiencehost.exe",
        "textinputhost.exe",
    }
)


class SingleLoopWindowsOperator(PyWinAutoWindowsOperator):
    """Windows operator tuned for Loom's direct visual single-loop runtime.

    The window shortlist exists to tell the model what the screenshot cannot
    show. Anything presenting pixels is already in the image and clickable there;
    what the model has no other way to learn is that an application is running
    offscreen - minimized, in the notification area, or on another virtual
    desktop - which is also the only thing ``switch_window`` is for. Those
    windows are therefore ranked ahead of ones the model can see, one
    representative per executable, with no application name hard-coded anywhere.

    ``switch_window`` shows and restores such a target before taking focus.
    """

    name = "windows-visual-single-loop"

    def status(self) -> dict[str, object]:
        status = dict(super().status())
        status.update(
            {
                "window_discovery": "onscreen windows plus recoverable offscreen top-level application windows",
                "offscreen_window_activation": "ShowWindow/Restore then foreground focus",
                "window_shortlist": "foreground, one offscreen representative per process, then onscreen windows largest first",
            }
        )
        return status

    @staticmethod
    def _process_name_for_window(hwnd: int) -> str:
        import win32api
        import win32con
        import win32process

        handle = None
        try:
            _thread_id, process_id = win32process.GetWindowThreadProcessId(hwnd)
            if not process_id:
                return ""
            access = int(getattr(win32con, "PROCESS_QUERY_INFORMATION", 0x0400)) | int(
                getattr(win32con, "PROCESS_VM_READ", 0x0010)
            )
            handle = win32api.OpenProcess(access, False, int(process_id))
            path = str(win32process.GetModuleFileNameEx(handle, 0) or "")
            return ntpath.basename(path)
        except Exception:
            return ""
        finally:
            if handle is not None:
                try:
                    handle.Close()
                except Exception:
                    pass

    @staticmethod
    def _restored_area(hwnd: int) -> int:
        """Area the window would occupy once restored.

        A minimized window's GetWindowRect is a far off-screen stub (Windows
        parks it thousands of pixels outside the desktop at a title-bar-sized
        rectangle), so ranking by it makes a real application window look like
        the least significant thing running. GetWindowPlacement still reports
        the restored rectangle.
        """

        import win32gui

        try:
            placement = win32gui.GetWindowPlacement(hwnd)
            left, top, right, bottom = map(int, placement[4])
            return max(0, right - left) * max(0, bottom - top)
        except Exception:
            return 0

    @staticmethod
    def _recoverable_score(window: ComputerWindow, *, minimized: bool, area: int) -> tuple:
        """Rank one process's offscreen windows by how likely it is the main one.

        Minimized-and-titled outranks everything else: a window the shell keeps
        in the taskbar under the application's own name is the application. A
        process's hidden helper HWNDs frequently have larger rectangles than its
        real main window, so area alone picks the wrong one.
        """

        titled = 1 if window.title else 0
        return (minimized and titled, titled, minimized, area, window.window_id)

    @staticmethod
    def _is_hidden_helper(hwnd: int, *, shown: bool, minimized: bool) -> bool:
        """Reject hidden topmost/tool HWNDs that are popups, not app entrypoints."""

        if shown or minimized:
            return False
        try:
            import win32con
            import win32gui

            exstyle = int(win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE))
            helper_bits = int(getattr(win32con, "WS_EX_TOOLWINDOW", 0x80)) | int(
                getattr(win32con, "WS_EX_TOPMOST", 0x8)
            )
            return bool(exstyle & helper_bits)
        except Exception:
            # Older Windows shims and unit fakes may not expose extended styles;
            # absence of evidence is not enough to hide a legitimate tray app.
            return False

    @staticmethod
    def _onscreen_rank(window: ComputerWindow) -> tuple:
        """Order windows the model can already see, largest first.

        Title order was the previous rule. Sorting by codepoint is not a
        relevance signal and it systematically demotes every non-Latin script:
        a CJK, Cyrillic or Arabic window title sorts after every ASCII one, so on
        a busy desktop those applications fell past the shortlist cutoff no
        matter how prominent they were on screen. Area is language-neutral.
        """

        rect = window.rect
        area = 0 if rect is None else max(0, rect.width) * max(0, rect.height)
        return (-area, window.window_id)

    def _enumerate_windows(self, foreground_hwnd: int) -> tuple[ComputerWindow, ...]:
        """Shortlist top-level windows, offscreen applications first.

        The screenshot already shows the model every window that is presenting
        pixels, and it can click those directly. The list therefore earns its
        place in the prompt by naming what the screenshot cannot show, which is
        also the only thing ``switch_window`` is for. So the partition is "can
        the model see this in the current frame", not ``IsWindowVisible``.

        Those differ in the ordinary case. A window minimized to the taskbar or
        the notification area stays ``IsWindowVisible``; the shell just parks it
        far outside the desktop. Bucketing on ``IsWindowVisible`` filed such
        windows with the on-screen ones, where they competed for a bounded
        shortlist against windows the model could already see.
        """

        import win32con
        import win32gui

        onscreen: list[ComputerWindow] = []
        # Offscreen candidates keep their placement facts for ranking.
        offscreen: list[tuple[ComputerWindow, bool, int]] = []
        bounds = self._virtual_screen_rect()

        def callback(hwnd: int, _extra) -> None:
            if len(onscreen) + len(offscreen) >= self.max_windows * 3:
                return
            try:
                if not win32gui.IsWindow(hwnd):
                    return
                shown = bool(win32gui.IsWindowVisible(hwnd))
                minimized = bool(win32gui.IsIconic(hwnd))
                title = str(win32gui.GetWindowText(hwnd) or "").strip()
                process_name = self._process_name_for_window(hwnd)

                if self._is_host_window(hwnd):
                    return

                try:
                    left, top, right, bottom = map(int, win32gui.GetWindowRect(hwnd))
                    rect = (
                        ComputerRect(left, top, right, bottom)
                        if right > left and bottom > top
                        else None
                    )
                except Exception:
                    rect = None

                # Presenting pixels means shown, not minimized, and overlapping
                # the desktop the screenshot covers.
                visible_now = (
                    shown
                    and not minimized
                    and rect is not None
                    and self._intersects(rect, bounds)
                )

                if visible_now:
                    if not title:
                        return
                    item = ComputerWindow(
                        window_id=self._window_id(hwnd),
                        title=title,
                        process_name=process_name,
                        rect=rect,
                        foreground=hwnd == foreground_hwnd,
                    )
                    onscreen.append(item)
                    return

                # Owned popup/helper windows are poor restore targets; the owner
                # is the application-level top window we want instead.
                if int(win32gui.GetWindow(hwnd, win32con.GW_OWNER) or 0):
                    return
                if self._is_hidden_helper(hwnd, shown=shown, minimized=minimized):
                    return
                folded = process_name.casefold()
                if not process_name or folded in _HIDDEN_PROCESS_DENYLIST:
                    return
                # An untitled offscreen HWND is still useful when its process is
                # identifiable; tray applications commonly expose several.
                area = self._restored_area(hwnd) if minimized else (
                    0 if rect is None else max(0, rect.width) * max(0, rect.height)
                )
                item = ComputerWindow(
                    window_id=self._window_id(hwnd),
                    title=title,
                    process_name=process_name,
                    rect=rect,
                    foreground=hwnd == foreground_hwnd,
                )
                offscreen.append((item, minimized, area))
            except Exception:
                return

        win32gui.EnumWindows(callback, None)

        # One representative per executable, so the model gets application-level
        # choices rather than a list of indistinguishable helper HWNDs.
        best: dict[str, tuple[ComputerWindow, bool, int]] = {}
        for candidate in offscreen:
            key = candidate[0].process_name.casefold()
            current = best.get(key)
            if current is None or self._recoverable_score(
                candidate[0], minimized=candidate[1], area=candidate[2]
            ) > self._recoverable_score(
                current[0], minimized=current[1], area=current[2]
            ):
                best[key] = candidate
        recoverable = [
            item
            for item, _minimized, _area in sorted(
                best.values(),
                key=lambda entry: self._recoverable_score(
                    entry[0], minimized=entry[1], area=entry[2]
                ),
                reverse=True,
            )
        ]

        onscreen.sort(key=self._onscreen_rank)
        ordered = (
            *(item for item in onscreen if item.foreground),
            *recoverable,
            *(item for item in onscreen if not item.foreground),
        )
        return tuple(ordered[: self.max_windows])

    @staticmethod
    def _virtual_screen_rect() -> tuple[int, int, int, int]:
        try:
            import win32api
            import win32con

            left = int(win32api.GetSystemMetrics(win32con.SM_XVIRTUALSCREEN))
            top = int(win32api.GetSystemMetrics(win32con.SM_YVIRTUALSCREEN))
            width = int(win32api.GetSystemMetrics(win32con.SM_CXVIRTUALSCREEN))
            height = int(win32api.GetSystemMetrics(win32con.SM_CYVIRTUALSCREEN))
            if width > 0 and height > 0:
                return (left, top, left + width, top + height)
        except Exception:
            pass
        return (-(2**30), -(2**30), 2**30, 2**30)

    @staticmethod
    def _intersects(rect: ComputerRect, bounds: tuple[int, int, int, int]) -> bool:
        left, top, right, bottom = bounds
        return (
            rect.right > left
            and rect.left < right
            and rect.bottom > top
            and rect.top < bottom
        )

    def _switch_window(self, action: ComputerAction) -> ComputerExecution:
        import win32con
        import win32gui

        hwnd = self._parse_window_id(action.window_id)
        if not win32gui.IsWindow(hwnd):
            raise RuntimeError(f"Windows window no longer exists: {action.window_id}")

        # The parent focus routine restores minimized windows but historically did
        # not show a tray-hidden main HWND before calling SetForegroundWindow.
        # Parent activation owns show/restore and rolls both back on failure.
        return super()._switch_window(action)


__all__ = ["SingleLoopWindowsOperator"]
