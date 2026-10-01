from __future__ import annotations

from pathlib import Path

import loom_ant_ling_bridge as bridge


ROOT = Path(__file__).resolve().parents[1]
BRIDGE = ROOT / "loom_ant_ling_bridge.py"
MANAGER = ROOT / "desktop-react" / "electron" / "modelManager.ts"


def test_registry_keeps_ant_ling_visible_without_managed_relay_access(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("LOOM_HOME", str(tmp_path))
    monkeypatch.setattr(bridge, "_relay_key", lambda: "")
    monkeypatch.setattr(bridge, "_relay_base_url", lambda: "https://muxway.dev/v1")

    registry = bridge._registry()
    profiles = registry["profiles"]
    models = [profile for profile in profiles if not profile.get("setupOnly")]
    status = [profile for profile in profiles if profile.get("setupOnly")]

    assert [profile["model"] for profile in models] == list(bridge.ANT_LING_FALLBACK_MODEL_IDS)
    assert all(profile["groupId"] == "ant-ling" for profile in profiles)
    assert all(profile["configured"] is True for profile in profiles)
    assert all(profile["available"] is False for profile in models)
    assert len(status) == 1
    assert "not been granted" in status[0]["statusMessage"]
    assert registry["activeSelection"] is None


def test_registry_only_enables_ant_ling_models_advertised_by_muxway(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("LOOM_HOME", str(tmp_path))
    monkeypatch.setattr(bridge, "_relay_key", lambda: "relay-test")
    monkeypatch.setattr(
        bridge,
        "_fetch_models",
        lambda _key: ["Ling-3.0-flash", "Ling-3.0-flash-VL", "Ring-2.6-1T"],
    )
    monkeypatch.setattr(bridge, "_relay_base_url", lambda: "https://muxway.dev/v1")

    profiles = bridge._registry()["profiles"]

    assert [profile["model"] for profile in profiles] == [
        "Ling-3.0-flash",
        "Ling-3.0-flash-VL",
        "Ring-2.6-1T",
    ]
    assert all(profile["groupId"] == "ant-ling" for profile in profiles)
    assert all(profile["configured"] is True for profile in profiles)
    assert all(profile["available"] is True for profile in profiles)
    assert all(profile["managed"] is True for profile in profiles)
    assert all(profile["baseUrl"] == "https://muxway.dev/v1" for profile in profiles)


def test_resolve_uses_relay_credential_not_ant_ling_upstream_key(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("LOOM_HOME", str(tmp_path))
    monkeypatch.setattr(bridge, "_relay_key", lambda: "relay-customer-key")
    monkeypatch.setattr(bridge, "_relay_base_url", lambda: "https://muxway.dev/v1")
    monkeypatch.setattr(bridge, "_fetch_models", lambda _key: ["Ling-3.0-flash"])

    resolved = bridge._resolve(bridge.ANT_LING_SELECTION)

    assert resolved["provider"] == "openai-compatible"
    assert resolved["baseUrl"] == "https://muxway.dev/v1"
    assert resolved["model"] == "Ling-3.0-flash"
    assert resolved["apiKey"] == "relay-customer-key"
    assert resolved["configured"] is True


def test_resolve_rejects_model_not_entitled_by_muxway(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("LOOM_HOME", str(tmp_path))
    monkeypatch.setattr(bridge, "_relay_key", lambda: "relay-customer-key")
    monkeypatch.setattr(bridge, "_fetch_models", lambda _key: [])

    try:
        bridge._resolve(bridge.ANT_LING_SELECTION)
    except RuntimeError as exc:
        assert "not enabled" in str(exc)
    else:
        raise AssertionError("managed Ant Ling must not bypass Relay entitlement")


def test_custom_ant_ling_model_keeps_provider_identity() -> None:
    selection = bridge._selection_for_model("Ling-next")
    assert selection.startswith(bridge.ANT_LING_SELECTION_PREFIX)
    assert bridge._model_from_selection(selection) == "Ling-next"


def test_ant_ling_vl_is_the_only_managed_image_profile(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("LOOM_HOME", str(tmp_path))
    monkeypatch.setattr(bridge, "_relay_base_url", lambda: "https://muxway.dev/v1")
    store = bridge.ReasoningConfigStore(tmp_path)

    text_profile = bridge._profile("Ling-3.0-flash", source="provider", reasoning_store=store)
    vision_profile = bridge._profile("Ling-3.0-flash-VL", source="provider", reasoning_store=store)

    assert text_profile["vision"] is False
    assert vision_profile["vision"] is True


def test_ling_flash_keeps_native_reasoning_control_through_relay(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("LOOM_HOME", str(tmp_path))
    monkeypatch.setattr(bridge, "_relay_base_url", lambda: "https://muxway.dev/v1")

    profile = bridge._profile(
        "Ling-3.0-flash",
        source="provider",
        reasoning_store=bridge.ReasoningConfigStore(tmp_path),
    )

    assert profile["reasoning"]["kind"] == "minimax-thinking"
    assert {item["value"] for item in profile["reasoning"]["options"]} == {"disabled", "enabled"}


def test_builtin_bridge_contains_no_ant_ling_upstream_secret_path() -> None:
    source = BRIDGE.read_text(encoding="utf-8")
    manager = MANAGER.read_text(encoding="utf-8")

    assert "api.ant-ling.com" not in source
    assert "ANT_LING_API_KEY" not in source
    assert '"loom_ant_ling_bridge.py", "resolve"' in manager
    assert '"loom_ant_ling_bridge.py", "set-reasoning"' in manager
