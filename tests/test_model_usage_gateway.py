import concurrent.futures
import json
import http.client
import threading
import time
import io
from unittest.mock import Mock

import pytest

from services.loom_model_gateway.usage import UsageService, endpoint, parse_usage
from services.loom_model_gateway.routes import PROVIDERS, request_route
from services.loom_model_gateway.server import LoomModelGateway, GatewayConfig, GatewayError, GatewayServer


GO = {"usage": {"rolling": {"percent": 1, "resetsAt": "2026-10-15T19:51:43.000Z"},
                "weekly": {"percent": 0}, "monthly": {"percent": 95}}}


def test_go_reported_usage_is_not_remaining_and_missing_is_not_zero():
    result = parse_usage("opencode-go", GO, "deepseek-flash")
    assert [r["remainingPercent"] for r in result["windows"]] == [99, 100, 5]
    assert result["windows"][0]["resetsAt"] == 1792093903000
    with pytest.raises(ValueError):
        parse_usage("opencode-go", {"usage": {"rolling": {"percent": None}}}, "demo")


def test_minimax_selects_exact_model_or_general_not_unrelated_quota():
    rows = [{"model_name": "video", "current_interval_remaining_percent": 99},
            {"model_name": "general", "current_interval_remaining_percent": 23, "end_time": 1800000000,
             "current_weekly_remaining_percent": 100, "current_weekly_status": 3}]
    result = parse_usage("minimax", {"model_remains": rows}, "MiniMax-M3.1-Flash-Preview")
    assert result["windows"] == [{"label": "5h", "remainingPercent": 23, "resetsAt": 1800000000000}]
    with pytest.raises(ValueError):
        parse_usage("minimax", {"model_remains": rows}, "unknown")


def test_money_precision_and_token_caps_are_not_wallet_unlimited():
    result = parse_usage("deepseek", {"balance_infos": [{"currency": "CNY", "total_balance": "0.00000001"}]}, "demo")
    assert result["balances"] == [{"currency": "CNY", "total": "0.00000001"}]
    result = parse_usage("managed-relay", {"data": {"unlimited_quota": True}}, "demo")
    assert result["scope"] == "api-key" and result["balances"] == []
    assert "不代表" in result["message"]


@pytest.mark.parametrize("group", PROVIDERS)
def test_every_group_has_authenticated_usage_route(group):
    path = "/v1/usage" if group == "ant-ling" else "/v1/" + group + "/usage"
    assert request_route(path) == (group, "usage")
    gateway = LoomModelGateway(GatewayConfig("https://account.example", "https://api.ant-ling.com/v1", "server-secret"))
    gateway.authorize_model = Mock(side_effect=GatewayError(403, "MODEL_NOT_ENTITLED", "denied"))
    gateway.usage_service.query = Mock(side_effect=AssertionError("must authorize before fetching or consulting cache"))
    with pytest.raises(GatewayError):
        gateway.usage("Bearer account-token", group, "demo")
    gateway.usage_service.query.assert_not_called()


def test_gateway_uses_server_key_and_labels_shared_scope_without_exposing_console_or_key():
    gateway = LoomModelGateway(GatewayConfig("https://account.example", "https://api.ant-ling.com/v1", "server-secret"))
    gateway.authorize_model = Mock()
    gateway.usage_service.query = Mock(return_value={"status": "unsupported", "scope": "provider-account", "dashboardUrl": "https://chat.ant-ling.com"})
    result = gateway.usage("Bearer account-token", "ant-ling", "Ling-3.0-flash", True)
    gateway.usage_service.query.assert_called_once_with("ant-ling", "https://api.ant-ling.com/v1", "server-secret", "Ling-3.0-flash", True)
    assert result["scope"] == "loom-shared-provider-account"
    assert "dashboardUrl" not in result
    assert "server-secret" not in json.dumps(result)


@pytest.mark.parametrize("group", ["intern", "ant-ling"])
def test_unverified_providers_do_not_guess_zero_or_call_console_with_api_key(group):
    service = UsageService()
    service._fetch = Mock(side_effect=AssertionError("no verified API"))
    result = service.query(group, PROVIDERS[group][0], "private-key", "demo")
    assert result["status"] == "unsupported"
    assert result["code"] == "NO_VERIFIED_API"
    assert result["windows"] == [] and result["balances"] == []


def test_provider_cache_partitions_credentials_deduplicates_and_parses_each_model():
    service = UsageService()
    def fetch(url, key):
        time.sleep(.03)
        return time.monotonic(), GO, 1234, ""
    service._fetch = Mock(side_effect=fetch)
    args = ("opencode-go", PROVIDERS["opencode-go"][0], "private-key")
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(lambda _: service.query(*args, "demo", True), range(4)))
    assert service._fetch.call_count == 1
    assert all(r["checkedAt"] == 1234 for r in results)
    service.query(*args, "other-model")
    assert service._fetch.call_count == 1
    service.query(args[0], args[1], "rotated-key", "demo")
    assert service._fetch.call_count == 2
    assert "private-key" not in repr(service._cache)


def test_query_failures_never_serialize_upstream_payload_or_secret():
    service = UsageService()
    service._fetch = Mock(return_value=(time.monotonic(), None, 100, "HTTP_403"))
    result = service.query("opencode-go", PROVIDERS["opencode-go"][0], "secret", "demo")
    assert result["status"] == "error" and result["code"] == "HTTP_403"
    assert result["balances"] == [] and result["windows"] == []
    assert "secret" not in json.dumps(result)


def test_credentials_only_sent_to_known_official_endpoint():
    for base in ["https://api.deepseek.com.evil.test", "http://api.deepseek.com", "https://api.deepseek.com:444", "https://user@api.deepseek.com"]:
        assert endpoint("deepseek", base) is None


@pytest.mark.parametrize("group", PROVIDERS)
def test_usage_http_route_requires_model_entitlement_and_preserves_query(group):
    gateway = LoomModelGateway(GatewayConfig("http://account", "", "server-key"))
    def authorize(auth, model, group_id):
        if auth != "Bearer scoped-account" or model != "demo" or group_id != group:
            raise GatewayError(403, "MODEL_NOT_ENTITLED", "denied")
    gateway.authorize_model = authorize
    gateway.usage_service.query = Mock(return_value={"status": "ok", "provider": "test", "checkedAt": 1,
        "windows": [], "balances": [], "scope": "provider-account"})
    server = GatewayServer(("127.0.0.1", 0), gateway)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    path = "/v1/" + ("" if group == "ant-ling" else group + "/") + "usage?model=demo&force=1"
    try:
        connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=3)
        connection.request("GET", path)
        rejected = connection.getresponse()
        assert rejected.status == 403
        rejected.read()
        gateway.usage_service.query.assert_not_called()
        connection.request("GET", path, headers={"Authorization": "Bearer scoped-account"})
        response = connection.getresponse()
        assert response.status == 200
        result = json.loads(response.read())
        assert result["scope"] == "loom-shared-provider-account"
        assert gateway.usage_service.query.call_args.args[-2:] == ("demo", True)
        assert "server-key" not in json.dumps(result)
        connection.close()
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=3)


def test_provider_fetch_has_size_bound_timeout_and_no_redirects(monkeypatch):
    import urllib.request
    observed = []
    class Response(io.BytesIO):
        pass
    class Opener:
        def open(self, request, timeout):
            observed.append((request, timeout))
            return Response(b"x" * 65537)
    def build(handler):
        assert issubclass(handler, urllib.request.HTTPRedirectHandler)
        assert handler().redirect_request(None, None, None, None, None, None) is None
        return Opener()
    monkeypatch.setattr(urllib.request, "build_opener", build)
    _, payload, _, code = UsageService._fetch("https://opencode.ai/zen/go/v1/usage", "private-key")
    assert payload is None
    assert observed[0][1] == 8
    assert observed[0][0].get_header("Authorization") == "Bearer private-key"
