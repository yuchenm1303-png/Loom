from __future__ import annotations

import sys
from types import SimpleNamespace

from app.agent_runtime.computer_types import ComputerAction, ComputerActionType, ComputerExecution
from app.agent_runtime.computer_windows import PyWinAutoWindowsOperator
from app.agent_runtime.computer_windows_single_loop import SingleLoopWindowsOperator


def _fake_window_modules(monkeypatch, *, handles, visible, titles, owners=None, iconic=None):
    owners = owners or {}
    iconic = iconic or set()
    shown: list[tuple[int, int]] = []
    con = SimpleNamespace(GW_OWNER=4, SW_SHOW=5, SW_RESTORE=9)

    def enum_windows(callback, extra):
        for hwnd in handles:
            callback(hwnd, extra)

    gui = SimpleNamespace(
        EnumWindows=enum_windows,
        IsWindow=lambda hwnd: hwnd in handles,
        IsWindowVisible=lambda hwnd: hwnd in visible,
        GetWindowText=lambda hwnd: titles.get(hwnd, ""),
        GetWindowRect=lambda hwnd: (hwnd * 10, 20, hwnd * 10 + 640, 500),
        GetWindow=lambda hwnd, flag: owners.get(hwnd, 0),
        IsIconic=lambda hwnd: hwnd in iconic,
        ShowWindow=lambda hwnd, mode: shown.append((hwnd, mode)),
    )
    monkeypatch.setitem(sys.modules, "win32con", con)
    monkeypatch.setitem(sys.modules, "win32gui", gui)
    return con, gui, shown


def test_hidden_app_representative_is_promoted_into_bounded_window_shortlist(monkeypatch):
    visible_handles = set(range(1, 11))
    handles = [*range(1, 11), 20, 21, 30, 40]
    titles = {1: "Editor"}
    titles.update({hwnd: f"Visible {hwnd}" for hwnd in range(2, 11)})
    titles[20] = ""
    titles[21] = "WeChat"
    titles[30] = ""
    titles[40] = "Owned helper"
    owners = {40: 999}
    _fake_window_modules(
        monkeypatch,
        handles=handles,
        visible=visible_handles,
        titles=titles,
        owners=owners,
    )

    processes = {
        **{hwnd: f"visible-{hwnd}.exe" for hwnd in visible_handles},
        20: "WeChat.exe",
        21: "WeChat.exe",
        30: "explorer.exe",
        40: "Helper.exe",
    }
    operator = object.__new__(SingleLoopWindowsOperator)
    operator.max_windows = 48
    monkeypatch.setattr(operator, "_process_name_for_window", lambda hwnd: processes.get(hwnd, ""))

    windows = operator._enumerate_windows(1)
    ids = [window.window_id for window in windows]
    wechat = [window for window in windows if window.process_name == "WeChat.exe"]

    assert ids[0] == "0x1"
    assert len(wechat) == 1
    assert wechat[0].window_id == "0x15"  # titled representative wins over 0x14
    assert windows.index(wechat[0]) <= 7  # foreground + six visible background windows
    assert all(window.process_name.casefold() != "explorer.exe" for window in windows)
    assert "0x28" not in ids  # owned helper is not an application restore target


def test_switch_window_shows_hidden_target_before_parent_focus(monkeypatch):
    con, _gui, shown = _fake_window_modules(
        monkeypatch,
        handles=[20],
        visible=set(),
        titles={20: "WeChat"},
        iconic={20},
    )
    operator = object.__new__(SingleLoopWindowsOperator)
    parent_calls: list[str] = []

    def parent_switch(self, action):
        parent_calls.append(action.window_id)
        return ComputerExecution(ok=True, message="focused", action=action, native=True)

    monkeypatch.setattr(PyWinAutoWindowsOperator, "_switch_window", parent_switch)
    action = ComputerAction(type=ComputerActionType.SWITCH_WINDOW, window_id="0x14")

    result = operator._switch_window(action)

    assert result.ok is True
    assert shown == [(20, con.SW_SHOW), (20, con.SW_RESTORE)]
    assert parent_calls == ["0x14"]
