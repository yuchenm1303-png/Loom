from __future__ import annotations

import sys
from types import SimpleNamespace

from app.agent_runtime.computer_types import ComputerAction, ComputerActionType, ComputerExecution
from app.agent_runtime.computer_windows import PyWinAutoWindowsOperator
from app.agent_runtime.computer_windows_single_loop import SingleLoopWindowsOperator


#: The desktop the screenshot covers in these fixtures.
SCREEN = (0, 0, 2560, 1600)

#: Where Windows parks a minimized window: far outside the desktop, at roughly
#: title-bar size. Its restored geometry only survives in GetWindowPlacement.
MINIMIZED_RECT = (-21333, -21333, -21175, -21307)


def _fake_window_modules(
    monkeypatch,
    *,
    handles,
    visible,
    titles,
    owners=None,
    iconic=None,
    rects=None,
    placements=None,
):
    owners = owners or {}
    iconic = iconic or set()
    rects = rects or {}
    placements = placements or {}
    shown: list[tuple[int, int]] = []
    con = SimpleNamespace(
        GW_OWNER=4,
        SW_SHOW=5,
        SW_RESTORE=9,
        SM_XVIRTUALSCREEN=76,
        SM_YVIRTUALSCREEN=77,
        SM_CXVIRTUALSCREEN=78,
        SM_CYVIRTUALSCREEN=79,
    )

    def enum_windows(callback, extra):
        for hwnd in handles:
            callback(hwnd, extra)

    def window_rect(hwnd):
        if hwnd in rects:
            return rects[hwnd]
        if hwnd in iconic:
            return MINIMIZED_RECT
        return (hwnd * 10, 20, hwnd * 10 + 640, 500)

    gui = SimpleNamespace(
        EnumWindows=enum_windows,
        IsWindow=lambda hwnd: hwnd in handles,
        IsWindowVisible=lambda hwnd: hwnd in visible,
        GetWindowText=lambda hwnd: titles.get(hwnd, ""),
        GetWindowRect=window_rect,
        GetWindow=lambda hwnd, flag: owners.get(hwnd, 0),
        IsIconic=lambda hwnd: hwnd in iconic,
        ShowWindow=lambda hwnd, mode: shown.append((hwnd, mode)),
        GetWindowPlacement=lambda hwnd: (
            0,
            2 if hwnd in iconic else 1,
            (0, 0),
            (0, 0),
            placements.get(hwnd, (100, 100, 900, 700)),
        ),
    )
    metrics = {
        con.SM_XVIRTUALSCREEN: SCREEN[0],
        con.SM_YVIRTUALSCREEN: SCREEN[1],
        con.SM_CXVIRTUALSCREEN: SCREEN[2] - SCREEN[0],
        con.SM_CYVIRTUALSCREEN: SCREEN[3] - SCREEN[1],
    }
    api = SimpleNamespace(GetSystemMetrics=lambda index: metrics[index])
    monkeypatch.setitem(sys.modules, "win32con", con)
    monkeypatch.setitem(sys.modules, "win32gui", gui)
    monkeypatch.setitem(sys.modules, "win32api", api)
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
    assert windows.index(wechat[0]) == 1  # straight after the foreground window
    assert all(window.process_name.casefold() != "explorer.exe" for window in windows)
    assert "0x28" not in ids  # owned helper is not an application restore target


#: How many background windows the single-loop runtime shows the model.
MODEL_WINDOW_LIMIT = 12


def test_a_minimized_app_outranks_windows_the_screenshot_already_shows(monkeypatch):
    """The shortlist has to carry what the screenshot cannot.

    A window minimized to the taskbar or the tray stays IsWindowVisible; the
    shell just parks it far outside the desktop. Bucketing on IsWindowVisible
    filed it with the on-screen windows, where it competed for a bounded
    shortlist against windows the model could already see and click.
    """

    busy = list(range(1, 26))
    minimized = 90
    handles = [*busy, minimized]
    titles = {hwnd: f"Window {hwnd}" for hwnd in busy}
    titles[minimized] = "Chat"
    _fake_window_modules(
        monkeypatch,
        handles=handles,
        visible=set(handles),  # a minimized window is still "visible" to Win32
        titles=titles,
        iconic={minimized},
    )
    processes = {hwnd: f"app-{hwnd}.exe" for hwnd in busy}
    processes[minimized] = "chat.exe"
    operator = object.__new__(SingleLoopWindowsOperator)
    operator.max_windows = 48
    monkeypatch.setattr(operator, "_process_name_for_window", lambda hwnd: processes.get(hwnd, ""))

    windows = operator._enumerate_windows(1)
    background = [window for window in windows if not window.foreground]
    target = next(window for window in background if window.process_name == "chat.exe")

    assert background.index(target) < MODEL_WINDOW_LIMIT
    assert target.window_id == "0x5a"


def test_shortlist_order_does_not_depend_on_the_title_alphabet(monkeypatch):
    """Codepoint order is not relevance, and it demotes every non-Latin script.

    Sorting by casefolded title put CJK, Cyrillic and Arabic titles after every
    ASCII one, so on a busy desktop those applications fell past the cutoff
    however prominent they were. Ranking is by geometry, which has no alphabet.
    """

    handles = [1, 2, 3]
    titles = {1: "Editor", 2: "微信", 3: "Zed"}
    rects = {
        1: (0, 0, 400, 400),
        2: (0, 0, 2000, 1200),  # by far the largest window on screen
        3: (0, 0, 800, 600),
    }
    _fake_window_modules(
        monkeypatch,
        handles=handles,
        visible=set(handles),
        titles=titles,
        rects=rects,
    )
    operator = object.__new__(SingleLoopWindowsOperator)
    operator.max_windows = 48
    monkeypatch.setattr(operator, "_process_name_for_window", lambda hwnd: f"app-{hwnd}.exe")

    background = [w for w in operator._enumerate_windows(1) if not w.foreground]

    assert [w.title for w in background] == ["微信", "Zed"]


def test_a_minimized_main_window_beats_a_larger_hidden_helper(monkeypatch):
    """Area alone picks a process's wrong window.

    Tray applications keep several top-level HWNDs, and the helper ones are
    routinely larger than the real main window. A window the shell lists under
    the application's own name is the application.
    """

    handles = [1, 50, 51]
    titles = {1: "Editor", 50: "TrayMessageWindow", 51: "Chat"}
    _fake_window_modules(
        monkeypatch,
        handles=handles,
        visible={1, 51},  # 50 is hidden, 51 is minimized
        titles=titles,
        iconic={51},
        rects={50: (0, 0, 1900, 1200)},  # helper has the bigger rectangle
        placements={51: (400, 200, 1300, 850)},  # real window, once restored
    )
    processes = {1: "editor.exe", 50: "chat.exe", 51: "chat.exe"}
    operator = object.__new__(SingleLoopWindowsOperator)
    operator.max_windows = 48
    monkeypatch.setattr(operator, "_process_name_for_window", lambda hwnd: processes[hwnd])

    chat = [w for w in operator._enumerate_windows(1) if w.process_name == "chat.exe"]

    assert len(chat) == 1
    assert chat[0].title == "Chat"


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
