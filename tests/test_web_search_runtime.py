from __future__ import annotations

import json
from urllib.parse import parse_qs, urlsplit

from app.agent_runtime import (
    AgentRuntime,
    AgentStatus,
    BraveWebSearchProvider,
    FileAgentSessionStore,
    PermissionMode,
    SandboxManager,
    SandboxPolicy,
    TavilyWebSearchProvider,
    WebSearchResponse,
    WebSearchResult,
    web_search_provider_from_env,
)
from app.agent_runtime.workspace_tools import loom_default_tools
from app.ai import AGENT_FAST_ROLE, ModelResponse, ToolCall


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
    runtime = AgentRuntime(
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
    brave = web_search_provider_from_env({"BRAVE_SEARCH_API_KEY": "b-key"})
    tavily = web_search_provider_from_env({"TAVILY_API_KEY": "t-key"})
    disabled = web_search_provider_from_env({"LOOM_WEB_SEARCH_PROVIDER": "off"})

    assert brave is not None and brave.provider_name == "brave"
    assert tavily is not None and tavily.provider_name == "tavily"
    assert disabled is None

    try:
        web_search_provider_from_env({"LOOM_WEB_SEARCH_API_KEY": "ambiguous"})
    except ValueError as exc:
        assert "LOOM_WEB_SEARCH_PROVIDER" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("generic search key without provider should fail closed")


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
    assert runtime.web_search_status() == {"enabled": False, "provider": "disabled"}
    runtime.close()
