from __future__ import annotations

import json

import pytest

from loom_model_bridge import resolve_model_spec, _describe_model, _persist_active
from app.ai.model_store import ModelConfigStore
from app.ai.reasoning_store import ReasoningConfigStore
from app.ai.model_selection_store import ModelSelectionStore
from services.loom_model_policy.catalog import group_for_selection, model_for_selection
from services.loom_model_policy.catalog_sync import parse_models
from services.loom_model_gateway.server import GatewayConfig, GatewayError, LoomModelGateway


@pytest.mark.parametrize("model", ["intern-s2", "minimax-m3", "deepseek-v4-flash-vision", "future/model"])
def test_intern_identity_survives_description_resolution_and_persistence(tmp_path, model):
    rows = parse_models("intern", {"data": [{"id": model}]})
    selection = rows[0]["model_id"]
    assert group_for_selection(selection) == "intern"
    assert model_for_selection(selection) == model
    spec = resolve_model_spec(selection, model=model, home=tmp_path)
    assert spec["selection"] == selection
    assert spec["model"] == model
    assert spec["authMode"] == "loom-account"
    assert spec["apiKey"] == ""
    assert spec["baseUrl"] == "https://account.smirel.com/model/v1/intern"
    store = ModelConfigStore(tmp_path)
    described = _describe_model(store, ReasoningConfigStore(tmp_path), selection, model)
    assert described["selection"] == selection
    _persist_active(store, ModelSelectionStore(tmp_path), {"selection": selection})
    assert ModelSelectionStore(tmp_path).get() == selection


def test_intern_gateway_routes_server_key_and_checks_exact_provider(monkeypatch):
    gateway = LoomModelGateway(GatewayConfig(
        account_base_url="http://account/v1", ant_ling_base_url="https://ant/v1",
        ant_ling_api_key="ant-secret", model_policy_base_url="http://policy/v1",
        intern_api_key="intern-secret",
    ))
    access = {"enabled": True, "models": ["builtin:intern:minimax-m3"], "catalog": {"models": [
        {"group_id": "intern", "model": "minimax-m3", "available": True},
        {"group_id": "intern", "model": "retired", "available": False},
    ]}}
    monkeypatch.setattr(gateway, "_account_access", lambda _: access)
    assert [row["id"] for row in gateway.catalog("Bearer token", "intern")["data"]] == ["minimax-m3"]
    requests = []
    monkeypatch.setattr("urllib.request.urlopen", lambda request, **kw: requests.append(request))
    gateway.upstream_request(json.dumps({"model": "minimax-m3", "messages": []}).encode(), "Bearer loom_model_test", "intern")
    assert requests[0].full_url == "https://discovery-api.intern-ai.org.cn/v1/chat/completions"
    assert requests[0].get_header("Authorization") == "Bearer intern-secret"
    with pytest.raises(GatewayError):
        gateway.authorize_model("Bearer token", "minimax-m3", "ant-ling")
    access["models"] = []
    with pytest.raises(GatewayError) as denied:
        gateway.authorize_model("Bearer token", "minimax-m3", "intern")
    assert denied.value.status == 403
    with pytest.raises(GatewayError) as retired:
        gateway.authorize_model("Bearer token", "retired", "intern")
    assert retired.value.status == 404
