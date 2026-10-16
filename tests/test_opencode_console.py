"""OpenCode Console (not OpenCode Go) access, billing and discovery regressions."""
from __future__ import annotations

import io
import json

import pytest

import loom_model_bridge as bridge
from services.loom_model_gateway.routes import gateway_base_url, request_route
from services.loom_model_gateway.server import GatewayConfig, GatewayError, LoomModelGateway
from services.loom_model_policy.catalog import (
    OPENCODE_CONSOLE_FREE_MODELS,
    is_opencode_console_free,
    selection_for_model,
)
from services.loom_model_policy.catalog_sync import parse_opencode_console_config
from services.loom_model_policy.policy import PolicyStore


FREE = "deepseek-v4.1-flash-free"
PAID = "deepseek-v4.1-flash"
BASE = "https://opencode.ai/inference/openai/v1"


def _gateway(monkeypatch, key: str = ""):
    gateway = LoomModelGateway(GatewayConfig(
        account_base_url="http://account/v1",
        ant_ling_base_url="",
        ant_ling_api_key="",
        model_policy_base_url="http://policy/v1",
        upstreams={"opencode-console": (BASE, key)},
    ))
    enabled = [FREE, PAID]
    gateway._account_access = lambda auth: {
        "enabled": True,
        "models": [selection_for_model("opencode-console", model) for model in enabled],
        "catalog": {"models": [
            {"group_id": "opencode-console", "model": model, "available": True}
            for model in enabled
        ]},
    }
    requests = []

    def urlopen(request, **_kwargs):
        requests.append(request)
        result = io.BytesIO(b'{"ok":true}')
        result.status = 200
        result.headers = {"Content-Type": "application/json"}
        return result

    monkeypatch.setattr("services.loom_model_gateway.server.urllib.request.urlopen", urlopen)
    return gateway, requests


def test_free_console_model_uses_separate_gateway_and_no_provider_credentials(monkeypatch):
    gateway, requests = _gateway(monkeypatch)
    response = gateway.upstream_request(
        json.dumps({"model": FREE, "stream": False}).encode(),
        "Bearer loom-account-key", "opencode-console",
    )
    assert response.status == 200
    assert requests[0].full_url == BASE + "/chat/completions"
    assert requests[0].get_header("Authorization") is None
    assert "loom-account-key" not in repr(requests[0].header_items())
    assert gateway_base_url("opencode-console", {}).endswith("/opencode-console")
    assert request_route("/v1/opencode-console/chat/completions") == (
        "opencode-console", "chat/completions")


def test_console_paid_requires_distinct_key_even_if_user_is_entitled(monkeypatch):
    gateway, requests = _gateway(monkeypatch)
    with pytest.raises(GatewayError) as error:
        gateway.upstream_request(
            json.dumps({"model": PAID}).encode(), "Bearer loom-account",
            "opencode-console",
        )
    assert error.value.code == "UPSTREAM_NOT_CONFIGURED"
    assert not requests


def test_console_free_stays_anonymous_even_when_paid_key_is_configured(monkeypatch):
    gateway, requests = _gateway(monkeypatch, "console-only-key")
    for model in (FREE, PAID):
        gateway.upstream_request(
            json.dumps({"model": model}).encode(), "Bearer loom-account",
            "opencode-console",
        )
    assert requests[0].get_header("Authorization") is None
    assert requests[1].get_header("Authorization") == "Bearer console-only-key"


def test_go_and_console_models_never_share_selection_or_runtime_binding():
    free = selection_for_model("opencode-console", FREE)
    go = selection_for_model("opencode-go", PAID)
    assert free != go
    assert bridge._canonical_builtin_selection(free, PAID) == selection_for_model(
        "opencode-console", PAID)
    assert bridge._safe_opencode_console(FREE)["adapter"] == "openai-compatible"
    assert bridge._safe_opencode_console(FREE)["baseUrl"] == BASE
    assert bridge._safe_opencode_go(PAID, configured=True)["groupId"] == "opencode-go"


def test_console_config_discovery_never_imports_go_or_responses_models():
    payload = {"providers": {
        "opencode": {
            "package": "aisdk:@ai-sdk/openai-compatible",
            "settings": {"baseURL": BASE},
            "models": {"minimax-m3": {"name": "MiniMax M3"},
                       "gpt-response-only": {"api": "responses"},
                       FREE: {"name": "DeepSeek V4.1 Flash Free"}},
        },
        "opencode-go": {"models": {"go-only": {}}},
    }}
    found = parse_opencode_console_config(payload)
    ids = {row["model"] for row in found}
    assert FREE in ids
    assert "minimax-m3" in ids
    assert "gpt-response-only" not in ids
    assert "go-only" not in ids
    assert set(OPENCODE_CONSOLE_FREE_MODELS) <= ids
    with pytest.raises(ValueError):
        parse_opencode_console_config({"providers": {"opencode": {
            **payload["providers"]["opencode"],
            "package": "aisdk:@ai-sdk/anthropic",
        }}})


def test_free_models_seed_policy_and_paid_discovery_is_opt_in(tmp_path):
    store = PolicyStore(tmp_path / "models.sqlite3")
    free_selection = selection_for_model("opencode-console", FREE)
    paid_selection = selection_for_model("opencode-console", PAID)
    assert is_opencode_console_free(FREE)
    assert not is_opencode_console_free(PAID)
    assert free_selection in store.effective_access(8, {"enabled": True})["models"]
    store.replace_provider_catalog("opencode-console", [
        {"model_id": free_selection, "model": FREE, "name": FREE},
        {"model_id": paid_selection, "model": PAID, "name": PAID},
    ])
    access = store.effective_access(8, {"enabled": True})
    assert free_selection in access["models"]
    assert paid_selection not in access["models"]
    store.set_global_rule(1, paid_selection, True)
    assert paid_selection in store.effective_access(8, {"enabled": True})["models"]
    store.set_global_model_group(1, "opencode-console", False)
    assert free_selection not in store.effective_access(8, {"enabled": True})["models"]
