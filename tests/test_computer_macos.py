from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

Image = pytest.importorskip('PIL.Image')

from app.agent_runtime import computer_macos
from app.agent_runtime.computer_types import ComputerAction, ComputerActionType, ComputerPoint


def _operator(monkeypatch, *, screen=True, accessibility=True):
    calls = []
    pyautogui = SimpleNamespace(
        size=lambda: SimpleNamespace(width=100, height=50),
        screenshot=lambda: Image.new("RGB", (200, 100), "#abcdef"),
        click=lambda *args, **kwargs: calls.append(("click", args, kwargs)),
        moveTo=lambda *args: calls.append(("move", args)),
        press=lambda *args: calls.append(("press", args)),
        hotkey=lambda *args: calls.append(("hotkey", args)),
        keyDown=lambda key: calls.append(("down", key)),
        keyUp=lambda key: calls.append(("up", key)),
        write=lambda *args, **kwargs: calls.append(("write", args)),
    )
    quartz = SimpleNamespace(
        CGPreflightScreenCaptureAccess=lambda: screen,
        AXIsProcessTrusted=lambda: accessibility,
    )
    monkeypatch.setitem(sys.modules, "pyautogui", pyautogui)
    monkeypatch.setitem(sys.modules, "Quartz", quartz)
    monkeypatch.setitem(sys.modules, "ApplicationServices", SimpleNamespace(AXIsProcessTrusted=lambda: accessibility))
    monkeypatch.setattr(computer_macos, "macos_computer_available", lambda: True)
    return computer_macos.MacOSComputerOperator(), calls


def test_primary_display_retina_coordinates_match_snapshot(monkeypatch):
    operator, calls = _operator(monkeypatch)
    observation = operator.observe()
    assert observation.frame.width == 100
    assert observation.frame.height == 50
    assert observation.image_media_type == "image/jpeg"
    assert not observation.controls
    action = ComputerAction(type=ComputerActionType.CLICK, point=ComputerPoint(0.5, 0.5))
    outcome = operator.execute(action, observation)
    assert outcome.ok
    assert calls == [("click", (50, 24), {"clicks": 1, "interval": 0.1, "button": "left"})]


def test_mac_screen_capture_requires_consent(monkeypatch):
    operator, calls = _operator(monkeypatch, screen=False)
    with pytest.raises(PermissionError, match="Screen Recording"):
        operator.observe()
    assert calls == []


def test_mac_input_requires_accessibility_consent(monkeypatch):
    operator, calls = _operator(monkeypatch, accessibility=False)
    observation = operator.observe()
    with pytest.raises(PermissionError, match="Accessibility"):
        operator.execute(ComputerAction(type=ComputerActionType.KEY, keys=("a",)), observation)
    assert calls == []


def test_mac_backend_rejects_untrusted_control_ids(monkeypatch):
    operator, calls = _operator(monkeypatch)
    observation = operator.observe()
    with pytest.raises(RuntimeError, match="control_id"):
        operator.execute(
            ComputerAction(type=ComputerActionType.CLICK, control_id="uia:42"), observation
        )
    assert calls == []


def test_utf16_chunks_do_not_split_surrogates():
    assert list(computer_macos._unicode_units("你好😀")) == [(20320, 22909, 55357, 56832)]
    chunks = list(computer_macos._unicode_units("a" * 19 + "😀x"))
    assert chunks == [tuple([97] * 19), (55357, 56832, 120)]
    assert "".join(
        bytes().join(int(unit).to_bytes(2, "little") for unit in part).decode("utf-16-le")
        for part in chunks
    ) == "a" * 19 + "😀x"


def test_mac_unicode_injected_as_quartz_key_events_without_clipboard(monkeypatch):
    operator, calls = _operator(monkeypatch)
    events = []
    def create_event(_source, keycode, down):
        assert keycode == 0
        return {"down": down}
    def set_unicode(event, length, units):
        assert length == len(units)
        event["units"] = tuple(units)
    def post(_tap, event):
        events.append((event["down"], event["units"]))
    quartz = sys.modules["Quartz"]
    quartz.CGEventCreateKeyboardEvent = create_event
    quartz.CGEventKeyboardSetUnicodeString = set_unicode
    quartz.CGEventPost = post
    quartz.kCGHIDEventTap = 9
    monkeypatch.setattr(computer_macos.time, "sleep", lambda _: None)
    observation = operator.observe()
    outcome = operator.execute(
        ComputerAction(type=ComputerActionType.TYPE, text="你好😀"), observation
    )
    assert outcome.ok
    assert events == [
        (True, tuple(chr(code) for code in (20320, 22909, 55357, 56832))),
        (False, tuple(chr(code) for code in (20320, 22909, 55357, 56832))),
    ]
    assert outcome.details["input_backend"] == "quartz-unicode"
    assert outcome.details["typed_text_length"] == 3
    assert calls == []


def test_mac_application_switch_requires_observed_window(monkeypatch):
    operator, calls = _operator(monkeypatch)
    activations = []
    app = SimpleNamespace(
        isTerminated=lambda: False,
        activateWithOptions_=lambda _options: activations.append(True) or True,
    )
    appkit = SimpleNamespace(
        NSRunningApplication=SimpleNamespace(
            runningApplicationWithProcessIdentifier_=lambda pid: app if pid == 42 else None
        ),
        NSApplicationActivateIgnoringOtherApps=1,
    )
    monkeypatch.setitem(sys.modules, "AppKit", appkit)
    observation = operator.observe()
    action = ComputerAction(type=ComputerActionType.SWITCH_WINDOW, window_id="macpid:42")
    with pytest.raises(RuntimeError, match="stale"):
        operator.execute(action, observation)
    from dataclasses import replace
    from app.agent_runtime.computer_types import ComputerWindow
    observed = replace(observation, windows=(ComputerWindow("macpid:42", "Example"),))
    outcome = operator.execute(action, observed)
    assert outcome.ok
    assert activations == [True]
    assert calls == []


def test_mac_stale_foreground_blocks_click(monkeypatch):
    operator, calls = _operator(monkeypatch)
    front = SimpleNamespace(processIdentifier=lambda: 900)
    appkit = SimpleNamespace(
        NSWorkspace=SimpleNamespace(sharedWorkspace=lambda: SimpleNamespace(
            frontmostApplication=lambda: front,
        )),
    )
    monkeypatch.setitem(sys.modules, "AppKit", appkit)
    from dataclasses import replace
    operator_observation = operator.observe()
    frame = replace(operator_observation.frame, window_id="macpid:123")
    observation = replace(operator_observation, frame=frame)
    action = ComputerAction(type=ComputerActionType.CLICK, point=ComputerPoint(0.5, 0.5))
    with pytest.raises(RuntimeError, match="foreground changed"):
        operator.execute(action, observation)
    assert calls == []


def test_mac_shortcut_uses_command_modifier(monkeypatch):
    operator, calls = _operator(monkeypatch)
    observation = operator.observe()
    operator.execute(
        ComputerAction(type=ComputerActionType.HOTKEY, keys=("ctrl", "a")), observation
    )
    assert calls == [
        ("down", "command"), ("down", "a"), ("up", "a"), ("up", "command")
    ]


def test_mac_refuses_screen_capture_mismatched_to_logical_display(monkeypatch):
    operator, calls = _operator(monkeypatch)
    pyautogui = sys.modules["pyautogui"]
    # Two screens or a wrongly cropped image can produce a mismatched aspect.
    pyautogui.screenshot = lambda: Image.new("RGB", (300, 100))
    with pytest.raises(RuntimeError, match="does not match the primary display"):
        operator.observe()
    assert calls == []


def test_mac_refuses_action_from_stale_observation(monkeypatch):
    from dataclasses import replace

    operator, calls = _operator(monkeypatch)
    observation = operator.observe()
    metadata = dict(observation.metadata)
    metadata["captured_at_monotonic"] -= computer_macos.MAX_OBSERVATION_AGE_SECONDS + 1
    expired = replace(observation, metadata=metadata)
    action = ComputerAction(type=ComputerActionType.CLICK, point=ComputerPoint(0.5, 0.5))
    with pytest.raises(RuntimeError, match="screenshot is stale"):
        operator.execute(action, expired)
    assert calls == []


def test_mac_multiple_keys_use_one_chord(monkeypatch):
    operator, calls = _operator(monkeypatch)
    operator.execute(
        ComputerAction(type=ComputerActionType.KEY, keys=("ctrl", "shift", "a")),
        operator.observe(),
    )
    assert calls == [
        ("down", "command"), ("down", "shift"), ("down", "a"),
        ("up", "a"), ("up", "shift"), ("up", "command")
    ]


def test_mac_releases_held_shortcut_modifiers_when_key_fails(monkeypatch):
    operator, calls = _operator(monkeypatch)
    pyautogui = sys.modules["pyautogui"]

    def key_down(key):
        calls.append(("down", key))
        if key == "a":
            raise RuntimeError("keyboard unavailable")

    pyautogui.keyDown = key_down
    with pytest.raises(RuntimeError, match="keyboard unavailable"):
        operator.execute(
            ComputerAction(type=ComputerActionType.HOTKEY, keys=("ctrl", "a")),
            operator.observe(),
        )
    assert calls == [
        ("down", "command"), ("down", "a"), ("up", "a"), ("up", "command")
    ]


def test_mac_screen_scale_allows_retina_and_nonretina():
    assert computer_macos._screen_scale(100, 50, 100, 50) == 1
    assert computer_macos._screen_scale(100, 50, 200, 100) == 2
