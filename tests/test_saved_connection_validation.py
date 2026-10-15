import urllib.error
from dataclasses import replace
from unittest.mock import Mock

import pytest

import loom_model_bridge as bridge
from app.ai.model_store import ModelConfigStore


def payload(**overrides):
    return {"name": "Personal", "adapter": "openai-compatible", "baseUrl": "https://api.example.com/v1", "model": "demo", "apiKey": "test-secret", **overrides}


def store(tmp_path):
    secrets = {}
    return ModelConfigStore(tmp_path, secret_getter=secrets.get, secret_setter=lambda alias, key: secrets.__setitem__(alias, key)), secrets


@pytest.mark.parametrize("status", [401, 403])
def test_rejected_key_is_not_saved(tmp_path, monkeypatch, status):
    config, secrets = store(tmp_path)
    monkeypatch.setattr(bridge.urllib.request, "urlopen", Mock(side_effect=urllib.error.HTTPError("https://api.example.com/v1/models", status, "rejected", {}, None)))
    with pytest.raises(ValueError, match=f"rejected the API key.*{status}"):
        bridge._save(config, payload())
    assert not config.list_models()
    assert not secrets
    assert not config.path.exists()


def test_valid_key_is_checked_before_saving(tmp_path, monkeypatch):
    config, secrets = store(tmp_path)
    def opened(request, timeout):
        assert request.full_url == "https://api.example.com/v1/models"
        assert request.get_header("Authorization") == "Bearer test-secret"
        assert timeout == 6.0
        assert not secrets
        assert not config.path.exists()
        class Response:
            def __enter__(self): return self
            def __exit__(self, *args): pass
        return Response()
    monkeypatch.setattr(bridge.urllib.request, "urlopen", opened)
    result = bridge._save(config, payload())
    assert result["name"] == "Personal"
    assert len(config.list_models()) == 1
    assert list(secrets.values()) == ["test-secret"]


@pytest.mark.parametrize("status", [404, 405])
def test_chat_only_custom_endpoints_can_still_be_saved(tmp_path, monkeypatch, status):
    config, _ = store(tmp_path)
    monkeypatch.setattr(bridge.urllib.request, "urlopen", Mock(side_effect=urllib.error.HTTPError("https://api.example.com/v1/models", status, "unsupported", {}, None)))
    bridge._save(config, payload())
    assert len(config.list_models()) == 1


def test_unreachable_endpoint_is_not_saved(tmp_path, monkeypatch):
    config, secrets = store(tmp_path)
    monkeypatch.setattr(bridge.urllib.request, "urlopen", Mock(side_effect=TimeoutError()))
    with pytest.raises(ValueError, match="could not reach"):
        bridge._save(config, payload())
    assert not config.list_models()
    assert not secrets


@pytest.mark.parametrize("key", ["Bearer test-secret", "test secret", "test\nsecret"])
def test_invalid_key_format_never_reaches_network(monkeypatch, key):
    opened = Mock()
    monkeypatch.setattr(bridge.urllib.request, "urlopen", opened)
    with pytest.raises(ValueError, match="Paste only"):
        bridge._check_connection_credentials(payload(apiKey=key))
    opened.assert_not_called()


@pytest.mark.parametrize("base", [
    "https://account.smirel.com/model/v1",
    "https://ACCOUNT.SMIREL.COM:443/model/v1/deepseek/",
    "https://account.smirel.com/model/v1/minimax",
    "https://account.smirel.com/model/v1/intern",
    "https://account.smirel.com/model/v1/opencode-go",
    "https://account.smirel.com/model/v1/managed-relay",
    "https://account.smirel.com/model/v1%2Fdeepseek",
    "https://gateway.example.com/custom/v1/deepseek",
])
def test_account_gateway_rejected_before_network_or_persistence(tmp_path, monkeypatch, base):
    monkeypatch.setenv("LOOM_MODEL_GATEWAY_BASE_URL", "https://gateway.example.com/custom/v1")
    config, secrets = store(tmp_path)
    opened = Mock()
    monkeypatch.setattr(bridge.urllib.request, "urlopen", opened)
    with pytest.raises(ValueError, match="Loom account gateway"):
        bridge._save(config, payload(baseUrl=base))
    opened.assert_not_called()
    assert not secrets and not config.path.exists()
    # Direct store callers (including the edit bridge) have the same boundary.
    args = dict(display_name="Personal", adapter="openai-compatible", model="demo", api_key="test-secret")
    with pytest.raises(ValueError, match="Loom account gateway"):
        config.save_model(base_url=base, **args)
    entry = config.save_model(base_url="https://api.example.com/v1", **args)
    before = config.path.read_bytes(), dict(secrets)
    with pytest.raises(ValueError, match="Loom account gateway"):
        config.update_model(entry.model_id, base_url=base, **{**args, "api_key": "replacement-secret"})
    assert (config.path.read_bytes(), secrets) == before


def test_gateway_detection_does_not_block_other_personal_endpoints():
    from services.loom_model_gateway.routes import reject_personal_gateway_url
    for base in ["https://api.deepseek.com", "https://account.smirel.com/other/v1",
                 "https://account.smirel.com/model/v10", "https://account.smirel.com.example.org/model/v1"]:
        reject_personal_gateway_url(base, {})


def test_legacy_saved_gateway_entry_never_materializes_personal_key(tmp_path):
    config, _ = store(tmp_path)
    entry = config.save_model(display_name="Personal", adapter="openai-compatible", model="demo",
        api_key="test-secret", base_url="https://api.deepseek.com")
    legacy = replace(entry, base_url="https://account.smirel.com/model/v1/deepseek")
    secret_getter = Mock(side_effect=AssertionError("must reject before retrieving personal key"))
    config._secret_getter = secret_getter
    with pytest.raises(ValueError, match="Loom account gateway"):
        config.secret_for(legacy)
    secret_getter.assert_not_called()
