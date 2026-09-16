"""The desktop Browser settings page reaching the production browser policy.

The stored preference is runtime behavior, not decoration. These tests also lock
Loom's current-browser-first default and the rule that an explicit external
connection failure is reported rather than hidden behind another browser.
"""

from __future__ import annotations

import json

import pytest

from app.app_server_browser_policy import BrowserPolicyLoomAppServerService
from app.app_server_reasoning import _is_browser_setting
from app.settings import SETTINGS_UPDATE_PREFIX, LoomSettingsStore


LOOPBACK_CDP = "http://127.0.0.1:9222"


def envelope(path: str, value: object) -> str:
    return SETTINGS_UPDATE_PREFIX + json.dumps({"path": path, "value": value})


class FakeBrowserRuntime:
    """Records connection changes and can be told to refuse one."""

    browser_model_controlled_connection = False
    browser_allow_external_backend_selection = False

    def __init__(self, *, refuse: str = ""):
        self.refuse = refuse
        self.calls: list[dict[str, object]] = []

    def browser_set_connection(self, mode, *, cdp_url="", persist_profile=None, engine=""):
        self.calls.append(
            {
                "mode": mode,
                "cdp_url": cdp_url,
                "persist_profile": persist_profile,
                "engine": engine,
            }
        )
        if self.refuse and mode == self.refuse:
            raise ValueError(f"cannot use {mode}")
        return {"browser_connection": mode}


def service_with(runtime, store: LoomSettingsStore) -> BrowserPolicyLoomAppServerService:
    service = object.__new__(BrowserPolicyLoomAppServerService)
    service.runtime = runtime
    service.settings_store = store
    return service


@pytest.mark.parametrize(
    ("capability", "expected"),
    [
        (envelope("browser.mode", "auto"), True),
        (envelope("browser.mode", "cdp-attach"), True),
        (envelope("browser.preferredEngine", "chrome"), True),
        (envelope("appearance.scale", "120"), False),
        ("browserUse", False),
        (SETTINGS_UPDATE_PREFIX + "not-json", False),
        (SETTINGS_UPDATE_PREFIX + json.dumps(["browser.mode"]), False),
    ],
)
def test_only_browser_preferences_trigger_a_reconnect(capability, expected):
    assert _is_browser_setting(capability) is expected


def test_stored_preferences_are_pushed_into_the_runtime(tmp_path):
    store = LoomSettingsStore(tmp_path)
    store.set_capability(envelope("browser.mode", "cdp-attach"), True)
    store.set_capability(envelope("browser.cdpUrl", LOOPBACK_CDP), True)
    store.set_capability(envelope("browser.preferredEngine", "chrome"), True)
    store.set_capability(envelope("browser.persistSessions", False), True)

    runtime = FakeBrowserRuntime()
    service = service_with(runtime, store)

    assert service._apply_browser_settings(store.snapshot()) == ""
    assert runtime.calls == [
        {
            "mode": "cdp-attach",
            "cdp_url": LOOPBACK_CDP,
            "persist_profile": False,
            "engine": "chrome",
        }
    ]


def test_default_settings_use_current_browser_first_auto_policy(tmp_path):
    runtime = FakeBrowserRuntime()
    store = LoomSettingsStore(tmp_path)
    service = service_with(runtime, store)

    service._apply_browser_settings(store.snapshot())

    assert runtime.calls[0]["mode"] == "auto"
    assert runtime.calls[0]["cdp_url"] == ""


def test_an_unusable_explicit_connection_is_reported_without_hidden_fallback(tmp_path):
    store = LoomSettingsStore(tmp_path)
    store.set_capability(envelope("browser.mode", "cdp-attach"), True)
    store.set_capability(envelope("browser.cdpUrl", LOOPBACK_CDP), True)

    runtime = FakeBrowserRuntime(refuse="cdp-attach")
    service = service_with(runtime, store)

    reason = service._apply_browser_settings(store.snapshot())

    assert "cannot use cdp-attach" in reason
    assert [call["mode"] for call in runtime.calls] == ["cdp-attach"]


def test_a_runtime_without_browser_support_is_left_alone(tmp_path):
    service = service_with(object(), LoomSettingsStore(tmp_path))
    assert service._apply_browser_settings({"browser": {"mode": "cdp-attach"}}) == ""


def test_cdp_url_can_be_cleared_when_switching_back(tmp_path):
    store = LoomSettingsStore(tmp_path)
    store.set_capability(envelope("browser.cdpUrl", LOOPBACK_CDP), True)
    assert store.snapshot()["browser"]["cdpUrl"] == LOOPBACK_CDP

    # Every other string setting rejects "", but an endpoint has to be removable
    # or a stale address outlives the switch away from cdp-attach.
    settings = store.set_capability(envelope("browser.cdpUrl", ""), True)
    assert settings["browser"]["cdpUrl"] == ""


def test_settings_store_accepts_auto_and_rejects_an_unknown_browser_mode(tmp_path):
    store = LoomSettingsStore(tmp_path)
    assert store.set_capability(envelope("browser.mode", "auto"), True)["browser"]["mode"] == "auto"
    with pytest.raises(ValueError):
        store.set_capability(envelope("browser.mode", "remote-grid"), True)


def test_model_can_always_choose_isolated_but_external_escalation_stays_opt_in(tmp_path):
    store = LoomSettingsStore(tmp_path)
    assert store.snapshot()["browser"]["modelSelectsConnection"] is False

    runtime = FakeBrowserRuntime()
    service = service_with(runtime, store)
    service._apply_browser_settings(store.snapshot())
    assert runtime.browser_model_controlled_connection is True
    assert runtime.browser_allow_external_backend_selection is False

    store.set_capability(envelope("browser.modelSelectsConnection", True), True)
    service._apply_browser_settings(store.snapshot())
    assert runtime.browser_model_controlled_connection is True
    assert runtime.browser_allow_external_backend_selection is True

    store.set_capability(envelope("browser.modelSelectsConnection", False), True)
    service._apply_browser_settings(store.snapshot())
    assert runtime.browser_model_controlled_connection is True
    assert runtime.browser_allow_external_backend_selection is False


def test_external_selection_switch_is_applied_even_when_connection_change_fails(tmp_path):
    """External browser escalation permission is independent of transport availability."""

    store = LoomSettingsStore(tmp_path)
    store.set_capability(envelope("browser.mode", "cdp-attach"), True)
    store.set_capability(envelope("browser.cdpUrl", LOOPBACK_CDP), True)
    store.set_capability(envelope("browser.modelSelectsConnection", True), True)

    runtime = FakeBrowserRuntime(refuse="cdp-attach")
    service = service_with(runtime, store)

    assert service._apply_browser_settings(store.snapshot()) != ""
    assert runtime.browser_model_controlled_connection is True
    assert runtime.browser_allow_external_backend_selection is True
    assert [call["mode"] for call in runtime.calls] == ["cdp-attach"]
