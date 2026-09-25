"""Computer Use must never take the user's own input away.

From a live run (2026-09-19): partway through a desktop task the user's mouse
stopped working entirely - clicks landed nowhere, desktop-wide, until the
machine was recovered by hand. The target application (a chat client) was in
the state the trace kept showing: a window that reported a size, refused
MoveWindow, and rendered nothing. In other words it had stopped pumping
messages.

Window activation is built from synchronous cross-process calls, and one of
them, AttachThreadInput, *merges Loom's input queue with the target's*. Attach
to an application that is not pumping, then block on SetForegroundWindow, and
the merged queue wedges: the user's own mouse and keyboard stop being processed
until the call returns, which for a hung application may be never. The detach
sits in a finally that cannot run because the call before it never returned.

These tests pin the rule that follows: prove the window is alive before doing
anything that can block on it, and never merge input queues on a maybe.
"""

from __future__ import annotations

import pytest

from app.agent_runtime.computer_single_loop_runtime import (
    _destructive_hotkey,
    _window_switch_hotkey,
)
from app.agent_runtime.computer_types import ComputerAction, ComputerActionType, ComputerFrame, ComputerPoint
from app.agent_runtime.computer_windows import PyWinAutoWindowsOperator


class _FakeWin32:
    """Enough of win32gui/win32api/win32process to drive _switch_window."""

    def __init__(self, *, foreground: int, exists: bool = True, iconic: bool = False) -> None:
        self.foreground = foreground
        self.exists = exists
        self.iconic = iconic
        self.calls: list[str] = []
        self.attached: list[bool] = []

    # win32gui
    def IsWindow(self, hwnd):
        return self.exists

    def IsIconic(self, hwnd):
        return self.iconic

    def IsWindowVisible(self, hwnd):
        return True

    def ShowWindow(self, hwnd, flag):
        self.calls.append(f"ShowWindow({flag})")
        return True

    def GetForegroundWindow(self):
        return self.foreground

    def SetForegroundWindow(self, hwnd):
        self.calls.append("SetForegroundWindow")
        return True

    def BringWindowToTop(self, hwnd):
        self.calls.append("BringWindowToTop")
        return True

    def SetWindowPos(self, *args):
        self.calls.append("SetWindowPos")
        return True

    # win32process
    def GetWindowThreadProcessId(self, hwnd):
        return ((4242 if hwnd == self.foreground else 5252), 4243)

    def AttachThreadInput(self, a, b, attach):
        self.calls.append(f"AttachThreadInput({attach})")
        self.attached.append(bool(attach))
        return True

    # win32api
    def GetCurrentThreadId(self):
        return 1111


def _operator() -> PyWinAutoWindowsOperator:
    operator = object.__new__(PyWinAutoWindowsOperator)
    operator.host_pids = frozenset()
    return operator


def _install(monkeypatch, fake: _FakeWin32, *, responds) -> None:
    import sys
    from types import SimpleNamespace

    con = SimpleNamespace(SW_HIDE=0, SW_SHOW=5, SW_MINIMIZE=6, SW_RESTORE=9, HWND_TOP=0, SWP_NOMOVE=2, SWP_NOSIZE=1, SWP_SHOWWINDOW=64)
    monkeypatch.setitem(sys.modules, "win32gui", fake)
    monkeypatch.setitem(sys.modules, "win32api", fake)
    monkeypatch.setitem(sys.modules, "win32process", fake)
    monkeypatch.setitem(sys.modules, "win32con", con)
    monkeypatch.setitem(sys.modules, "pywintypes", SimpleNamespace(error=OSError))
    monkeypatch.setattr(
        "app.agent_runtime.computer_windows._window_responds",
        lambda hwnd, timeout_ms=300: responds,
    )


def _switch(window_id="0x20abe"):
    return ComputerAction(type=ComputerActionType.SWITCH_WINDOW, window_id=window_id)


def test_a_hung_window_is_refused_before_anything_can_block_on_it(monkeypatch):
    """The whole incident in one assertion: nothing is called, so nothing wedges."""

    fake = _FakeWin32(foreground=0x1234)
    _install(monkeypatch, fake, responds=False)

    with pytest.raises(RuntimeError, match="not responding"):
        _operator()._switch_window(_switch())

    assert fake.calls == [], f"nothing may be called against a hung window: {fake.calls}"
    assert fake.attached == [], "input queues must never be merged with a hung window"


def test_input_queues_are_not_merged_when_liveness_cannot_be_determined(monkeypatch):
    """"Unknown" is not "yes".

    If the liveness probe itself fails, Loom gives up the AttachThreadInput
    escalation rather than gamble the user's desktop on it. Ordinary activation
    is still attempted, because that cannot take input down with it.
    """

    fake = _FakeWin32(foreground=0x1234)
    _install(monkeypatch, fake, responds=None)

    with pytest.raises(RuntimeError, match="refused to activate"):
        _operator()._switch_window(_switch())

    assert "SetForegroundWindow" in fake.calls
    assert fake.attached == [], "an unverified window must not have its input queue merged"


def test_a_live_window_still_gets_the_full_escalation(monkeypatch):
    """The safety rule must not cost the capability it protects."""

    fake = _FakeWin32(foreground=0x1234)
    _install(monkeypatch, fake, responds=True)

    # Foreground only agrees once AttachThreadInput has run, as on a real desktop.
    original = fake.SetForegroundWindow

    def set_foreground(hwnd):
        original(hwnd)
        if "AttachThreadInput(True)" in fake.calls:
            fake.foreground = hwnd
        return True

    fake.SetForegroundWindow = set_foreground

    execution = _operator()._switch_window(_switch())

    assert execution.ok is True
    assert fake.attached == [True, False], "attach must always be paired with a detach"


def test_detach_still_runs_when_activation_raises(monkeypatch):
    """A leaked attach is the freeze. It must survive an exception."""

    fake = _FakeWin32(foreground=0x1234)
    _install(monkeypatch, fake, responds=True)

    def explode(hwnd):
        fake.calls.append("SetForegroundWindow")
        raise OSError("access denied")

    fake.SetForegroundWindow = explode

    with pytest.raises(RuntimeError, match="refused to activate.*access denied"):
        _operator()._switch_window(_switch())

    assert fake.attached == [True, False], "the input queue must be detached even on failure"


def test_window_switch_attaches_foreground_thread_not_loom_worker(monkeypatch):
    fake = _FakeWin32(foreground=0x1234)
    attached_pairs: list[tuple[int, int, bool]] = []
    fake.AttachThreadInput = lambda a, b, attach: attached_pairs.append((a, b, bool(attach))) or True
    _install(monkeypatch, fake, responds=True)

    def set_foreground(hwnd):
        if attached_pairs and attached_pairs[-1][2]:
            fake.foreground = hwnd

    fake.SetForegroundWindow = set_foreground

    _operator()._switch_window(_switch())

    assert attached_pairs == [(4242, 5252, True), (4242, 5252, False)]


def test_attach_thread_race_is_reported_as_activation_failure(monkeypatch):
    fake = _FakeWin32(foreground=0x1234)
    _install(monkeypatch, fake, responds=True)

    def invalid_thread(_foreground_thread, _target_thread, _attach):
        raise OSError(87, "AttachThreadInput", "invalid parameter")

    fake.AttachThreadInput = invalid_thread

    with pytest.raises(RuntimeError, match="refused to activate.*AttachThreadInput"):
        _operator()._switch_window(_switch())

    assert "BringWindowToTop" not in fake.calls, "failed foreground authority must not mutate visual Z-order"


def test_detach_thread_race_does_not_escape_as_raw_pywin32_error(monkeypatch):
    fake = _FakeWin32(foreground=0x1234)
    _install(monkeypatch, fake, responds=True)

    def attach_then_target_exits(_foreground_thread, _target_thread, attach):
        if attach:
            return True
        raise OSError(87, "AttachThreadInput", "invalid parameter")

    fake.AttachThreadInput = attach_then_target_exits

    with pytest.raises(RuntimeError, match="refused to activate.*AttachThreadInput"):
        _operator()._switch_window(_switch())


def test_failed_hidden_window_activation_is_rolled_back(monkeypatch):
    fake = _FakeWin32(foreground=0x1234)
    fake.IsWindowVisible = lambda _hwnd: False
    _install(monkeypatch, fake, responds=None)

    with pytest.raises(RuntimeError, match="refused to activate"):
        _operator()._switch_window(_switch())

    assert fake.calls[0] == "ShowWindow(5)"
    assert fake.calls[-1] == "ShowWindow(0)"


def test_coordinate_click_refuses_a_window_that_covered_the_screenshot(monkeypatch):
    import sys
    from types import SimpleNamespace

    cursor_moves: list[tuple[int, int]] = []
    monkeypatch.setitem(
        sys.modules,
        "win32api",
        SimpleNamespace(SetCursorPos=cursor_moves.append, mouse_event=lambda *_args: None),
    )
    monkeypatch.setitem(
        sys.modules,
        "win32gui",
        SimpleNamespace(WindowFromPoint=lambda _point: 0x20, GetAncestor=lambda hwnd, _kind: hwnd),
    )
    monkeypatch.setitem(
        sys.modules,
        "win32con",
        SimpleNamespace(GA_ROOT=2, MOUSEEVENTF_LEFTDOWN=2, MOUSEEVENTF_LEFTUP=4),
    )
    frame = ComputerFrame(
        frame_id="frame",
        origin_x=0,
        origin_y=0,
        width=100,
        height=100,
        window_id="0x10",
    )

    with pytest.raises(RuntimeError, match="target changed after the screenshot"):
        _operator()._click_point(frame, ComputerPoint(0.5, 0.5))

    assert cursor_moves == []


def test_clear_text_is_one_atomic_shortcut_sequence(monkeypatch):
    import sys
    import threading
    from types import SimpleNamespace

    calls: list[tuple[str, ...]] = []
    monkeypatch.setitem(
        sys.modules,
        "pyautogui",
        SimpleNamespace(
            hotkey=lambda *keys: calls.append(("hotkey", *keys)),
            press=lambda key: calls.append(("press", key)),
            keyUp=lambda key: calls.append(("up", key)),
        ),
    )
    operator = object.__new__(PyWinAutoWindowsOperator)
    operator._lock = threading.RLock()
    operator._control_maps = {}
    frame = ComputerFrame(frame_id="frame", origin_x=0, origin_y=0, width=100, height=100)
    observation = SimpleNamespace(observation_id="obs", frame=frame)
    action = ComputerAction(type=ComputerActionType.CLEAR_TEXT)

    execution = operator.execute(action, observation)

    assert execution.ok is True
    assert execution.fallback_used is True
    assert calls[:2] == [("hotkey", "ctrl", "a"), ("press", "delete")]


def test_modifier_keys_are_released_even_when_the_chord_fails(monkeypatch):
    """A stuck modifier is indistinguishable from a dead mouse.

    Every later click becomes a Win-click or Alt-click, which mostly does
    nothing visible - exactly what "clicking does not work" looks like.
    """

    import sys
    from types import SimpleNamespace

    released: list[str] = []

    def hotkey(*keys):
        raise RuntimeError("injection failed midway")

    fake_pyautogui = SimpleNamespace(
        hotkey=hotkey,
        press=lambda key: None,
        keyUp=released.append,
        keyDown=lambda key: None,
    )
    monkeypatch.setitem(sys.modules, "pyautogui", fake_pyautogui)
    monkeypatch.setitem(sys.modules, "win32api", SimpleNamespace())
    monkeypatch.setitem(sys.modules, "win32con", SimpleNamespace())

    operator = _operator()
    from app.agent_runtime.computer_types import ComputerFrame

    frame = ComputerFrame(frame_id="f", origin_x=0, origin_y=0, width=100, height=100)

    with pytest.raises(RuntimeError):
        operator._coordinate_action(
            ComputerAction(type=ComputerActionType.HOTKEY, keys=("win", "d")),
            frame,
        )

    for modifier in ("alt", "ctrl", "shift", "win"):
        assert modifier in released, f"{modifier} was left held: {released}"


def test_session_wide_shortcuts_are_refused_with_a_reason():
    def hotkey(*keys):
        return ComputerAction(type=ComputerActionType.HOTKEY, keys=keys)

    assert "minimising every window" in _destructive_hotkey(hotkey("win", "d"))
    assert "minimising every window" in _destructive_hotkey(hotkey("Win", "D"))
    assert "locks the workstation" in _destructive_hotkey(hotkey("win", "l"))
    assert _destructive_hotkey(hotkey("win", "m"))

    # Application shortcuts stay available; this is targeted, not a blanket ban.
    assert _destructive_hotkey(hotkey("ctrl", "c")) == ""
    assert _destructive_hotkey(hotkey("ctrl", "v")) == ""
    assert _destructive_hotkey(hotkey("win", "up")) == ""
    assert _destructive_hotkey(hotkey("alt", "f4")) == ""

    # And the two refusal families stay distinct.
    assert _window_switch_hotkey(hotkey("win", "d")) is False
    assert _destructive_hotkey(hotkey("alt", "tab")) == ""
