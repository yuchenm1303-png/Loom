from __future__ import annotations

import json

import pytest

from app.settings import DEFAULT_SETTINGS, SETTINGS_UPDATE_PREFIX, LoomSettingsStore


def _set(store: LoomSettingsStore, path: str, value: object) -> dict[str, object]:
    payload = json.dumps({"path": path, "value": value})
    return store.set_capability(f"{SETTINGS_UPDATE_PREFIX}{payload}", True)


def test_shortcut_defaults_are_normalized(tmp_path) -> None:
    store = LoomSettingsStore(tmp_path / "home")

    assert store.snapshot()["shortcuts"] == DEFAULT_SETTINGS["shortcuts"]
    assert store.snapshot()["shortcuts"]["newConversation"] == "Ctrl+N"
    assert store.snapshot()["shortcuts"]["stopTask"] == "Escape"


def test_shortcut_preferences_persist(tmp_path) -> None:
    store = LoomSettingsStore(tmp_path / "home")

    _set(store, "shortcuts.newConversation", "Ctrl+Shift+N")
    _set(store, "shortcuts.toggleInspector", "Alt+I")
    result = _set(store, "shortcuts.attachFiles", "Ctrl+Alt+A")

    assert result["shortcuts"]["newConversation"] == "Ctrl+Shift+N"
    assert result["shortcuts"]["toggleInspector"] == "Alt+I"
    assert result["shortcuts"]["attachFiles"] == "Ctrl+Alt+A"

    reloaded = LoomSettingsStore(tmp_path / "home").snapshot()["shortcuts"]
    assert reloaded == result["shortcuts"]


@pytest.mark.parametrize("value", ["", "   ", 123, False])
def test_invalid_shortcut_values_are_rejected(tmp_path, value: object) -> None:
    store = LoomSettingsStore(tmp_path / "home")

    with pytest.raises(ValueError):
        _set(store, "shortcuts.focusComposer", value)


def test_invalid_persisted_shortcut_falls_back_to_default(tmp_path) -> None:
    home = tmp_path / "home"
    home.mkdir(parents=True)
    (home / "settings.json").write_text(
        json.dumps({"shortcuts": {"openSettings": ""}}),
        encoding="utf-8",
    )

    assert LoomSettingsStore(home).snapshot()["shortcuts"]["openSettings"] == "Ctrl+,"
