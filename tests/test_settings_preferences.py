from __future__ import annotations

import json

import pytest

from app.settings import SETTINGS_UPDATE_PREFIX, LoomSettingsStore


def envelope(path: str, value: object) -> str:
    return SETTINGS_UPDATE_PREFIX + json.dumps({"path": path, "value": value})


def test_desktop_preferences_have_safe_defaults(tmp_path):
    store = LoomSettingsStore(tmp_path)
    settings = store.snapshot()

    assert settings["schemaVersion"] == 2
    assert settings["appearance"]["scale"] == "100"
    assert settings["appearance"]["density"] == "comfortable"
    assert settings["terminal"]["shell"] == "powershell"
    assert settings["browser"]["preferredEngine"] == "edge"
    assert settings["computer"]["verifyActions"] is True
    assert settings["privacy"]["telemetry"] is False


def test_settings_envelope_updates_typed_preference_without_touching_capabilities(tmp_path):
    store = LoomSettingsStore(tmp_path)
    before = store.snapshot()["capabilities"]

    settings = store.set_capability(envelope("appearance.scale", "120"), True)
    settings = store.set_capability(envelope("terminal.commandTimeoutSeconds", 300), True)

    assert settings["appearance"]["scale"] == "120"
    assert settings["terminal"]["commandTimeoutSeconds"] == 300
    assert settings["capabilities"] == before


def test_settings_envelope_rejects_invalid_values(tmp_path):
    store = LoomSettingsStore(tmp_path)

    with pytest.raises(ValueError):
        store.set_capability(envelope("appearance.scale", "500"), True)
    with pytest.raises(ValueError):
        store.set_capability(envelope("privacy.telemetry", "yes"), True)
    with pytest.raises(ValueError):
        store.set_capability(envelope("unknown.value", True), True)


def test_normalization_repairs_invalid_persisted_values(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(
        json.dumps(
            {
                "appearance": {"scale": "999", "density": "tiny", "codeFontSize": 40},
                "terminal": {"shell": "fish", "commandTimeoutSeconds": 2},
                "privacy": {"telemetry": "yes"},
            }
        ),
        encoding="utf-8",
    )

    settings = LoomSettingsStore(tmp_path).snapshot()

    assert settings["appearance"]["scale"] == "100"
    assert settings["appearance"]["density"] == "comfortable"
    assert settings["appearance"]["codeFontSize"] == 12
    assert settings["terminal"]["shell"] == "powershell"
    assert settings["terminal"]["commandTimeoutSeconds"] == 120
    assert settings["privacy"]["telemetry"] is False
