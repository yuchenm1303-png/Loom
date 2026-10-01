import sys
from types import SimpleNamespace

import pytest

from app.agent_runtime.computer_types import ComputerAction, ComputerActionType, ComputerFrame, ComputerPoint
from app.agent_runtime.computer_windows import PyWinAutoWindowsOperator
from app.agent_runtime.computer_transient import ComputerTransientInputPlatform


@pytest.mark.parametrize("duration", [0, 800, 900])
def test_drag_moves_continuously_while_held_and_releases(monkeypatch, duration):
    calls = []
    monkeypatch.setitem(sys.modules, "pyautogui", SimpleNamespace())
    monkeypatch.setitem(sys.modules, "win32api", SimpleNamespace(
        SetCursorPos=lambda point: calls.append(("move", point)),
        mouse_event=lambda flag, *args: calls.append(("button", flag))))
    monkeypatch.setitem(sys.modules, "win32con", SimpleNamespace(MOUSEEVENTF_LEFTDOWN=2, MOUSEEVENTF_LEFTUP=4))
    monkeypatch.setattr("app.agent_runtime.computer_windows.time.sleep", lambda t: None)
    operator = object.__new__(PyWinAutoWindowsOperator)
    frame = ComputerFrame("f", -100, -50, 1000, 800)
    action = ComputerAction(ComputerActionType.DRAG, point=ComputerPoint(.1, .2),
                            end_point=ComputerPoint(.8, .7), duration_ms=duration)
    operator._coordinate_action(action, frame)
    assert calls[1] == ("button", 2)
    assert len([c for c in calls[2:-1] if c[0] == "move"]) >= 10
    assert calls[-2] == ("move", frame.to_screen(action.end_point))
    assert calls[-1] == ("button", 4)


def test_drag_exception_releases_button(monkeypatch):
    flags = []
    monkeypatch.setitem(sys.modules, "pyautogui", SimpleNamespace())
    def move(point):
        if flags:
            raise RuntimeError("movement failed")
    monkeypatch.setitem(sys.modules, "win32api", SimpleNamespace(SetCursorPos=move, mouse_event=lambda f, *a: flags.append(f)))
    monkeypatch.setitem(sys.modules, "win32con", SimpleNamespace(MOUSEEVENTF_LEFTDOWN=2, MOUSEEVENTF_LEFTUP=4))
    with pytest.raises(RuntimeError):
        object.__new__(PyWinAutoWindowsOperator)._coordinate_action(
            ComputerAction(ComputerActionType.DRAG, point=ComputerPoint(.1, .2), end_point=ComputerPoint(.8, .7)),
            ComputerFrame("f", 0, 0, 100, 100))
    assert flags == [2, 4]


@pytest.mark.parametrize("before,after,matched", [
    (("id", ""), ("id", "中文_123"), True),
    (("id", "ab"), ("id", "a中文_123b"), True),
    (("id", ""), ("id", "123"), False),
    (("id", "中文_123"), ("id", "中文_123"), False),
    (("old", ""), ("new", "中文_123"), False),
    (None, None, False),
])
def test_readback_proves_content_not_injection_or_pixels(before, after, matched):
    details = PyWinAutoWindowsOperator._verify_text_readback("中文_123", before, after)
    assert details["content_verified"] is matched
    assert "中文" not in str(details)


def test_transient_handle_is_not_the_literal_input_and_cannot_be_recycled():
    platform = ComputerTransientInputPlatform(object())
    original = "CU_TEST_中文_123"
    handle = platform._stash(original)
    assert platform.consume(handle) == original
    with pytest.raises(RuntimeError):
        platform.consume(handle)
    with pytest.raises(RuntimeError, match="copied transient"):
        platform.consume(platform._stash(handle))


def test_ime_unavailable_reports_unknown_and_uses_escape(monkeypatch):
    calls = []
    monkeypatch.setitem(sys.modules, "pyautogui", SimpleNamespace(press=calls.append))
    monkeypatch.setitem(sys.modules, "win32gui", SimpleNamespace())
    monkeypatch.setattr("app.agent_runtime.computer_windows.time.sleep", lambda t: None)
    details = object.__new__(PyWinAutoWindowsOperator)._cancel_ime_composition(ComputerFrame("f", 0, 0, 100, 100))
    assert calls == ["esc"]
    assert details["after_ime_composition"] is None
    assert "escape_cancel" in details["ime_clear_steps"]


@pytest.mark.parametrize("remaining", [0, 2])
def test_imm_composition_is_cancelled_and_rechecked(monkeypatch, remaining):
    import ctypes
    calls = []
    class Fn:
        def __init__(self, call):
            self.call = call
        def __call__(self, *args):
            return self.call(*args)
    sizes = iter([4, remaining, remaining])
    imm = SimpleNamespace(
        ImmGetContext=Fn(lambda hwnd: 123),
        ImmReleaseContext=Fn(lambda *a: calls.append("release")),
        ImmGetCompositionStringW=Fn(lambda *a: next(sizes)),
        ImmNotifyIME=Fn(lambda *a: calls.append(("notify", a)) or 1))
    monkeypatch.setattr(ctypes, "WinDLL", lambda *a, **kw: imm, raising=False)
    monkeypatch.setattr("app.agent_runtime.computer_windows._focused_hwnd_for_thread", lambda tid: 2)
    monkeypatch.setitem(sys.modules, "win32gui", SimpleNamespace(GetGUIThreadInfo=lambda tid: {"hwndFocus": 2}))
    monkeypatch.setitem(sys.modules, "win32process", SimpleNamespace(GetWindowThreadProcessId=lambda h: (1, 3)))
    monkeypatch.setitem(sys.modules, "pyautogui", SimpleNamespace(press=lambda key: calls.append(key)))
    monkeypatch.setattr("app.agent_runtime.computer_windows.time.sleep", lambda t: None)
    details = object.__new__(PyWinAutoWindowsOperator)._cancel_ime_composition(
        ComputerFrame("f", 0, 0, 100, 100, window_id="0x1"))
    assert details["before_ime_composition"] is True
    assert details["after_ime_composition"] is (remaining > 0)
    assert ("notify", (123, 0x15, 4, 0)) in calls
    assert calls.count("release") == (2 if remaining else 1)


def test_focus_query_uses_typed_user32_without_optional_pywin32_export(monkeypatch):
    import ctypes
    from app.agent_runtime.computer_windows import _focused_hwnd_for_thread
    class Query:
        def __call__(self, thread_id, pointer):
            info = pointer._obj
            assert thread_id == 42
            assert info.cbSize == ctypes.sizeof(info)
            info.hwndFocus = 0x12345678
            return 1
    monkeypatch.setattr(ctypes, "WinDLL", lambda *a, **kw: SimpleNamespace(GetGUIThreadInfo=Query()), raising=False)
    monkeypatch.setitem(sys.modules, "win32gui", SimpleNamespace())
    assert _focused_hwnd_for_thread(42) == 0x12345678


def test_same_window_switch_is_verified_noop_not_changed():
    from app.agent_runtime.computer_single_loop_runtime import _classify_effect
    from app.agent_runtime.computer_types import ComputerExecution, ComputerObservation, ComputerWindow
    action = ComputerAction(ComputerActionType.SWITCH_WINDOW, window_id="0X1")
    obs = ComputerObservation("obs", ComputerFrame("f", 0, 0, 100, 100), b"png",
                              active_window=ComputerWindow("0x1", "test"))
    snapshot = SimpleNamespace(observation=obs)
    outcome = SimpleNamespace(execution=ComputerExecution(True, "already foreground", action, native=True),
                              prediction=SimpleNamespace(action=action), before=snapshot, after=snapshot,
                              verification={"target_confirmed": True})
    result = _classify_effect(outcome)
    assert result["effect"] == "noop"
    assert result["noop"] is True
    assert result["semantic_verified"] is True
    assert result["active_window_changed"] is False


def test_clear_text_refuses_selection_when_composition_persists(monkeypatch):
    import threading
    calls = []
    monkeypatch.setitem(sys.modules, "pyautogui", SimpleNamespace(press=calls.append))
    operator = object.__new__(PyWinAutoWindowsOperator)
    operator._lock = threading.RLock()
    operator._control_maps = {}
    monkeypatch.setattr(operator, "_cancel_ime_composition", lambda frame: {
        "ime_clear_steps": ["imm_cancel_failed", "escape_cancel"], "after_ime_composition": True})
    result = operator.execute(ComputerAction(ComputerActionType.CLEAR_TEXT), SimpleNamespace(
        observation_id="obs", frame=ComputerFrame("f", 0, 0, 100, 100)))
    assert result.ok is False
    assert result.details["after_ime_composition"] is True
    assert calls == []
