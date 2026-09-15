from __future__ import annotations

from pathlib import Path

from app.ai import AGENT_FAST_ROLE, ModelResponse, ToolCall
from app.agent_runtime import (
    AgentStatus,
    FileAgentSessionStore,
    PermissionMode,
    ToolRegistry,
    ToolSearchRuntime,
)
from app.connectors import ConnectorManager


class RecordingPlatform:
    def __init__(self, responses) -> None:
        self.responses = list(responses)
        self.requests = []

    def execute_chat(self, _profile_id, request):
        self.requests.append(request)
        if not self.responses:
            raise AssertionError("scripted platform ran out of responses")
        return self.responses.pop(0)


class FakeVault:
    def __init__(self) -> None:
        self.values = {"github/access-token": "connected-token"}

    def get(self, name: str) -> str:
        return self.values.get(name, "")

    def set(self, name: str, value: str) -> None:
        self.values[name] = value

    def delete(self, name: str) -> None:
        self.values.pop(name, None)


class FakeGitHubClient:
    def __init__(self, token: str) -> None:
        self.token = token

    def user(self):
        assert self.token == "connected-token"
        return {"login": "alice"}, {"X-OAuth-Scopes": "repo"}

    def request(self, method: str, path: str, *, query=None, body=None):
        _ = query
        assert self.token == "connected-token"
        if method == "POST" and path == "/repos/acme/widgets/issues":
            return {
                "number": 17,
                "title": body["title"],
                "state": "open",
                "html_url": "https://github.com/acme/widgets/issues/17",
                "user": {"login": "alice"},
                "body": body.get("body", ""),
            }, {}
        raise AssertionError(f"unexpected GitHub request: {method} {path} {query} {body}")


def _tool_names(request) -> set[str]:
    return {tool.name for tool in request.tools}


def test_connected_github_tool_is_discovered_then_executed_through_tool_search(tmp_path: Path) -> None:
    platform = RecordingPlatform(
        [
            ModelResponse(
                tool_calls=(
                    ToolCall(
                        call_id="search-github",
                        name="tool_search",
                        arguments={"query": "github create issue", "limit": 2},
                    ),
                )
            ),
            ModelResponse(
                tool_calls=(
                    ToolCall(
                        call_id="create-issue",
                        name="github_issue_create",
                        arguments={
                            "repository": "acme/widgets",
                            "title": "Connector integration",
                            "body": "Created through Loom Tool Search",
                        },
                    ),
                )
            ),
            ModelResponse(text="Issue created."),
        ]
    )
    runtime = ToolSearchRuntime(
        platform=platform,
        store=FileAgentSessionStore(tmp_path / "state"),
        tools=ToolRegistry(()),
        mcp_servers=(),
        auto_configure_browser=False,
        auto_configure_web_search=False,
    )
    manager = ConnectorManager(
        tmp_path / "home",
        vault=FakeVault(),
        environment={},
        client_factory=FakeGitHubClient,
        command_runner=lambda *args, **kwargs: type("Result", (), {"returncode": 1, "stdout": "", "stderr": ""})(),
    )
    manager.install_runtime(runtime)

    try:
        session = runtime.create_session(
            AGENT_FAST_ROLE.role_id,
            workspace_dir=tmp_path,
            permission_mode=PermissionMode.FULL_ACCESS,
        )
        result = runtime.start_turn(session.session_id, "Create the GitHub issue.")

        assert result.status is AgentStatus.COMPLETED
        assert len(platform.requests) == 3
        initial = _tool_names(platform.requests[0])
        activated = _tool_names(platform.requests[1])

        assert "tool_search" in initial
        assert "github_connection_status" in initial
        assert "github_issue_create" not in initial
        assert "github_issue_create" in activated
        assert "github_repository_get" not in activated

        persisted = runtime.get_session(session.session_id)
        issue_events = [
            item for item in persisted.messages
            if getattr(item, "name", "") == "github_issue_create"
        ]
        assert issue_events
        assert "17" in str(issue_events[-1].content)
    finally:
        runtime.close()
