from __future__ import annotations

import pytest

from app.agent_runtime import AgentRuntime
from app.agent_runtime.browser_auto_policy import BrowserAutoPolicyMixin
from app.agent_runtime.browser_extension_bridge import BrowserExtensionSessionBackend
from app.agent_runtime.tools import ToolRegistry
from app.app_server_browser_policy import BrowserPolicyLoomAppServerService
from app.settings import DEFAULT_SETTINGS, LoomSettingsStore


class FakeBridge:
    def __init__(self, connected: bool = False):
        self.connected = connected

    def status(self):
        return {"connected": self.connected}


class FakeBrowserBase:
    def __init__(self, *, connected: bool = False):
        self.browser_headless = True
        self.browser_extension_bridge = FakeBridge(connected)
        self.browser_model_controlled_connection = False
        self.browser_allow_private_networks = False
        self.tools = ToolRegistry(())
        self.calls: list[tuple[str, str, bool | None, str]] = []

    def _extension_bridge_for_session(self):
        return self.browser_extension_bridge

    def browser_set_connection(
        self,
        mode: str,
        *,
        cdp_url: str = "",
        persist_profile: bool | None = None,
        engine: str = "",
    ):
        self.calls.append((mode, cdp_url, persist_profile, engine))
        return self.browser_status()

    def browser_session_connection(self, connect: str, *, cdp_url: str = ""):
        return "local-factory", False, "local-launch"

    def browser_status(self, owner_session_id=None):
        return {
            "enabled": True,
            "backend": "browser-use",
            "browser_connection": "local-launch",
            "external_browser": False,
        }


class AutoHarness(BrowserAutoPolicyMixin, FakeBrowserBase):
    pass


def test_production_runtime_stack_contains_browser_auto_policy():
    assert BrowserAutoPolicyMixin in AgentRuntime.mro()


def test_desktop_browser_default_is_auto(tmp_path):
    assert DEFAULT_SETTINGS["browser"]["mode"] == "auto"
    store = LoomSettingsStore(tmp_path)
    assert store.snapshot()["browser"]["mode"] == "auto"
    assert store.set_value("browser.mode", "auto")["browser"]["mode"] == "auto"


def test_auto_uses_visible_local_fallback_when_extension_is_offline():
    runtime = AutoHarness(connected=False)

    status = runtime.browser_set_connection(
        "auto",
        persist_profile=True,
        engine="edge",
    )
    factory, external, label = runtime.browser_session_connection("")

    assert runtime.browser_headless is False
    assert runtime.calls == [("local-launch", "", True, "edge")]
    assert (factory, external, label) == ("local-factory", False, "local-launch")
    assert status["requested_browser_connection"] == "auto"
    assert status["auto_selected_connection"] == "local-launch"
    assert status["auto_fallback"] is True
    assert "visible isolated browser" in status["auto_fallback_reason"]


def test_auto_prefers_a_really_connected_current_tab():
    runtime = AutoHarness(connected=True)
    runtime.browser_set_connection("auto")

    factory, external, label = runtime.browser_session_connection("")
    backend = factory(object())

    assert isinstance(backend, BrowserExtensionSessionBackend)
    assert backend.bridge is runtime.browser_extension_bridge
    assert external is True
    assert label == "extension"
    status = runtime.browser_status()
    assert status["backend"] == "browser-extension"
    assert status["browser_connection"] == "extension-bridge"
    assert status["external_browser"] is True
    assert status["auto_fallback"] is False


def test_explicit_current_browser_never_silently_launches_another_browser():
    runtime = AutoHarness(connected=False)
    runtime.browser_set_connection("extension")

    with pytest.raises(RuntimeError, match="Current Tab Bridge extension is not connected"):
        runtime.browser_session_connection("")

    # Selecting the strict extension route itself must not ask the base class for
    # a local launch. The only configured mode is the one the user requested.
    assert [call[0] for call in runtime.calls] == ["extension"]


class RejectingRuntime:
    browser_model_controlled_connection = False

    def __init__(self):
        self.calls: list[str] = []

    def browser_set_private_networks(self, allowed: bool):
        return {"allow_private_networks": bool(allowed)}

    def browser_set_connection(self, mode, **kwargs):
        self.calls.append(mode)
        if mode == "extension":
            raise RuntimeError("extension unavailable")
        return {"browser_connection": mode}


def test_production_app_server_does_not_hide_an_explicit_extension_failure():
    service = object.__new__(BrowserPolicyLoomAppServerService)
    service.runtime = RejectingRuntime()

    error = service._apply_browser_settings(
        {
            "browser": {
                "mode": "extension",
                "cdpUrl": "",
                "preferredEngine": "edge",
                "persistSessions": True,
                "modelSelectsConnection": False,
                "allowPrivateNetworks": False,
            }
        }
    )

    assert "extension unavailable" in error
    assert service.runtime.calls == ["extension"]


def test_browser_events_never_enter_the_full_screen_desktop_hud():
    assert BrowserPolicyLoomAppServerService._hud_tool_family("browser_click") == ""
    assert BrowserPolicyLoomAppServerService._hud_tool_family("browser_navigate") == ""
    assert BrowserPolicyLoomAppServerService._hud_point("browser_click", {"index": 19}, {}) is None
    assert BrowserPolicyLoomAppServerService._hud_tool_family("computer_action") == "computer"
