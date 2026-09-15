"""The desktop Computer Use settings page reaching the computer runtime.

``computer.screenshotQuality`` shipped in the settings schema and in the settings
page, and nothing in the runtime ever read it: ``grep screenshot_quality app/``
matched only the schema itself. Choosing "Fast" changed nothing, while the
capture stayed a lossless full-resolution PNG whose upload dominated every step.
"""

from __future__ import annotations

import json

import pytest

from app.app_server_reasoning import (
    ReasoningManagedLoomAppServerService,
    _is_browser_setting,
    _is_computer_setting,
)
from app.settings import SETTINGS_UPDATE_PREFIX, LoomSettingsStore


def envelope(path: str, value: object) -> str:
    return SETTINGS_UPDATE_PREFIX + json.dumps({"path": path, "value": value})


class FakeComputerRuntime:
    def __init__(self, *, refuse: str = "") -> None:
        self.refuse = refuse
        self.profiles: list[str] = []

    def computer_set_capture_profile(self, profile: str) -> str:
        if self.refuse and profile == self.refuse:
            raise ValueError(f"unknown computer capture profile: {profile!r}")
        self.profiles.append(profile)
        return profile


def service_with(runtime, store: LoomSettingsStore) -> ReasoningManagedLoomAppServerService:
    service = object.__new__(ReasoningManagedLoomAppServerService)
    service.runtime = runtime
    service.settings_store = store
    return service


@pytest.mark.parametrize(
    ("capability", "expected"),
    [
        (envelope("computer.screenshotQuality", "fast"), True),
        (envelope("computer.verifyActions", True), True),
        (envelope("browser.mode", "cdp-attach"), False),
        ("computerUse", False),
        (SETTINGS_UPDATE_PREFIX + "not-json", False),
        (SETTINGS_UPDATE_PREFIX + json.dumps(["computer.screenshotQuality"]), False),
    ],
)
def test_only_computer_preferences_trigger_a_capture_change(capability, expected):
    assert _is_computer_setting(capability) is expected


def test_browser_and_computer_envelopes_do_not_overlap():
    browser = envelope("browser.mode", "extension")
    computer = envelope("computer.screenshotQuality", "high")
    assert _is_browser_setting(browser) and not _is_computer_setting(browser)
    assert _is_computer_setting(computer) and not _is_browser_setting(computer)


def test_stored_screenshot_quality_is_pushed_into_the_runtime(tmp_path):
    store = LoomSettingsStore(tmp_path)
    store.set_capability(envelope("computer.screenshotQuality", "fast"), True)
    runtime = FakeComputerRuntime()

    assert service_with(runtime, store)._apply_computer_settings(store.snapshot()) == ""
    assert runtime.profiles == ["fast"]


def test_the_default_quality_reaches_the_runtime_too(tmp_path):
    """The schema default has to be applied, not merely stored."""

    store = LoomSettingsStore(tmp_path)
    runtime = FakeComputerRuntime()

    service_with(runtime, store)._apply_computer_settings(store.snapshot())

    assert runtime.profiles == ["balanced"]


def test_a_quality_the_runtime_rejects_is_reported_not_raised(tmp_path):
    """Startup must survive a preference the operator cannot honour."""

    store = LoomSettingsStore(tmp_path)
    store.set_capability(envelope("computer.screenshotQuality", "high"), True)
    runtime = FakeComputerRuntime(refuse="high")

    reason = service_with(runtime, store)._apply_computer_settings(store.snapshot())

    assert "high" in reason
    assert runtime.profiles == []


def test_a_runtime_without_computer_use_is_left_alone(tmp_path):
    store = LoomSettingsStore(tmp_path)

    class Bare:
        pass

    assert service_with(Bare(), store)._apply_computer_settings(store.snapshot()) == ""
