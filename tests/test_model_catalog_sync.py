from __future__ import annotations

import json
import threading
from http.server import ThreadingHTTPServer
from types import SimpleNamespace
from urllib.request import Request, urlopen

import pytest

from services.loom_model_policy.catalog import GROUPS, selection_for_model
from services.loom_model_policy.catalog_sync import CatalogSynchronizer, parse_models
from services.loom_model_policy.policy import PolicyStore
from services.loom_model_policy.server import ModelPolicyApplication, PolicyRequestHandler


def decision(access, selection):
    return next(item for item in access["decisions"] if item["model_id"] == selection)


def test_discovery_retirement_reappearance_and_policy_survive_restart(tmp_path):
    path = tmp_path / "policy.db"
    store = PolicyStore(path)
    models = parse_models("minimax", {"data": [{"id": "MiniMax-new"}, {"id": "MiniMax-other"}]})
    first, other = [item["model_id"] for item in models]
    store.set_user_rule(1, 7, first, False)
    store.replace_provider_catalog("minimax", models)
    before = store.effective_access(7, {"enabled": True})
    assert decision(before, first)["source"] == "user"
    assert other in before["models"]
    store.replace_provider_catalog("minimax", models[1:])
    retired = store.effective_access(7, {"enabled": True})
    assert decision(retired, first) == {"model_id": first, "enabled": False, "source": "catalog", "groups": []}
    assert retired["revision"] != before["revision"]
    store = PolicyStore(path)
    assert not next(row for row in store.catalog()["models"] if row["model_id"] == first)["available"]
    store.replace_provider_catalog("minimax", models)
    assert decision(store.effective_access(7, {"enabled": True}), first)["source"] == "user"


def test_discovery_failure_retains_catalog_and_does_not_expose_secrets(tmp_path):
    store = PolicyStore(tmp_path / "policy.db")
    models = parse_models("minimax", {"data": [{"id": "MiniMax-new"}]})
    store.replace_provider_catalog("minimax", models)
    before = store.catalog()
    def failed(url, credential):
        raise OSError("secret-example-token at " + url)
    sync = CatalogSynchronizer(store, environ={"MINIMAX_API_KEY": "secret-example-token"}, fetch=failed)
    assert sync.refresh()
    after = store.catalog()
    assert before["models"] == after["models"]
    assert before["revision"] == after["revision"]
    assert "secret-example-token" not in json.dumps(after)
    assert next(row for row in after["providers"] if row["group_id"] == "minimax")["error"]


def test_all_provider_discovery_is_independent_of_clients_and_keeps_global_denies(tmp_path):
    store = PolicyStore(tmp_path / "policy.db")
    from services.loom_model_policy.catalog_sync import PROVIDERS
    env = {keys[0]: "server-key" for _, keys, _ in PROVIDERS.values()}
    sync = CatalogSynchronizer(store, environ=env, fetch=lambda url, key: {"data": [{"id": "future-model"}]})
    store.set_global_model_group(1, "deepseek", False)
    sync.refresh()
    access = store.effective_access(7, {"enabled": True})
    for group in GROUPS:
        selection = selection_for_model(group.id, "future-model")
        assert decision(access, selection)["enabled"] == (group.id != "deepseek")
    assert len([row for row in access["catalog"]["models"] if row["available"]]) == len(GROUPS)
    # Discovery on a subsequent run must not reset individual rules.
    minimax = selection_for_model("minimax", "future-model")
    store.set_global_rule(1, minimax, False)
    sync.refresh()
    assert decision(store.effective_access(7, {"enabled": True}), minimax)["source"] == "global"


@pytest.mark.parametrize("payload", [{}, {"data": []}, {"data": [{"id": 1}]}, {"data": [{"id": "\n"}]},
    {"data": [{"id": "MiniMax-M3"}], "has_more": True}])
def test_invalid_provider_response_is_rejected(payload):
    with pytest.raises(ValueError):
        parse_models("minimax", payload)


def test_manifest_supports_providers_without_listing_endpoint(tmp_path):
    manifest = tmp_path / "catalog.json"
    manifest.write_text(json.dumps({"ant-ling": [{"id": "Ling-new", "name": "New Ling"}]}))
    store = PolicyStore(tmp_path / "policy.db")
    sync = CatalogSynchronizer(store, environ={"LOOM_MODEL_CATALOG_MANIFEST": str(manifest)})
    sync.refresh()
    row = next(item for item in store.catalog()["models"] if item["model"] == "Ling-new")
    assert row["source"] == "manifest" and row["name"] == "New Ling"
    # Legacy account allow-lists also apply to newly discovered Ant Ling IDs.
    access = store.effective_access(7, {"enabled": True, "source": "override", "models": ["Ling-3.0-flash"]})
    assert decision(access, row["model_id"])["source"] == "user_legacy"
    assert not decision(access, row["model_id"])["enabled"]


def test_refreshes_coalesce(tmp_path):
    sync = CatalogSynchronizer(PolicyStore(tmp_path / "policy.db"), environ={})
    with sync.guard:
        assert sync.refresh() is False


def test_http_access_and_check_share_catalog_and_old_clients_are_read_only(tmp_path):
    app = object.__new__(ModelPolicyApplication)
    app.store = PolicyStore(tmp_path / "policy.db")
    app.accounts = SimpleNamespace(user_id_for_authorization=lambda auth: 7, model_access=lambda auth: {"enabled": True})
    app.store.replace_provider_catalog("minimax", parse_models("minimax", {"data": [{"id": "MiniMax-new"}]}))
    server = ThreadingHTTPServer(("127.0.0.1", 0), PolicyRequestHandler)
    server.application = app
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = "http://127.0.0.1:" + str(server.server_port)
    def request(path, body=None):
        req = Request(base + path, data=json.dumps(body).encode() if body is not None else None,
            headers={"Authorization": "Bearer test", "Content-Type": "application/json"})
        with urlopen(req, timeout=2) as response:
            return json.load(response)
    try:
        known, unknown = "builtin:minimax:MiniMax-new", "builtin:minimax:invented"
        get = request("/v1/access")["access"]
        post = request("/v1/access", {"model_ids": [known, unknown]})["access"]
        checked = request("/v1/check", {"model_id": known})
        assert decision(get, known) == decision(post, known) == checked["decision"]
        assert decision(post, unknown)["source"] == "catalog"
        assert unknown not in {row["model_id"] for row in app.store.global_rules()}
        assert get["catalog"]["revision"] == post["catalog"]["revision"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(2)
