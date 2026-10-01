from __future__ import annotations

from pathlib import Path

import loom_ant_ling_bridge as bridge


ROOT = Path(__file__).resolve().parents[1]
MANAGER = ROOT / "desktop-react" / "electron" / "modelManager.ts"
PANEL = ROOT / "desktop-react" / "src" / "components" / "ModelPanel.tsx"


def test_registry_exposes_ant_ling_before_key_is_configured(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("LOOM_HOME", str(tmp_path))
    monkeypatch.setattr(bridge, "_api_key", lambda: "")

    registry = bridge._registry()
    profiles = registry["profiles"]

    assert [profile["model"] for profile in profiles] == list(bridge.ANT_LING_FALLBACK_MODEL_IDS)
    assert all(profile["groupId"] == "ant-ling" for profile in profiles)
    assert all(profile["configured"] is False for profile in profiles)
    assert all(profile["baseUrl"] == "https://api.ant-ling.com/v1" for profile in profiles)


def test_stored_key_stays_configured_when_model_discovery_is_unavailable(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("LOOM_HOME", str(tmp_path))
    monkeypatch.setattr(bridge, "_api_key", lambda: "sk-test")
    monkeypatch.setattr(bridge, "_fetch_models", lambda _key: ([], True))

    registry = bridge._registry()

    assert registry["profiles"]
    assert all(profile["configured"] is True for profile in registry["profiles"])
    assert [profile["model"] for profile in registry["profiles"]] == list(bridge.ANT_LING_FALLBACK_MODEL_IDS)


def test_resolve_routes_ant_ling_to_official_openai_compatible_endpoint(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("LOOM_HOME", str(tmp_path))
    monkeypatch.setattr(bridge, "_api_key", lambda: "sk-test")

    resolved = bridge._resolve(bridge.ANT_LING_SELECTION)

    assert resolved["provider"] == "openai-compatible"
    assert resolved["baseUrl"] == "https://api.ant-ling.com/v1"
    assert resolved["model"] == "Ling-3.0-flash"
    assert resolved["apiKey"] == "sk-test"


def test_custom_ant_ling_model_keeps_provider_identity() -> None:
    selection = bridge._selection_for_model("Ling-next")
    assert selection.startswith(bridge.ANT_LING_SELECTION_PREFIX)
    assert bridge._model_from_selection(selection) == "Ling-next"


def test_ant_ling_vl_model_advertises_vision(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("LOOM_HOME", str(tmp_path))
    profile = bridge._profile(
        "Ling-3.0-flash-VL",
        configured=False,
        source="fallback",
        reasoning_store=bridge.ReasoningConfigStore(tmp_path),
    )
    assert profile["vision"] is True


def test_desktop_picker_routes_ant_ling_through_dedicated_bridge() -> None:
    manager = MANAGER.read_text(encoding="utf-8")
    panel = PANEL.read_text(encoding="utf-8")

    assert 'const ANT_LING_SELECTION = "builtin:ant-ling";' in manager
    assert '"loom_ant_ling_bridge.py", "resolve"' in manager
    assert '"loom_ant_ling_bridge.py", "set-key"' in manager
    assert '"loom_ant_ling_bridge.py", "set-reasoning"' in manager
    assert 'groupId === "ant-ling"' in panel
    assert 'provider: "ant-ling"' in panel
