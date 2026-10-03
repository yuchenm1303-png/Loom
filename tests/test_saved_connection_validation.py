import urllib.error
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
