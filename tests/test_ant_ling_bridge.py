from pathlib import Path
import loom_ant_ling_bridge as bridge
ROOT = Path(__file__).resolve().parents[1]

def test_registry_uses_loom_gateway(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("LOOM_HOME", str(tmp_path)); monkeypatch.delenv("LOOM_MODEL_GATEWAY_BASE_URL", raising=False)
    profiles = bridge._registry()["profiles"]
    assert [p["model"] for p in profiles] == list(bridge.ANT_LING_MODEL_IDS)
    assert all(p["groupId"] == "ant-ling" for p in profiles)
    assert all(p["baseUrl"] == "https://account.smirel.com/model/v1" for p in profiles)
    assert all(p["authMode"] == "loom-account" for p in profiles)

def test_resolve_never_contains_ant_ling_upstream_key(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("LOOM_HOME", str(tmp_path))
    resolved = bridge._resolve(bridge.ANT_LING_SELECTION)
    assert resolved["baseUrl"] == "https://account.smirel.com/model/v1"
    assert resolved["apiKey"] == ""
    assert resolved["authMode"] == "loom-account"

def test_vl_and_reasoning_metadata(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("LOOM_HOME", str(tmp_path)); store=bridge.ReasoningConfigStore(tmp_path)
    text=bridge._profile("Ling-3.0-flash", reasoning_store=store); vision=bridge._profile("Ling-3.0-flash-VL", reasoning_store=store)
    assert text["vision"] is False and vision["vision"] is True
    assert text["reasoning"]["kind"] == "minimax-thinking"

def test_desktop_materializes_loom_account_token_only_in_main_process() -> None:
    manager=(ROOT/'desktop-react/electron/modelManager.ts').read_text(encoding='utf-8')
    main=(ROOT/'desktop-react/electron/main.ts').read_text(encoding='utf-8')
    panel=(ROOT/'desktop-react/src/components/ModelPanel.tsx').read_text(encoding='utf-8')
    source=(ROOT/'loom_ant_ling_bridge.py').read_text(encoding='utf-8')
    assert 'api.ant-ling.com' not in source and 'ANT_LING_API_KEY' not in source
    assert 'authMode?: "loom-account" | string' in manager
    assert 'await this.account.modelCredential()' in main
    assert 'LOOM_ACCOUNT_MODEL_CREDENTIAL: accountModelCredential' in main
    target=panel.split('function providerCredentialTarget',1)[1].split('// Every saved connection',1)[0]
    assert 'groupId === "ant-ling"' not in target


def test_generic_thread_resolver_accepts_ant_ling_selection(tmp_path, monkeypatch) -> None:
    import loom_model_bridge as generic_bridge

    monkeypatch.setenv("LOOM_HOME", str(tmp_path))
    resolved = generic_bridge.resolve_model_spec(
        "builtin:ant-ling:Ling-2.6-flash",
        model="Ling-2.6-flash",
        home=tmp_path,
    )

    assert resolved["selection"] == "builtin:ant-ling:Ling-2.6-flash"
    assert resolved["provider"] == "openai-compatible"
    assert resolved["baseUrl"] == "https://account.smirel.com/model/v1"
    assert resolved["model"] == "Ling-2.6-flash"
    assert resolved["authMode"] == "loom-account"
    assert resolved["apiKey"] == ""
