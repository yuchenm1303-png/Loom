import http.client
import io
import json
import threading
from types import SimpleNamespace as NS

import pytest

import loom_model_bridge as bridge
from app.ai.model_store import ModelConfigStore
from app.ai.reasoning_store import ReasoningConfigStore
from app.ai.model_selection_store import ModelSelectionStore
from services.loom_model_policy.catalog import selection_for_model
from services.loom_model_gateway.routes import PROVIDERS, gateway_base_url, request_route
from services.loom_model_gateway.server import GatewayConfig, GatewayServer, LoomModelGateway, GatewayError

MODELS = {"minimax": "MiniMax-M3.1-Flash-Preview", "deepseek": "deepseek-flash", "intern": "intern-s2",
          "ant-ling": "Ling-3.0-flash", "opencode-go": "minimax-m3",
          "opencode-console": "deepseek-v4.1-flash-free", "managed-relay": "MiniMax-M3.1-Flash-Preview"}


@pytest.mark.parametrize("group,model", MODELS.items())
def test_clean_desktop_resolves_builtins_without_any_provider_key(tmp_path, monkeypatch, group, model):
    monkeypatch.setenv("LOOM_HOME", str(tmp_path))
    def forbidden(*args, **kwargs):
        raise AssertionError("built-in model must not read workstation provider credentials")
    for name in ["_primary_minimax_key", "_deepseek_key", "_opencode_go_key", "_managed_relay_key"]:
        monkeypatch.setattr(bridge, name, forbidden)
    spec = bridge.resolve_model_spec(selection_for_model(group, model), model=model, home=tmp_path)
    assert spec["apiKey"] == ""
    assert spec["authMode"] == "loom-account"
    assert spec["baseUrl"] == gateway_base_url(group, {})
    assert spec["provider"] == ("opencode-go" if group == "opencode-go" else "openai-compatible")
    assert spec["model"] == model
    assert spec["personalConnection"] == {"adapter": "openai-compatible", "baseUrl": PROVIDERS[group][0]}


def test_fresh_catalog_does_not_discover_or_fetch_keys_on_client(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("upstreams are server-owned")
    monkeypatch.setattr(bridge.urllib.request, "urlopen", forbidden)
    monkeypatch.setattr(bridge, "_credential_get", forbidden)
    store = ModelConfigStore(tmp_path, secret_getter=forbidden)
    snapshot = bridge._snapshot(store, ReasoningConfigStore(tmp_path), ModelSelectionStore(tmp_path))
    assert all(p["authMode"] == "loom-account" and p["configured"] for p in snapshot["profiles"])
    assert any(p["model"] == "MiniMax-M3.1-Flash-Preview" for p in snapshot["profiles"])


@pytest.mark.parametrize("group,endpoint", [(group, "chat/completions") for group in PROVIDERS] + [("opencode-go", "messages"), ("opencode-go", "responses")])
def test_gateway_http_routes_use_server_credentials_and_preserve_protocol(monkeypatch, group, endpoint):
    model = MODELS[group]
    policy_id = selection_for_model(group, model)
    gateway = LoomModelGateway(GatewayConfig(account_base_url="http://account", ant_ling_base_url="", ant_ling_api_key="",
        model_policy_base_url="http://policy", upstreams={key: ("https://provider.invalid/"+key, "server-secret-"+key) for key in PROVIDERS}))
    authorizations, requests = [], []
    def access(auth):
        authorizations.append(auth)
        return {"enabled": True, "models": [policy_id], "catalog": {"models": [{"model": model, "group_id": group, "available": True}]}}
    gateway._account_access = access
    def upstream(request, **kwargs):
        requests.append(request)
        response = io.BytesIO(b'{"ok":true}')
        response.status = 200
        response.headers = {"Content-Type": "application/json"}
        return response
    monkeypatch.setattr("services.loom_model_gateway.server.urllib.request.urlopen", upstream)
    server = GatewayServer(("127.0.0.1", 0), gateway)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=3)
        body = json.dumps({"model": model, "stream": False, "tools": [{"name": "test"}]}).encode()
        path = "/v1/" + ("" if group == "ant-ling" else group + "/") + endpoint
        connection.request("POST", path, body, {"Authorization": "Bearer loom_model_account", "Content-Type": "application/json", "x-api-key": "must-not-forward", "x-opencode-session": "thread-1"})
        response = connection.getresponse()
        payload = json.loads(response.read())
        assert authorizations == ["Bearer loom_model_account"]
        if group == "opencode-console":
            assert response.status == 403
            assert payload["error"]["code"] == "OPENCODE_FREE_CLIENT_RESTRICTED"
            assert not requests
            connection.close()
            return
        assert response.status == 200
        assert payload == {"ok": True}
        request = requests[0]
        assert request.full_url == "https://provider.invalid/"+group+"/"+endpoint
        assert request.data == body
        assert request.get_header("Authorization") == "Bearer server-secret-"+group
        assert "loom_model_account" not in repr(request.header_items())
        if endpoint == "messages":
            assert request.get_header("X-api-key") == "server-secret-"+group
            assert request.get_header("Anthropic-version") == "2023-06-01"
        connection.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(2)


@pytest.mark.parametrize("path", ["/v1/minimax/messages", "/v1/unknown/chat/completions", "/v1/intern/../minimax/chat/completions", "/v1/opencode-go/other"])
def test_gateway_route_cannot_escape_provider_or_protocol(path):
    with pytest.raises(ValueError):
        request_route(path)


@pytest.mark.parametrize("model,protocol", [("minimax-m3", "messages"), ("gpt-5.6-luna", "responses"), ("kimi-k3", "chat-completions")])
def test_opencode_protocols_keep_gateway_on_cold_and_hot_model_bindings(model, protocol):
    from app.runtime_model_switch import build_runtime_model_platform
    from app.ai import AGENT_FAST_ROLE, CredentialRef, ModelProfile, ProviderConnection, ProviderAdapter
    from app.ai.opencode_go_runtime import OpenCodeGoBackend
    base = gateway_base_url("opencode-go", {})
    platform = build_runtime_model_platform(provider="opencode-go", base_url=base, model=model, api_key="loom_model_test")
    connection = ProviderConnection(provider_id="test", adapter=ProviderAdapter.OPENCODE_GO, credential_ref=CredentialRef.runtime("test"), base_url=base)
    backend = OpenCodeGoBackend(connection=connection, profile=ModelProfile(profile_id="test", provider="test", model=model, capabilities=AGENT_FAST_ROLE.required_capabilities), api_key="loom_model_test")
    assert backend.protocol == protocol
    actual = backend.backend.base_url if protocol == "messages" else str(backend.backend.client.base_url).rstrip("/")
    assert actual == base


def test_existing_builtin_thread_migrates_and_rebinds_after_account_change(tmp_path, monkeypatch):
    import app.app_server_reasoning as module
    from app.app_server_reasoning import ReasoningManagedLoomAppServerService
    service = object.__new__(ReasoningManagedLoomAppServerService)
    session = NS(session_id="thread", model_selection="builtin:minimax", model="MiniMax-M3", model_provider="openai-compatible",
        model_base_url="https://api.minimaxi.com/v1", model_vision=None)
    service._ensure_thread_model_metadata = lambda value: value
    service._session_reasoning = lambda value: None
    service._runtime_home = lambda: tmp_path
    service._thread_uses_default_model = lambda value: True
    cleared, bindings, saved = [], [], []
    service.runtime = NS(has_session_model=lambda value: True, clear_session_model=cleared.append,
        set_session_model=lambda *args, **kwargs: None)
    service.store = NS(save=saved.append)
    monkeypatch.setattr(module, "build_runtime_model_platform", lambda **kwargs: bindings.append(kwargs) or object())
    monkeypatch.setattr(module, "validate_runtime_reasoning", lambda **kwargs: None)
    service._loom_account_model_credential = "loom_model_account_A"
    service._ensure_thread_model_runtime(session)
    service._loom_account_model_credential = "loom_model_account_B"
    service._ensure_thread_model_runtime(session)
    assert cleared == ["thread", "thread"]
    assert [binding["api_key"] for binding in bindings] == ["loom_model_account_A", "loom_model_account_B"]
    assert all(binding["base_url"].endswith("/minimax") for binding in bindings)
    assert session.model_base_url == gateway_base_url("minimax", {})
    assert saved


def test_saved_minimax_connection_stays_personal_and_keeps_its_key(tmp_path):
    secrets = {}
    store = ModelConfigStore(tmp_path, secret_setter=lambda alias,value: secrets.__setitem__(alias,value), secret_getter=secrets.get)
    saved = store.save_model(display_name="Personal", adapter="openai-compatible", base_url="https://private.invalid/v1", model="MiniMax-M3", api_key="personal-key")
    described = bridge._describe_model(store, ReasoningConfigStore(tmp_path), saved.selection, "MiniMax-M3.1-Flash-Preview")
    resolved = bridge._resolve(store, ReasoningConfigStore(tmp_path), ModelSelectionStore(tmp_path), saved.selection)
    assert described["selection"] == saved.selection
    assert described["baseUrl"] == "https://private.invalid/v1"
    assert resolved["apiKey"] == "personal-key"
    assert resolved.get("authMode") != "loom-account"


@pytest.mark.parametrize("group,model", MODELS.items())
def test_every_gateway_group_enforces_account_model_permissions(group, model):
    gateway = LoomModelGateway(GatewayConfig(account_base_url="http://account", ant_ling_base_url="", ant_ling_api_key="",
        model_policy_base_url="http://policy", upstreams={group: ("https://provider.invalid", "server-key")}))
    gateway._account_access = lambda auth: {"enabled": True, "models": [], "catalog": {"models": [{"model": model, "group_id": group, "available": True}]}}
    with pytest.raises(GatewayError) as denied:
        gateway.authorize_model("Bearer loom_model_test", model, group)
    assert denied.value.status == 403
