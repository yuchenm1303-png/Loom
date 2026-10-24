import copy
import json
from urllib.parse import parse_qs, urlsplit

import pytest

from app.mcp_marketplace import MCPMarketplace, normalize, suggested_name


def record(**updates):
    server = {"name": "io.github.example/demo", "version": "1.2.3", "title": "Demo",
              "description": "Example server", "remotes": [{"type": "streamable-http", "url": "https://example.com/mcp"}]}
    server.update(updates)
    return {"server": server, "_meta": {"io.modelcontextprotocol.registry/official": {"status": "active"}}}


def package(registry="npm", **updates):
    raw = {"registryType": registry, "identifier": "@example/demo" if registry == "npm" else "example-demo",
           "version": "1.2.3", "transport": {"type": "stdio"}}
    raw.update(updates)
    return record(remotes=[], packages=[raw])


def client_for(tmp_path, raw):
    return MCPMarketplace(tmp_path, fetcher=lambda url: copy.deepcopy(raw))


def test_list_search_cursor_latest_and_persistent_cache(tmp_path):
    urls = []
    data = {"servers": [record(), record()], "metadata": {"nextCursor": "opaque+cursor:one/two"}}
    def fetch(url):
        urls.append(url)
        return data
    client = MCPMarketplace(tmp_path, fetcher=fetch, clock=lambda: 1000)
    result = client.list({"search": " demo & test ", "cursor": "exact+cursor"})
    query = parse_qs(urlsplit(urls[0]).query)
    assert query["search"] == ["demo & test"]
    assert query["cursor"] == ["exact+cursor"]
    assert query["version"] == ["latest"]
    assert len(result["servers"]) == 1
    assert result["next_cursor"] == "opaque+cursor:one/two"
    assert not result["cached"]
    restored = MCPMarketplace(tmp_path, fetcher=lambda url: pytest.fail("fresh cache must not fetch"), clock=lambda: 1100)
    assert restored.list({"search": "demo & test", "cursor": "exact+cursor"})["cached"]


def test_expired_cache_is_served_with_stale_warning_only_on_failure(tmp_path):
    clock = [1000]
    client = MCPMarketplace(tmp_path, fetcher=lambda url: {"servers": [record()]}, clock=lambda: clock[0])
    client.list({})
    clock[0] = 5000
    client.fetcher = lambda url: (_ for _ in ()).throw(TimeoutError())
    result = client.list({})
    assert result["stale"] and result["cached"]
    assert result["fetched_at"] == 1000
    assert result["warning"]
    assert result["servers"][0]["name"] == "io.github.example/demo"


def test_refresh_bypasses_fresh_cache(tmp_path):
    client = client_for(tmp_path, {"servers": [record()]})
    client.list({})
    client.fetcher = lambda url: {"servers": [record(name="io.github.other/second")]}
    assert client.list({"refresh": True})["servers"][0]["name"] == "io.github.other/second"


def test_missing_cache_and_network_failure_is_actionable(tmp_path):
    client = MCPMarketplace(tmp_path, fetcher=lambda url: (_ for _ in ()).throw(OSError("network")))
    with pytest.raises(RuntimeError, match="quick setup/manual"):
        client.list({})


def test_bad_entries_and_inactive_records_do_not_break_page(tmp_path):
    deleted = record(name="io.github.example/deleted")
    deleted["_meta"]["io.modelcontextprotocol.registry/official"]["status"] = "deleted"
    client = client_for(tmp_path, {"servers": [None, {"server": {}}, deleted, record()]})
    assert len(client.list({})["servers"]) == 1


@pytest.mark.parametrize("params", [{"search": []}, {"cursor": "a" * 2049}, {"search": "a" * 201}])
def test_invalid_queries_never_reach_network(tmp_path, params):
    client = MCPMarketplace(tmp_path, fetcher=lambda url: pytest.fail("unexpected request"))
    with pytest.raises(ValueError):
        client.list(params)


def test_detail_escapes_identity_and_rejects_response_mismatch(tmp_path):
    urls = []
    client = MCPMarketplace(tmp_path, fetcher=lambda url: (urls.append(url) or record()))
    client.detail({"name": "io.github.example/demo", "version": "1.2.3"})
    assert urls[0].endswith("/io.github.example%2Fdemo/versions/1.2.3")
    with pytest.raises(ValueError, match="different server"):
        client.detail({"name": "io.github.other/demo", "version": "1.2.3"})
    with pytest.raises(ValueError):
        client.detail({"name": "https://evil.example/mcp"})


def test_http_bearer_recipe_prepares_without_credentials_or_execution(tmp_path):
    raw = record(remotes=[{"type": "streamable-http", "url": "https://example.com/mcp",
                           "headers": [{"name": "Authorization", "value": "Bearer {token}", "isSecret": True, "isRequired": True}]}])
    client = client_for(tmp_path, raw)
    result = client.prepare({"name": raw["server"]["name"], "version": "1.2.3", "variant": "remote:0"})
    assert result["required_auth"]
    assert result["server"]["bearer_token_env"] == ""
    assert result["server"]["url"] == "https://example.com/mcp"
    assert result["server"]["name"] == suggested_name(raw["server"]["name"])
    assert not (tmp_path / "mcp-servers.json").exists()


def test_remote_url_variables_are_required_and_resolved(tmp_path):
    raw = record(remotes=[{"type": "streamable-http", "url": "https://{tenant}.example.com/mcp",
                           "variables": {"tenant": {"description": "Tenant name"}}}])
    client = client_for(tmp_path, raw)
    fields = client.detail({"name": raw["server"]["name"]})["server"]["variants"][0]["inputs"]
    assert fields[0]["required"]
    with pytest.raises(ValueError, match="Required"):
        client.prepare({"name": raw["server"]["name"], "variant": "remote:0"})
    result = client.prepare({"name": raw["server"]["name"], "variant": "remote:0", "inputs": {"url.tenant": "acme"}})
    assert result["server"]["url"] == "https://acme.example.com/mcp"


def test_optional_bearer_header_does_not_require_a_token(tmp_path):
    raw = record(remotes=[{"type": "streamable-http", "url": "https://example.com/mcp",
                           "headers": [{"name": "Authorization", "value": "Bearer {token}", "isRequired": False}]}])
    plan = client_for(tmp_path, raw).prepare({"name": raw["server"]["name"], "variant": "remote:0"})
    assert not plan["required_auth"]


def test_npm_pinned_package_arguments_and_environment_refs(tmp_path):
    raw = package(packageArguments=[{"type": "positional", "value": "serve"},
                                    {"type": "positional", "valueHint": "directory", "isRequired": True},
                                    {"type": "named", "name": "--mode", "default": "read", "choices": ["read", "write"]}],
                  environmentVariables=[{"name": "API_KEY", "isSecret": True, "isRequired": True},
                                        {"name": "OPTIONAL_KEY", "isSecret": True}])
    client = client_for(tmp_path, raw)
    params = {"name": raw["server"]["name"], "variant": "package:0", "inputs": {"argument.1": "C:/My Files"}}
    result = client.prepare(params)
    assert result["server"]["command"] == "npx"
    assert result["server"]["args"] == ["-y", "@example/demo@1.2.3", "serve", "C:/My Files", "--mode=read"]
    assert result["server"]["env_from"] == {"API_KEY": "API_KEY"}
    params["inputs"]["environment.0"] = "literal-secret-value!"
    with pytest.raises(ValueError, match="variable name"):
        client.prepare(params)


def test_pypi_recipe_uses_fixed_version_and_reports_runtime(tmp_path, monkeypatch):
    import app.mcp_marketplace as module
    monkeypatch.setattr(module.shutil, "which", lambda name: None)
    raw = package("pypi")
    result = client_for(tmp_path, raw).prepare({"name": raw["server"]["name"], "variant": "package:0"})
    assert result["server"]["command"] == "uvx"
    assert result["server"]["args"] == ["example-demo==1.2.3"]
    assert not result["runtime_available"]


@pytest.mark.parametrize("remote,reason", [
    ({"type": "sse", "url": "https://example.com/sse"}, "transport"),
    ({"type": "streamable-http", "url": "https://example.com/mcp?key=abc"}, "url"),
    ({"type": "streamable-http", "url": "http://localhost.evil/mcp"}, "url"),
    ({"type": "streamable-http", "url": "https://example.com/mcp", "headers": [{"name": "X-API-Key"}]}, "custom-headers"),
    ({"type": "streamable-http", "url": "https://example.com/mcp", "headers": [{"name": "Authorization", "value": "Basic {key}"}]}, "authorization"),
])
def test_unsupported_remote_modes_have_explicit_reasons(remote, reason):
    result = normalize(record(remotes=[remote]))
    assert not result["compatible"]
    assert result["variants"][0]["reason"] == reason


@pytest.mark.parametrize("updates,reason", [
    ({"registryType": "oci"}, "package-type"),
    ({"runtimeArguments": [{"type": "positional", "value": "--registry=evil"}]}, "runtime-arguments"),
    ({"packageArguments": [{"type": "positional", "isSecret": True, "valueHint": "token"}]}, "secret-argument"),
    ({"identifier": "https://example.com/package.tgz"}, "package-identifier"),
    ({"version": "latest"}, "package-version"),
    ({"registryBaseUrl": "https://evil.example"}, "package-registry"),
    ({"fileSha256": "a" * 64}, "checksum"),
])
def test_advanced_or_unsafe_package_recipes_are_manual(updates, reason):
    result = normalize(package(**updates))
    assert result["variants"][0]["reason"] == reason
    assert not result["compatible"]


def test_prepare_rejects_unknown_inputs_and_inactive_version(tmp_path):
    raw = record()
    client = client_for(tmp_path, raw)
    with pytest.raises(ValueError, match="Unknown setup"):
        client.prepare({"name": raw["server"]["name"], "variant": "remote:0", "inputs": {"command": "injected"}})
    raw["_meta"]["io.modelcontextprotocol.registry/official"]["status"] = "deprecated"
    inactive = client_for(tmp_path / "inactive", raw)
    with pytest.raises(ValueError, match="manual setup"):
        inactive.prepare({"name": raw["server"]["name"], "variant": "remote:0"})


def test_publisher_links_are_https_without_credentials():
    server = normalize(record(websiteUrl="javascript:alert(1)", repository={"url": "https://user:pass@example.com"}))
    assert not server["website"] and not server["repository"]


def test_name_is_stable_and_does_not_collide_between_publishers():
    assert suggested_name("io.github.one/demo") != suggested_name("io.github.two/demo")
    assert len(suggested_name("io.github.one/" + "x" * 100)) <= 64


def test_corrupted_cache_is_ignored_and_replaced(tmp_path):
    client = client_for(tmp_path, {"servers": [record()]})
    client.list({})
    cache = next(tmp_path.glob("*.json"))
    content = json.loads(cache.read_text())
    content["data"] = {"servers": "invalid"}
    cache.write_text(json.dumps(content))
    result = client.list({})
    assert not result["cached"]
    assert result["servers"][0]["compatible"]


def test_numeric_choice_and_variable_arguments_are_validated(tmp_path):
    raw = package(packageArguments=[{"type": "named", "name": "--port", "format": "number", "isRequired": True},
                                    {"type": "positional", "value": "prefix-{mode}", "variables": {"mode": {"choices": ["read", "write"], "default": "read"}}}])
    client = client_for(tmp_path, raw)
    params = {"name": raw["server"]["name"], "variant": "package:0", "inputs": {"argument.0": "8080"}}
    assert client.prepare(params)["server"]["args"][-2:] == ["--port=8080", "prefix-read"]
    params["inputs"]["argument.0"] = "NaN"
    with pytest.raises(ValueError, match="number"):
        client.prepare(params)
    params["inputs"] = {"argument.0": "8080", "argument.1.mode": "unknown"}
    with pytest.raises(ValueError, match="allowed value"):
        client.prepare(params)


def test_fixed_environment_values_are_not_silently_dropped():
    result = normalize(package(environmentVariables=[{"name": "MODE", "value": "production"}]))
    assert not result["compatible"]
    assert result["variants"][0]["reason"] == "fixed-environment"


def test_rpc_registry_browse_and_prepare_do_not_save_or_connect(tmp_path):
    from app.agent_runtime import ConfiguredMCPRuntime, FileAgentSessionStore, ToolRegistry
    from app.app_server import LoomAppServerService, LoomRpcController
    store = FileAgentSessionStore(tmp_path)
    runtime = ConfiguredMCPRuntime(platform=object(), store=store, tools=ToolRegistry(), mcp_servers=(),
                                   auto_configure_computer=False, auto_configure_browser=False,
                                   auto_configure_web_search=False)
    service = LoomAppServerService(runtime=runtime, store=store, model="test", default_workspace=tmp_path)
    raw = record()
    service._mcp_marketplace_client = MCPMarketplace(tmp_path / "cache", fetcher=lambda url: {"servers": [raw]} if "?" in url else raw)
    rpc = LoomRpcController(service)
    try:
        page = rpc._dispatch("mcp/marketplace/list", {})
        assert len(page["servers"]) == 1
        detail = rpc._dispatch("mcp/marketplace/detail", {"name": raw["server"]["name"], "version": "1.2.3"})
        assert detail["server"]["compatible"]
        plan = rpc._dispatch("mcp/marketplace/prepare", {"name": raw["server"]["name"], "version": "1.2.3", "variant": "remote:0"})
        assert plan["server"]["url"] == "https://example.com/mcp"
        assert runtime.mcp_clients.status()["connected_servers"] == 0
        assert not (tmp_path / "mcp-servers.json").exists()
    finally:
        runtime.close()


def test_registry_http_does_not_follow_redirects_and_limits_body(monkeypatch):
    import httpx
    from app import mcp_marketplace as module
    original = httpx.Client
    requests = []
    def redirect(request):
        requests.append(request)
        return httpx.Response(302, headers={"location": "https://other.example/"})
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: original(transport=httpx.MockTransport(redirect), **kwargs))
    with pytest.raises(httpx.HTTPStatusError):
        module.fetch_json("https://registry.modelcontextprotocol.io/v0.1/servers")
    assert len(requests) == 1
    assert "authorization" not in requests[0].headers
    monkeypatch.setattr(module, "MAX_BYTES", 64)
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: original(transport=httpx.MockTransport(lambda request: httpx.Response(200, content=b"x" * 65)), **kwargs))
    with pytest.raises(ValueError, match="too large"):
        module.fetch_json("https://registry.modelcontextprotocol.io/v0.1/servers")
