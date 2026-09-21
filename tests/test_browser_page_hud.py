"""The page HUD must not depend on which browser Loom is driving.

The extension renders it from a content script. A browser Loom launched itself,
or attached to over CDP, has no extension in it, so the same automation ran with
no overlay at all: the user watched a browser open and then move on its own with
nothing on screen saying who was driving.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from app.agent_runtime.browser_page_hud import PageHud, hud_presentation, hud_script


class _FakeCdp:
    """Enough of a CDP session to watch what the HUD driver sends."""

    def __init__(self, *, installed: bool = False) -> None:
        self.installed = installed
        self.calls: list[tuple[str, str]] = []
        self.session_id = "cdp-session-1"
        self.cdp_client = SimpleNamespace(
            send=SimpleNamespace(
                Runtime=SimpleNamespace(
                    evaluate=self._evaluate,
                    callFunctionOn=self._call_function_on,
                ),
                Page=SimpleNamespace(
                    addScriptToEvaluateOnNewDocument=self._add_script,
                ),
            )
        )

    async def _add_script(self, params=None, session_id=None):
        self.calls.append(("addScriptToEvaluateOnNewDocument", ""))
        return {}

    async def _evaluate(self, params=None, session_id=None):
        expression = str((params or {}).get("expression") or "")
        if "__loomBrowserHudRuntimeV2" in expression and "runtime.present" in expression:
            self.calls.append(("present", ""))
            return {"result": {"value": self.installed}}
        if "runtime.hide" in expression:
            self.calls.append(("hide", ""))
            return {"result": {"value": True}}
        # Anything else is the HUD asset itself being installed.
        self.calls.append(("install", ""))
        self.installed = True
        return {"result": {"value": None}}

    async def _call_function_on(self, params=None, session_id=None):
        self.calls.append(("present_on_element", str((params or {}).get("objectId") or "")))
        return {"result": {"value": self.installed}}

    def kinds(self) -> list[str]:
        return [kind for kind, _ in self.calls]


def test_the_hud_asset_ships_with_a_standalone_entry_point():
    """Injection has nothing to drive unless the asset exposes present()."""

    script = hud_script()
    assert script
    assert "function present(payload)" in script
    assert "standalone" in script


def test_a_page_without_the_hud_gets_it_installed_and_then_driven():
    hud = PageHud()
    cdp = _FakeCdp(installed=False)

    shown = asyncio.run(hud.present(cdp, title="Click 515, 600", subtitle="Browser Use", point=(515, 600)))

    assert shown is True
    assert cdp.kinds() == ["addScriptToEvaluateOnNewDocument", "present", "install", "present"]


def test_a_page_that_already_has_the_hud_is_not_reinstalled():
    hud = PageHud()
    cdp = _FakeCdp(installed=True)

    asyncio.run(hud.present(cdp, title="Reading page", subtitle="Browser Use"))
    asyncio.run(hud.present(cdp, title="Press Enter", subtitle="Browser Use"))

    # Primed once for future documents; no reinstall, no second priming.
    assert cdp.kinds() == ["addScriptToEvaluateOnNewDocument", "present", "present"]


def test_an_element_action_draws_on_the_element_itself():
    hud = PageHud()
    cdp = _FakeCdp(installed=True)

    asyncio.run(hud.present(cdp, title="Click #3", subtitle="Browser Use", object_id="node-7"))

    assert ("present_on_element", "node-7") in cdp.calls


def test_hiding_asks_the_page_hud_to_hide():
    hud = PageHud()
    cdp = _FakeCdp(installed=True)

    asyncio.run(hud.hide(cdp))

    assert cdp.kinds() == ["hide"]


@pytest.mark.parametrize(
    "action, args",
    [
        ("type", {"index": 3, "text": "hunter2-not-for-the-screen", "clear": True}),
        ("send_text", {"text_length": 26, "text_present": True}),
    ],
)
def test_typed_text_never_reaches_the_overlay(action, args):
    """The HUD is drawn in the page the user is watching."""

    title, subtitle, _point, _click = hud_presentation(action, args)

    assert "hunter2" not in title
    assert "hunter2" not in subtitle
    assert "26 characters" in subtitle


def test_a_coordinate_action_carries_the_point_it_clicked():
    title, _subtitle, point, click = hud_presentation("click_at", {"x": 515, "y": 600, "button": "left"})

    assert point == (515.0, 600.0)
    assert "515, 600" in title
    assert click is True


def test_every_action_announces_itself_at_the_one_place_they_all_pass():
    """Per-action HUD calls are how five actions had one and the rest did not."""

    from app.agent_runtime.browser_use_backend import _HUD_SILENT_ACTIONS, BrowserUseBackend

    announced: list[tuple[str, dict]] = []
    state = object()
    stub = SimpleNamespace(
        action_timeout_seconds=60.0,
        state_revision=1,
        _log=lambda *args, **kwargs: None,
        _state_summary=lambda value, include_dom_excerpt=True: {},
        _announce_action=lambda action, args: announced.append((action, dict(args or {}))),
        _runner=SimpleNamespace(run=lambda coroutine, timeout=None: state),
    )

    result = BrowserUseBackend._run_state_action(stub, "click_at", None, args={"x": 5, "y": 7})

    assert result is state
    assert announced == [("click_at", {"x": 5, "y": 7})]
    # Only the three that cannot or must not draw: no session yet, the page is
    # going away, and the one that draws itself after the capture instead.
    assert _HUD_SILENT_ACTIONS == frozenset({"start", "close", "screenshot"})


def test_an_unknown_action_still_gets_a_readable_title():
    """A new action must show up on the HUD the day it is added, not later."""

    title, subtitle, point, click = hud_presentation("harvest_widgets", {})

    assert title == "Harvest widgets"
    assert subtitle == "Browser Use"
    assert point is None
    assert click is False
