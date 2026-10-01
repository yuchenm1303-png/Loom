from __future__ import annotations

from http import HTTPStatus

import pytest

from services.loom_model_gateway.server import ANT_LING_MODELS, GatewayConfig, GatewayError, LoomModelGateway


def _gateway() -> LoomModelGateway:
    return LoomModelGateway(GatewayConfig(
        account_base_url="http://account/v1",
        ant_ling_base_url="https://api.ant-ling.com/v1",
        ant_ling_api_key="server-only-secret",
    ))


def test_catalog_only_returns_models_allowed_by_loom_account(monkeypatch) -> None:
    gateway = _gateway()
    monkeypatch.setattr(gateway, "_account_access", lambda _auth: {
        "enabled": True,
        "models": ["Ling-3.0-flash", "Ling-3.0-flash-VL"],
    })
    catalog = gateway.catalog("Bearer loom_access_test")
    assert [item["id"] for item in catalog["data"]] == ["Ling-3.0-flash", "Ling-3.0-flash-VL"]
    assert all(item["owned_by"] == "loom/ant-ling" for item in catalog["data"])


def test_disabled_account_has_empty_catalog(monkeypatch) -> None:
    gateway = _gateway()
    monkeypatch.setattr(gateway, "_account_access", lambda _auth: {"enabled": False, "models": list(ANT_LING_MODELS)})
    assert gateway.catalog("Bearer token")["data"] == []


def test_gateway_rejects_model_not_entitled(monkeypatch) -> None:
    gateway = _gateway()
    monkeypatch.setattr(gateway, "_account_access", lambda _auth: {"enabled": True, "models": ["Ling-3.0-tiny"]})
    with pytest.raises(GatewayError) as denied:
        gateway.authorize_model("Bearer token", "Ling-3.0-flash")
    assert denied.value.status == HTTPStatus.FORBIDDEN
    assert denied.value.code == "MODEL_NOT_ENTITLED"


def test_gateway_never_uses_client_authorization_as_upstream_key(monkeypatch) -> None:
    gateway = _gateway()
    monkeypatch.setattr(gateway, "_account_access", lambda _auth: {"enabled": True, "models": ["Ling-3.0-flash"]})
    gateway.authorize_model("Bearer loom-user-token", "Ling-3.0-flash")
    assert gateway.config.ant_ling_api_key == "server-only-secret"
