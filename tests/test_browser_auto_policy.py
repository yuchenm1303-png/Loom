from __future__ import annotations

import pytest

from app.agent_runtime import AgentRuntime
from app.agent_runtime.browser_auto_policy import BrowserAutoPolicyMixin
from app.agent_runtime.browser_backend_registry import (
    BrowserBackendRegistryMixin,
    CURRENT_BROWSER,
    DEVELOPER_CDP,
    ISOLATED_BROWSER,
    normalize_browser_backend,
)
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
        self.browser_extension_attached = False
        self.browser_cdp_attached = False
        self._browser_cdp_url = ""
        self.tools = ToolRegistry(())
        self.calls: list[tuple[str, str, bool | None, str]] = []
        self.configured_mode = "local-launch"

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
        self.configured_mode = mode
        self.browser_extension_attached = mode == "extension"
        self.browser_cdp_attached = mode == "cdp-attach"
        self._browser_cdp_url = cdp_url if self.browser_cdp_attached else ""
        return self.browser_status()

    def browser_session_connection(self, connect: str, *, cdp_url: str = ""):
        requested = str(connect or "").strip()
        if requested == "launch":
            return "isolated-factory", False, "local-launch"
        if requested == "attach":
            return "cdp-factory", True, "cdp-attach"
        if requested == "current_tab":
            return "extension-factory", True, "extension"
        if self.configured_mode == "extension":
            return "extension-factory", True, "extension"
        if self.configured_mode == "cdp-attach":
            return "cdp-factory", True, "cdp-attach"
        return "isolated-factory", False, "local-launch"

    def browser_status(self, owner_session_id=None):
        del owner_session_id
        if self.configured_mode == "extension":
            connection = "extension-bridge"
            backend = "browser-extension"
            external = True
        elif self.configured_mode == "cdp-attach":
            connection = "cdp-attach"
            backend = "browser-use"
            external = True
        else:
            connection = "local-launch"
            backend = "browser-use"
            external = False
        return {
            "enabled": True,
            "backend": backend,
            "browser_connection": connection,
            "external_browser": external,
        }


class RegistryHarness(BrowserBackendRegistryMixin, FakeBrowserBase):
    pass


def test_production_runtime_stack_contains_browser_backend_registry():
    assert BrowserBackendRegistryMixin in AgentRuntime.mro()
    assert BrowserAutoPolicyMixin is BrowserBackendRegistryMixin


def test_desktop_auto_setting_remains_a_compatibility_alias_for_current_browser(tmp_path):
    assert DEFAULT_SETTINGS["browser"]["mode"] == "auto"
    store = LoomSettingsStore(tmp_path)
    assert store.snapshot()["browser"]["mode"] == "auto"
    assert normalize_browser_backend(store.snapshot()["browser"]["mode"]) == CURRENT_BROWSER


def test_backend_aliases_normalize_to_three_canonical_routes():
    assert normalize_browser_backend("auto") == CURRENT_BROWSER
    assert normalize_browser_backend("extension") == CURRENT_BROWSER
    assert normalize_browser_backend("current-tab") == CURRENT_BROWSER
    assert normalize_browser_backend("local-launch") == ISOLATED_BROWSER
    assert normalize_browser_backend("cdp-attach") == DEVELOPER_CDP


def test_current_browser_offline_fails_fast_and_never_launches_fallback(monkeypatch):
    monkeypatch.setattr("app.agent_runtime.browser_backend_registry.browser_use_available", lambda: True)
    runtime = RegistryHarness(connected=False)

    status = runtime.browser_set_connection("auto", persist_profile=True, engine="edge")

    with pytest.raises(RuntimeError, match="will not open another browser automatically"):
        runtime.browser_session_connection("")

    assert runtime.browser_headless is False
    assert [call[0] for call in runtime.calls] == ["extension"]
    assert status["selected_browser_backend"] == CURRENT_BROWSER
    assert status["selected_backend_available"] is False
    assert status["automatic_fallback"] is False
    assert status["browser_connection"] == "current-browser-unavailable"
    assert "Current Tab Bridge" in status["connection_error"]


def test_current_browser_connected_uses_extension_and_reports_canonical_label(monkeypatch):
    monkeypatch.setattr("app.agent_runtime.browser_backend_registry.browser_use_available", lambda: True)
    runtime = RegistryHarness(connected=True)
    runtime.browser_set_connection(CURRENT_BROWSER)

    factory, external, label = runtime.browser_session_connection("")

    assert factory == "extension-factory"
    assert external is True
    assert label == CURRENT_BROWSER
    status = runtime.browser_status()
    assert status["backend"] == "browser-extension"
    assert status["browser_connection"] == "extension-bridge"
    assert status["external_browser"] is True
    assert status["selected_backend_available"] is True
    assert status["automatic_fallback"] is False


def test_isolated_browser_is_only_used_when_explicitly_selected(monkeypatch):
    monkeypatch.setattr("app.agent_runtime.browser_backend_registry.browser_use_available", lambda: True)
    runtime = RegistryHarness(connected=False)
    runtime.browser_set_connection(ISOLATED_BROWSER, engine="edge")

    factory, external, label = runtime.browser_session_connection("")

    assert [call[0] for call in runtime.calls] == ["local-launch"]
    assert (factory, external, label) == ("isolated-factory", False, "local-launch")
    status = runtime.browser_status()
    assert status["selected_browser_backend"] == ISOLATED_BROWSER
    assert status["automatic_fallback"] is False


def test_browser_backend_registry_is_model_visible_without_starting_a_session(monkeypatch):
    monkeypatch.setattr("app.agent_runtime.browser_backend_registry.browser_use_available", lambda: True)
    runtime = RegistryHarness(connected=False)
    runtime.browser_set_connection(CURRENT_BROWSER)

    tool = runtime.tools.get("browser_backends")
    assert tool is not None
    rows = runtime.browser_backend_registry()
    assert [row["id"] for row in rows] == [CURRENT_BROWSER, ISOLATED_BROWSER, DEVELOPER_CDP]
    current = rows[0]
    assert current["selected"] is True
    assert current["available"] is False


class RejectingRuntime:
    browser_model_controlled_connection = False

    def __init__(self):
        self.calls: list[str] = []

    def browser_set_private_networks(self, allowed: bool):
        return {"allow_private_networks": bool(allowed)}

    def browser_set_connection(self, mode, **kwargs):
        self.calls.append(mode)
        if mode == "auto":
            raise RuntimeError("current browser unavailable")
        return {"browser_connection": mode}


def test_production_app_server_never_hides_a_selected_backend_failure():
    service = object.__new__(BrowserPolicyLoomAppServerService)
    service.runtime = RejectingRuntime()

    error = service._apply_browser_settings(
        {
            "browser": {
                "mode": "auto",
                "cdpUrl": "",
                "preferredEngine": "edge",
                "persistSessions": True,
                "modelSelectsConnection": False,
                "allowPrivateNetworks": False,
            }
        }
    )

    assert "current browser unavailable" in error
    assert service.runtime.calls == ["auto"]


def test_browser_events_never_enter_the_full_screen_desktop_hud():
    assert BrowserPolicyLoomAppServerService._hud_tool_family("browser_click") == ""
    assert BrowserPolicyLoomAppServerService._hud_tool_family("browser_navigate") == ""
    assert BrowserPolicyLoomAppServerService._hud_point("browser_click", {"index": 19}, {}) is None
    assert BrowserPolicyLoomAppServerService._hud_tool_family("computer_action") == "computer"
