from __future__ import annotations

import json
import pytest
from urllib.parse import parse_qs, urlsplit

from app.agent_runtime import (
    AgentStatus,
    BraveWebSearchProvider,
    DuckDuckGoWebSearchProvider,
    FileAgentSessionStore,
    PermissionMode,
    SandboxManager,
    SandboxPolicy,
    TavilyWebSearchProvider,
    WebSearchResponse,
    WebSearchResult,
    WebSearchRuntime,
    web_search_provider_from_env,
)
from app.agent_runtime.workspace_tools import loom_default_tools
from app.ai import AGENT_FAST_ROLE, ModelResponse, ToolCall
from app.agent_runtime.web_search import WebSearchError
from app.agent_runtime.web_search_tools import web_search_tools
from app.agent_runtime.tools import ToolContext


class ScriptedPlatform:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def execute_chat(self, profile_id, request):
        self.requests.append((profile_id, request))
        if not self.responses:
            raise AssertionError("scripted platform ran out of responses")
        return self.responses.pop(0)


class FakeSearchProvider:
    provider_name = "fake"

    def __init__(self):
        self.calls = []

    def search(self, query: str, *, count: int = 8) -> WebSearchResponse:
        self.calls.append((query, count))
        return WebSearchResponse(
            provider=self.provider_name,
            query=query,
            results=(
                WebSearchResult(
                    title="Loom result",
                    url="https://example.com/loom",
                    snippet="A current search result for Loom.",
                    source="example.com",
                ),
            ),
            request_id="req-fake",
        )


def _runtime(tmp_path, responses, provider, mode=PermissionMode.APPROVAL):
    store = FileAgentSessionStore(tmp_path / "state")
    platform = ScriptedPlatform(responses)
    # This file tests the WebSearchRuntime permission/network contract itself.
    # The production stack adds ToolSearch later, where schema-pressure shedding
    # may intentionally defer unrelated direct tools; that behavior has its own
    # tests and must not turn a web-search permission test into an exposure test.
    runtime = WebSearchRuntime(
        platform=platform,
        store=store,
        tools=loom_default_tools(),
        sandbox_manager=SandboxManager(policy=SandboxPolicy.OFF),
        web_search_provider=provider,
        auto_configure_web_search=False,
    )
    workspace = tmp_path / "project"
    workspace.mkdir(exist_ok=True)
    session = runtime.create_session(
        AGENT_FAST_ROLE.role_id,
        workspace_dir=workspace,
        permission_mode=mode,
    )
    return runtime, store, platform, session


def test_duckduckgo_provider_is_keyless_and_parses_public_results():
    requested = []

    def transport(url: str, timeout: float) -> str:
        requested.append((url, timeout))
        return """
        <html><body>
          <a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Ffresh">
            Fresh result
          </a>
          <a class="result__snippet">Current public-web information.</a>
        </body></html>
        """

    provider = DuckDuckGoWebSearchProvider(transport=transport)
    response = provider.search("latest agent frameworks", count=3)

    assert provider.provider_name == "duckduckgo"
    assert requested and requested[0][0].startswith("https://html.duckduckgo.com/html/?")
    assert response.provider == "duckduckgo"
    assert response.results[0].title == "Fresh result"
    assert response.results[0].url == "https://example.com/fresh"
    assert response.results[0].source == "example.com"
    assert "Current public-web information" in response.results[0].snippet


def test_ddg_verification_is_failure_and_parallel_batch_stops_requesting(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    calls = []
    def transport(url, timeout):
        calls.append(url)
        return '<html><form id="challenge-form">Unfortunately, bots use DuckDuckGo too.</form></html>'

    provider = DuckDuckGoWebSearchProvider(transport=transport)
    tool = next(t for t in web_search_tools(provider) if t.name == "web_search")
    context = ToolContext(session_id="test", turn_id="test", workspace=tmp_path)
    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(lambda q: tool.handler(context, {"query": q}), ["OpenAI", "ant-ling", "API docs"]))
    assert len(calls) == 1
    assert all(not r.ok and r.data["errorCode"] == "blocked" for r in results)
    assert all("No web results found" not in r.content for r in results)
    assert "verification" in provider.last_error


def test_ddg_blocked_provider_recovers_after_cooldown(monkeypatch):
    from app.agent_runtime import web_search as module

    now = [100.0]
    monkeypatch.setattr(module.time, "monotonic", lambda: now[0])
    pages = iter(['<form id="challenge-form"></form>',
                  '<a class="result__a" href="https://example.com">Recovered</a>'])
    provider = DuckDuckGoWebSearchProvider(transport=lambda *_: next(pages))
    with pytest.raises(WebSearchError):
        provider.search("OpenAI")
    now[0] += 61.0
    assert provider.search("OpenAI").results[0].title == "Recovered"
    assert provider.last_error == ""


def test_ddg_lite_fallback_parses_links_and_nested_snippets():
    calls = []
    def transport(url, timeout):
        calls.append(url)
        if "html.duckduckgo" in url:
            return '<html>Unexpected upstream page</html>'
        return '''<table><tr><td><a class="result-link" href="https://example.org/docs">API <b>Docs</b></a></td></tr>
          <tr><td class="result-snippet">Before <span>nested</span> after.</td></tr></table>'''
    response = DuckDuckGoWebSearchProvider(transport=transport).search("docs")
    assert len(calls) == 2
    assert response.results[0].title == "API Docs"
    assert response.results[0].snippet == "Before nested after."


def test_ddg_unknown_page_cannot_become_empty_success():
    provider = DuckDuckGoWebSearchProvider(transport=lambda *_: '<html>Service unavailable</html>')
    with pytest.raises(WebSearchError) as error:
        provider.search("OpenAI")
    assert error.value.code == "invalid_response"


def test_ddg_explicit_no_results_is_valid_and_does_not_retry():
    calls = []
    def transport(url, timeout):
        calls.append(url)
        return '<div class="no-results">No results found for obscure query</div>'
    assert not DuckDuckGoWebSearchProvider(transport=transport).search("obscure").results
    assert len(calls) == 1


def test_ddg_valid_empty_lite_response_overrides_html_failure():
    pages = iter(['<html>Invalid HTML response</html>', '<div class="no-results">No results found for query</div>'])
    response = DuckDuckGoWebSearchProvider(transport=lambda *_: next(pages)).search("query")
    assert response.results == ()


@pytest.mark.parametrize("factory", [BraveWebSearchProvider, TavilyWebSearchProvider])
def test_api_error_payload_is_not_zero_results(factory):
    provider = factory("test-key", transport=lambda *_: {"error": "upstream error"})
    with pytest.raises(WebSearchError):
        provider.search("OpenAI")


def test_json_transport_reports_rate_limit_without_exposing_response(monkeypatch):
    from urllib.error import HTTPError
    from app.agent_runtime import web_search as module
    class Opener:
        def open(self, *args, **kwargs):
            raise HTTPError("https://api.tavily.com/search", 429, "secret-body", {}, None)
    monkeypatch.setattr(module, "build_opener", lambda *_: Opener())
    with pytest.raises(WebSearchError) as error:
        TavilyWebSearchProvider("secret-key").search("OpenAI")
    assert error.value.code == "rate_limited"
    assert "secret" not in str(error.value)


def test_loom_shared_provider_never_sends_bridge_auth_to_remote_host():
    from app.agent_runtime.web_search import LoomWebSearchProvider
    for url in ["https://example.com/search", "http://localhost:1234/search", "http://127.0.0.1:1234/search?x=1"]:
        with pytest.raises(ValueError):
            LoomWebSearchProvider(url, "bridge-secret")


def test_shared_provider_reports_auth_and_recovers(monkeypatch):
    from app.agent_runtime import web_search as module
    from urllib.error import HTTPError
    from io import BytesIO
    class Opener:
        attempts = 0
        def open(self, request, **kwargs):
            self.attempts += 1
            assert request.get_header("Authorization") == "Bearer bridge-secret"
            if self.attempts == 1:
                raise HTTPError(request.full_url, 401, "token-details", {}, None)
            return BytesIO(json.dumps({"results": [{"title": "Docs", "url": "https://example.com", "snippet": "API", "source": "example.com"}]}).encode())
    opener = Opener()
    monkeypatch.setattr(module, "build_opener", lambda *_: opener)
    provider = module.LoomWebSearchProvider("http://127.0.0.1:1234/search", "bridge-secret")
    with pytest.raises(WebSearchError) as error:
        provider.search("docs")
    assert error.value.code == "authentication"
    assert "Sign in" in provider.last_error
    assert provider.search("docs").results[0].title == "Docs"
    assert provider.last_error == ""


def test_brave_provider_uses_fixed_endpoint_and_subscription_header():
    captured = {}

    def transport(method, url, headers, body, timeout):
        captured.update(
            method=method,
            url=url,
            headers=dict(headers),
            body=body,
            timeout=timeout,
        )
        return {
            "web": {
                "results": [
                    {
                        "title": "Official result",
                        "url": "https://example.com/a",
                        "description": "Primary snippet",
                        "extra_snippets": ["Extra context"],
                    }
                ]
            }
        }

    provider = BraveWebSearchProvider("brave-secret", transport=transport)
    response = provider.search("Loom agent runtime", count=3)

    assert captured["method"] == "GET"
    parsed = urlsplit(captured["url"])
    assert parsed.scheme == "https"
    assert parsed.netloc == "api.search.brave.com"
    assert parsed.path == "/res/v1/web/search"
    assert parse_qs(parsed.query)["q"] == ["Loom agent runtime"]
    assert parse_qs(parsed.query)["count"] == ["3"]
    assert captured["headers"]["X-Subscription-Token"] == "brave-secret"
    assert captured["body"] is None
    assert response.provider == "brave"
    assert response.results[0].source == "example.com"
    assert "Extra context" in response.results[0].snippet
    assert "brave-secret" not in json.dumps(response.to_dict())


def test_tavily_provider_uses_bearer_auth_and_parses_scores():
    captured = {}

    def transport(method, url, headers, body, timeout):
        captured.update(
            method=method,
            url=url,
            headers=dict(headers),
            body=body,
            timeout=timeout,
        )
        return {
            "request_id": "tvly-request",
            "results": [
                {
                    "title": "Tavily result",
                    "url": "https://example.org/b",
                    "content": "Processed search snippet",
                    "score": 0.91,
                }
            ],
        }

    provider = TavilyWebSearchProvider("tvly-secret", transport=transport)
    response = provider.search("current agent frameworks", count=5)

    assert captured["method"] == "POST"
    assert captured["url"] == "https://api.tavily.com/search"
    assert captured["headers"]["Authorization"] == "Bearer tvly-secret"
    payload = json.loads(captured["body"].decode("utf-8"))
    assert payload["query"] == "current agent frameworks"
    assert payload["max_results"] == 5
    assert payload["include_raw_content"] is False
    assert response.request_id == "tvly-request"
    assert response.results[0].score == 0.91
    assert "tvly-secret" not in json.dumps(response.to_dict())


def test_web_search_provider_env_detection_is_explicit_and_secret_safe():
    builtin = web_search_provider_from_env({})
    brave = web_search_provider_from_env({"BRAVE_SEARCH_API_KEY": "b-key"})
    tavily = web_search_provider_from_env({"TAVILY_API_KEY": "t-key"})
    disabled = web_search_provider_from_env({"LOOM_WEB_SEARCH_PROVIDER": "off"})

    assert builtin is not None and builtin.provider_name == "duckduckgo"
    assert brave is not None and brave.provider_name == "brave"
    assert tavily is not None and tavily.provider_name == "tavily"
    assert disabled is None

    try:
        web_search_provider_from_env({"LOOM_WEB_SEARCH_API_KEY": "ambiguous"})
    except ValueError as exc:
        assert "LOOM_WEB_SEARCH_PROVIDER" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("generic search key without provider should fail closed")


def test_auto_configured_runtime_exposes_keyless_web_search(monkeypatch, tmp_path):
    for name in (
        "LOOM_WEB_SEARCH_PROVIDER",
        "LOOM_WEB_SEARCH_API_KEY",
        "BRAVE_SEARCH_API_KEY",
        "TAVILY_API_KEY",
    ):
        monkeypatch.delenv(name, raising=False)

    runtime = WebSearchRuntime(
        platform=ScriptedPlatform([]),
        store=FileAgentSessionStore(tmp_path / "state"),
        tools=loom_default_tools(),
        sandbox_manager=SandboxManager(policy=SandboxPolicy.OFF),
        auto_configure_web_search=True,
    )
    try:
        # Assert the keys this test is actually about, not the whole payload.
        # web_search_status() grows as the settings page gains fields (it now
        # also carries choice/configured/keySource/state/reason), and an exact
        # equality here turns every addition into a spurious failure.
        status = runtime.web_search_status()
        assert status["enabled"] is True
        assert status["provider"] == "duckduckgo"
        assert status["state"] == "ready"
        assert runtime.tools.get("web_search") is not None
        assert runtime.tools.router().get("web_search") is not None
    finally:
        runtime.close()


def test_external_web_search_requires_approval_in_default_mode(tmp_path):
    provider = FakeSearchProvider()
    runtime, store, platform, session = _runtime(
        tmp_path,
        [
            ModelResponse(
                tool_calls=(
                    ToolCall(
                        call_id="search-1",
                        name="web_search",
                        arguments={"query": "latest Loom architecture", "count": 4},
                    ),
                )
            ),
            ModelResponse(text="Search completed."),
        ],
        provider,
        mode=PermissionMode.APPROVAL,
    )

    first = runtime.start_turn(session.session_id, "Search the web for the latest Loom architecture.")

    assert first.status is AgentStatus.WAITING_APPROVAL
    assert first.pending_approval is not None
    assert first.pending_approval.tool_name == "web_search"
    assert provider.calls == []

    result = runtime.resume_approval(
        session.session_id,
        "search-1",
        approved=True,
    )

    assert result.status is AgentStatus.COMPLETED
    assert provider.calls == [("latest Loom architecture", 4)]
    tool_messages = [message for message in store.load(session.session_id).messages if message.name == "web_search"]
    assert len(tool_messages) == 1
    assert "https://example.com/loom" in tool_messages[0].content
    assert any(tool.name == "web_search" for tool in platform.requests[0][1].tools)
    runtime.close()


def test_read_only_denies_web_search_without_network_call(tmp_path):
    provider = FakeSearchProvider()
    runtime, store, _, session = _runtime(
        tmp_path,
        [
            ModelResponse(
                tool_calls=(
                    ToolCall(
                        call_id="search-denied",
                        name="web_search",
                        arguments={"query": "external request"},
                    ),
                )
            ),
            ModelResponse(text="Could not search because network access is denied."),
        ],
        provider,
        mode=PermissionMode.READ_ONLY,
    )

    result = runtime.start_turn(session.session_id, "Search externally.")

    assert result.status is AgentStatus.COMPLETED
    assert provider.calls == []
    tool_messages = [message for message in store.load(session.session_id).messages if message.name == "web_search"]
    assert len(tool_messages) == 1
    assert "blocked by permissions" in tool_messages[0].content
    runtime.close()


def test_unconfigured_runtime_exposes_status_but_not_search(tmp_path):
    runtime, _, platform, session = _runtime(
        tmp_path,
        [ModelResponse(text="done")],
        None,
        mode=PermissionMode.FULL_ACCESS,
    )
    result = runtime.start_turn(session.session_id, "Report what tools are available.")

    assert result.status is AgentStatus.COMPLETED
    names = {tool.name for tool in platform.requests[0][1].tools}
    assert "web_search_status" in names
    assert "web_search" not in names
    status = runtime.web_search_status()
    assert status["enabled"] is False
    assert status["provider"] == "disabled"
    assert status["state"] == "not_configured"
    runtime.close()
