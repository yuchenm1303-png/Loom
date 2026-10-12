from __future__ import annotations

import pytest
from loom_image_capability import ImageCapabilityStore, image_capability
from loom_model_bridge import resolve_model_spec
from services.loom_model_policy.catalog_sync import parse_models
from services.loom_model_policy.policy import PolicyStore


@pytest.mark.parametrize("metadata,expected", [
    ({}, None), ({"id": "vision-multimodal-vl"}, None),
    ({"vision": "false"}, None), ({"vision": False}, False),
    ({"input_modalities": ["text", "image"]}, True),
    ({"input_modalities": ["text"]}, False),
    ({"input_modalities": []}, None),
    ({"architecture": {"input_modalities": ["text", "image"]}}, True),
    ({"capabilities": {"vision": True}}, True),
    ({"vision": True, "input_modalities": ["text"]}, None),
])
def test_only_explicit_consistent_evidence_is_authoritative(metadata, expected):
    assert image_capability(metadata)["supported"] is expected


def test_catalog_evidence_survives_sqlite_restart_and_refresh(tmp_path):
    path = tmp_path / "policy.db"
    store = PolicyStore(path)
    rows = parse_models("intern", {"data": [{"id": "arbitrary-id", "input_modalities": ["text", "image"]}]})
    store.replace_provider_catalog("intern", rows)
    row = next(r for r in PolicyStore(path).catalog()["models"] if r["model"] == "arbitrary-id")
    assert row["image_input"]["supported"] is True
    store.replace_provider_catalog("intern", parse_models("intern", {"data": [{"id": "arbitrary-id"}]}))
    row = next(r for r in store.catalog()["models"] if r["model"] == "arbitrary-id")
    assert row["image_input"]["supported"] is None


@pytest.mark.parametrize("supported", [True, False, None])
def test_host_resolution_reuses_endpoint_scoped_evidence(tmp_path, supported):
    selection = "builtin:intern:any-model"
    spec = resolve_model_spec(selection, model="any-model", home=tmp_path)
    assert spec["vision"] is None
    cache = ImageCapabilityStore(tmp_path)
    cache.put(spec["baseUrl"], spec["model"], {"supported": supported, "source": "provider/models:input_modalities"})
    assert resolve_model_spec(selection, model="any-model", home=tmp_path)["vision"] is supported
    assert cache.get("https://other.example/v1", spec["model"]) is None
    assert cache.get(spec["baseUrl"], "other-model") is None


def test_expired_evidence_is_not_reused(tmp_path, monkeypatch):
    import time
    now = time.time()
    cache = ImageCapabilityStore(tmp_path)
    cache.put("https://example/v1", "model", {"supported": True, "source": "provider/models"})
    monkeypatch.setattr(time, "time", lambda: now + 86401)
    assert cache.get("https://example/v1", "model") is None


@pytest.mark.parametrize("supported", [True, False, None])
def test_runtime_transport_and_normalization_use_the_same_state(supported):
    from app.runtime_model_switch import build_runtime_model_platform
    from app.ai import AIMessage, ChatRequest, ImagePart, MessageRole, TextPart
    from app.ai.agent import AGENT_FAST_ROLE
    platform = build_runtime_model_platform(provider="openai-compatible", base_url="https://example.test/v1", model="arbitrary-model", api_key="test-secret", vision=supported)
    assert platform._loom_model_connection["vision"] is supported
    request = ChatRequest(messages=(AIMessage(role=MessageRole.USER, content=(TextPart("image"), ImagePart("data:image/png;base64,AA"))),))
    normalized = platform._delegate._normalize_chat_request(AGENT_FAST_ROLE.role_id, request)
    assert normalized.uses_vision is (supported is not False)
    assert request.uses_vision is True
