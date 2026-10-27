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
        write=lambda *args, **kwargs: calls.append(("write", args)),
    )
    quartz = SimpleNamespace(
        CGPreflightScreenCaptureAccess=lambda: screen,
        AXIsProcessTrusted=lambda: accessibility,
    )
    monkeypatch.setitem(sys.modules, "pyautogui", pyautogui)
    monkeypatch.setitem(sys.modules, "Quartz", quartz)
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


def test_mac_backend_refuses_control_ids_and_unicode_without_corrupting_text(monkeypatch):
    operator, calls = _operator(monkeypatch)
    observation = operator.observe()
    with pytest.raises(RuntimeError, match="control_id"):
        operator.execute(
            ComputerAction(type=ComputerActionType.CLICK, control_id="uia:42"), observation
        )
    with pytest.raises(RuntimeError, match="Unicode"):
        operator.execute(
            ComputerAction(type=ComputerActionType.TYPE, text="你好"), observation
        )
    assert calls == []


def test_mac_shortcut_uses_command_modifier(monkeypatch):
    operator, calls = _operator(monkeypatch)
    observation = operator.observe()
    operator.execute(
        ComputerAction(type=ComputerActionType.HOTKEY, keys=("ctrl", "a")), observation
    )
    assert calls == [("hotkey", ("command", "a"))]
