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

    The legacy operator intentionally exposed only visible windows. That is a bad
    fit for a visual agent because many desktop applications keep a hidden main
    HWND while they live in the notification area. This operator keeps visible
    windows first, then a bounded set of recoverable hidden top-level windows.
    ``switch_window`` explicitly shows/restores such a target before focusing it.
    """

    name = "windows-visual-single-loop"

    def status(self) -> dict[str, object]:
        status = dict(super().status())
        status.update(
            {
                "window_discovery": "visible plus recoverable hidden top-level application windows",
                "hidden_window_activation": "ShowWindow/Restore then foreground focus",
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

    def _enumerate_windows(self, foreground_hwnd: int) -> tuple[ComputerWindow, ...]:
        import win32con
        import win32gui

        visible_items: list[ComputerWindow] = []
        hidden_items: list[ComputerWindow] = []

        def callback(hwnd: int, _extra) -> None:
            if len(visible_items) + len(hidden_items) >= self.max_windows * 3:
                return
            try:
                if not win32gui.IsWindow(hwnd):
                    return
                visible = bool(win32gui.IsWindowVisible(hwnd))
                title = str(win32gui.GetWindowText(hwnd) or "").strip()
                process_name = self._process_name_for_window(hwnd)

                if visible:
                    if not title:
                        return
                else:
                    # Owned popup/helper windows are poor restore targets; the
                    # owner is the application-level top window we want instead.
                    owner = int(win32gui.GetWindow(hwnd, win32con.GW_OWNER) or 0)
                    if owner:
                        return
                    folded = process_name.casefold()
                    if not process_name or folded in _HIDDEN_PROCESS_DENYLIST:
                        return
                    # An untitled hidden HWND is still useful when its process is
                    # identifiable (WeChat and similar tray apps do this).

                try:
                    left, top, right, bottom = map(int, win32gui.GetWindowRect(hwnd))
                    rect = (
                        ComputerRect(left, top, right, bottom)
                        if right > left and bottom > top
                        else None
                    )
                except Exception:
                    rect = None

                item = ComputerWindow(
                    window_id=self._window_id(hwnd),
                    title=title,
                    process_name=process_name,
                    rect=rect,
                    foreground=hwnd == foreground_hwnd,
                )
                (visible_items if visible else hidden_items).append(item)
            except Exception:
                return

        win32gui.EnumWindows(callback, None)
        visible_items.sort(key=lambda item: (not item.foreground, item.title.casefold()))
        hidden_items.sort(key=lambda item: (item.process_name.casefold(), item.title.casefold()))
        return tuple((*visible_items, *hidden_items)[: self.max_windows])

    def _switch_window(self, action: ComputerAction) -> ComputerExecution:
        import win32con
        import win32gui

        hwnd = self._parse_window_id(action.window_id)
        if not win32gui.IsWindow(hwnd):
            raise RuntimeError(f"Windows window no longer exists: {action.window_id}")

        # The parent focus routine restores minimized windows but historically did
        # not show a tray-hidden main HWND before calling SetForegroundWindow.
        if not win32gui.IsWindowVisible(hwnd):
            win32gui.ShowWindow(hwnd, win32con.SW_SHOW)
        if win32gui.IsIconic(hwnd):
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)

        return super()._switch_window(action)


__all__ = ["SingleLoopWindowsOperator"]
