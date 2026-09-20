"""Typed text must name the physical key it came from.

A VNC console in the browser reads the keyboard instead of a field: noVNC
resolves KeyboardEvent.code, falls back to keyCode, and only then drops into a
virtual-keyboard path that tracks nothing. browser_press named the key and
browser_send_text did not, so on a real console single presses arrived and typed
commands vanished while the tool still reported success.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from app.agent_runtime.browser_backend import BrowserUseSessionBackend
from app.agent_runtime.browser_keys import character_key


class _Input:
    def __init__(self) -> None:
        self.events: list[dict] = []

    async def dispatchKeyEvent(self, params=None, session_id=None) -> None:  # noqa: N802 - CDP name
        self.events.append(dict(params or {}))


def _send(text: str) -> list[dict]:
    recorder = _Input()
    cdp = SimpleNamespace(
        session_id="cdp-1",
        cdp_client=SimpleNamespace(send=SimpleNamespace(Input=recorder)),
    )

    async def get_or_create_cdp_session():
        return cdp

    async def ensure_session():
        return SimpleNamespace(get_or_create_cdp_session=get_or_create_cdp_session)

    async def state():
        return None

    backend = object.__new__(BrowserUseSessionBackend)
    backend._ensure_session = ensure_session
    backend._state_async = state
    asyncio.run(backend._send_text_async(text))
    return recorder.events


def test_typed_characters_carry_their_physical_key_and_virtual_key_code():
    downs = [event for event in _send("wg show") if event["type"] == "keyDown"]
    assert [(event["key"], event.get("code"), event.get("windowsVirtualKeyCode")) for event in downs] == [
        ("w", "KeyW", 87),
        ("g", "KeyG", 71),
        (" ", "Space", 32),
        ("s", "KeyS", 83),
        ("h", "KeyH", 72),
        ("o", "KeyO", 79),
        ("w", "KeyW", 87),
    ]
    assert all(event.get("text") == event["key"] for event in downs)


def test_shifted_characters_hold_shift_around_the_key_they_share():
    events = _send("A!")
    assert [(event["type"], event["key"], event.get("code")) for event in events] == [
        ("keyDown", "Shift", "ShiftLeft"),
        ("keyDown", "A", "KeyA"),
        ("keyUp", "A", "KeyA"),
        ("keyUp", "Shift", "ShiftLeft"),
        ("keyDown", "Shift", "ShiftLeft"),
        ("keyDown", "!", "Digit1"),
        ("keyUp", "!", "Digit1"),
        ("keyUp", "Shift", "ShiftLeft"),
    ]
    shifted = [event for event in events if event["key"] in {"A", "!"}]
    assert all(event["modifiers"] == 8 for event in shifted)


def test_named_keys_stop_reporting_key_code_zero():
    downs = [event for event in _send("a\n\tb\b") if event["type"] == "keyDown"]
    named = {event["key"]: event.get("windowsVirtualKeyCode") for event in downs}
    assert named["Enter"] == 13
    assert named["Tab"] == 9
    assert named["Backspace"] == 8


def test_characters_with_no_us_key_keep_the_text_only_event():
    """CJK and emoji have no physical key to name, and inventing one is worse."""

    assert character_key("中") == ("", 0, False)
    downs = [event for event in _send("中") if event["type"] == "keyDown"]
    assert len(downs) == 1
    assert "code" not in downs[0]
    assert downs[0]["text"] == "中"
