from __future__ import annotations

import json

import pytest

from app.settings import DEFAULT_SETTINGS, SETTINGS_UPDATE_PREFIX, LoomSettingsStore


def _set(store: LoomSettingsStore, path: str, value: object) -> dict[str, object]:
    payload = json.dumps({"path": path, "value": value})
    return store.set_capability(f"{SETTINGS_UPDATE_PREFIX}{payload}", True)


def test_expanded_appearance_defaults_are_normalized(tmp_path) -> None:
    store = LoomSettingsStore(tmp_path / "home")

    appearance = store.snapshot()["appearance"]

    assert appearance == DEFAULT_SETTINGS["appearance"]
    assert appearance["conversationWidth"] == "balanced"
    assert appearance["sidebarWidth"] == "standard"
    assert appearance["inspectorWidth"] == "standard"
    assert appearance["chatFontSize"] == 13
    assert appearance["messageLineHeight"] == "comfortable"
    assert appearance["ambientEffects"] is True
    assert appearance["codeLineHeight"] == "comfortable"
    assert appearance["codeWrap"] is False


def test_expanded_appearance_preferences_persist(tmp_path) -> None:
    store = LoomSettingsStore(tmp_path / "home")

    _set(store, "appearance.scale", "130")
    _set(store, "appearance.density", "spacious")
    _set(store, "appearance.conversationWidth", "wide")
    _set(store, "appearance.sidebarWidth", "compact")
    _set(store, "appearance.inspectorWidth", "wide")
    _set(store, "appearance.chatFontSize", 16)
    _set(store, "appearance.messageLineHeight", "relaxed")
    _set(store, "appearance.ambientEffects", False)
    _set(store, "appearance.codeLineHeight", "compact")
    result = _set(store, "appearance.codeWrap", True)

    appearance = result["appearance"]
    assert appearance["scale"] == "130"
    assert appearance["density"] == "spacious"
    assert appearance["conversationWidth"] == "wide"
    assert appearance["sidebarWidth"] == "compact"
    assert appearance["inspectorWidth"] == "wide"
    assert appearance["chatFontSize"] == 16
    assert appearance["messageLineHeight"] == "relaxed"
    assert appearance["ambientEffects"] is False
    assert appearance["codeLineHeight"] == "compact"
    assert appearance["codeWrap"] is True

    reloaded = LoomSettingsStore(tmp_path / "home").snapshot()["appearance"]
    assert reloaded == appearance


@pytest.mark.parametrize(
    ("path", "value"),
    [
        ("appearance.scale", "140"),
        ("appearance.density", "dense"),
        ("appearance.conversationWidth", "full"),
        ("appearance.sidebarWidth", "huge"),
        ("appearance.chatFontSize", 20),
        ("appearance.messageLineHeight", "double"),
        ("appearance.ambientEffects", "yes"),
        ("appearance.codeLineHeight", "double"),
        ("appearance.codeWrap", "yes"),
    ],
)
def test_invalid_appearance_values_are_rejected(tmp_path, path: str, value: object) -> None:
    store = LoomSettingsStore(tmp_path / "home")

    with pytest.raises(ValueError):
        _set(store, path, value)
