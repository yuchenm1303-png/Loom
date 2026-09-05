from __future__ import annotations

from pathlib import Path

from app.agent_runtime import (
    AgentStatus,
    AgentTool,
    FileAgentSessionStore,
    PermissionMode,
    ToolExposure,
    ToolRegistry,
    ToolResult,
    ToolSearchRuntime,
)
from app.ai import AGENT_FAST_ROLE, ModelResponse, ToolCall


class RecordingPlatform:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def execute_chat(self, _profile_id, request):
        self.requests.append(request)
        if not self.responses:
            raise AssertionError("scripted platform ran out of responses")
        return self.responses.pop(0)


def _tool(name: str, description: str, *, exposure: ToolExposure, calls: list[str] | None = None):
    def handler(_context, arguments):
        value = str(arguments.get("value") or "")
        if calls is not None:
            calls.append(value)
        return ToolResult(ok=True, content=f"called:{name}:{value}")

    return AgentTool(
        name=name,
        description=description,
        input_schema={
            "type": "object",
            "properties": {"value": {"type": "string"}},
            "required": ["value"],
            "additionalProperties": False,
        },
        handler=handler,
        exposure=exposure,
    )


def _tool_names(request) -> set[str]:
    return {tool.name for tool in request.tools}


def test_registry_search_only_considers_deferred_tools():
    registry = ToolRegistry(
        (
            _tool(
                "mcp.github.create_issue",
                "Create a GitHub issue",
                exposure=ToolExposure.DEFERRED,
            ),
            _tool(
                "hidden.github.create_issue",
                "Create a GitHub issue",
                exposure=ToolExposure.HIDDEN,
            ),
            _tool(
                "code.github.create_issue",
                "Create a GitHub issue",
                exposure=ToolExposure.CODE_MODE_ONLY,
            ),
        )
    )

    matches = registry.search_deferred("github create issue", limit=10)

    assert [tool.name for tool in matches] == ["mcp.github.create_issue"]
    assert registry.router().get("mcp.github.create_issue") is None
    assert registry.router(activated_names=("mcp.github.create_issue",)).get("mcp.github.create_issue") is not None
    assert registry.router(activated_names=("hidden.github.create_issue",)).get("hidden.github.create_issue") is None


def test_tool_search_exposes_only_matching_deferred_tool_for_current_turn(tmp_path: Path):
    calls: list[str] = []
    target = _tool(
        "mcp.github.create_issue",
        "Create a GitHub issue in a repository",
        exposure=ToolExposure.DEFERRED,
        calls=calls,
    )
    unrelated = _tool(
        "mcp.github.delete_repository",
        "Delete a GitHub repository",
        exposure=ToolExposure.DEFERRED,
    )
    platform = RecordingPlatform(
        [
            ModelResponse(
                tool_calls=(
                    ToolCall(
                        call_id="search-1",
                        name="tool_search",
                        arguments={"query": "github create issue", "limit": 1},
                    ),
                )
            ),
            ModelResponse(
                tool_calls=(
                    ToolCall(
                        call_id="call-1",
                        name="mcp.github.create_issue",
                        arguments={"value": "bug"},
                    ),
                )
            ),
            ModelResponse(text="first turn done"),
            ModelResponse(text="second turn done"),
        ]
    )
    runtime = ToolSearchRuntime(
        platform=platform,
        store=FileAgentSessionStore(tmp_path / "state"),
        tools=ToolRegistry((target, unrelated)),
        mcp_servers=(),
        auto_configure_browser=False,
        auto_configure_web_search=False,
    )
    try:
        session = runtime.create_session(
            AGENT_FAST_ROLE.role_id,
            workspace_dir=tmp_path,
            permission_mode=PermissionMode.FULL_ACCESS,
        )

        first = runtime.start_turn(session.session_id, "Create an issue.")
        assert first.status is AgentStatus.COMPLETED
        assert calls == ["bug"]
        assert len(platform.requests) == 3

        initial_names = _tool_names(platform.requests[0])
        searched_names = _tool_names(platform.requests[1])
        assert "tool_search" in initial_names
        assert "mcp.github.create_issue" not in initial_names
        assert "mcp.github.delete_repository" not in initial_names
        assert "mcp.github.create_issue" in searched_names
        assert "mcp.github.delete_repository" not in searched_names

        second = runtime.start_turn(session.session_id, "Say done without tools.")
        assert second.status is AgentStatus.COMPLETED
        second_turn_names = _tool_names(platform.requests[3])
        assert "tool_search" in second_turn_names
        assert "mcp.github.create_issue" not in second_turn_names
    finally:
        runtime.close()


def test_deferred_approval_can_resume_after_runtime_restart(tmp_path: Path):
    calls: list[str] = []
    sensitive = _tool(
        "mcp.external.write_record",
        "Write a record to an external service",
        exposure=ToolExposure.DEFERRED,
        calls=calls,
    )
    # Replace the default read-only effect with sensitive while preserving the handler/schema.
    sensitive = AgentTool(
        name=sensitive.name,
        description=sensitive.description,
        input_schema=sensitive.input_schema,
        handler=sensitive.handler,
        effect="sensitive",
        exposure=sensitive.exposure,
    )
    store = FileAgentSessionStore(tmp_path / "state")
    first_platform = RecordingPlatform(
        [
            ModelResponse(
                tool_calls=(
                    ToolCall(
                        call_id="search-write",
                        name="tool_search",
                        arguments={"query": "external write record", "limit": 1},
                    ),
                )
            ),
            ModelResponse(
                tool_calls=(
                    ToolCall(
                        call_id="write-1",
                        name="mcp.external.write_record",
                        arguments={"value": "approved-value"},
                    ),
                )
            ),
        ]
    )
    runtime1 = ToolSearchRuntime(
        platform=first_platform,
        store=store,
        tools=ToolRegistry((sensitive,)),
        mcp_servers=(),
        auto_configure_browser=False,
        auto_configure_web_search=False,
    )
    session = runtime1.create_session(
        AGENT_FAST_ROLE.role_id,
        workspace_dir=tmp_path,
        permission_mode=PermissionMode.APPROVAL,
    )
    waiting = runtime1.start_turn(session.session_id, "Write the record.")
    assert waiting.status is AgentStatus.WAITING_APPROVAL
    assert calls == []
    runtime1.close()

    second_platform = RecordingPlatform([ModelResponse(text="approved and done")])
    runtime2 = ToolSearchRuntime(
        platform=second_platform,
        store=store,
        tools=ToolRegistry((sensitive,)),
        mcp_servers=(),
        auto_configure_browser=False,
        auto_configure_web_search=False,
    )
    try:
        completed = runtime2.resume_approval(
            session.session_id,
            "write-1",
            approved=True,
        )
        assert completed.status is AgentStatus.COMPLETED
        assert completed.final_text == "approved and done"
        assert calls == ["approved-value"]
        assert "mcp.external.write_record" in _tool_names(second_platform.requests[0])
    finally:
        runtime2.close()


def test_tool_search_runtime_defers_mcp_direct_tools_by_default(tmp_path: Path):
    direct_mcp = _tool(
        "mcp.demo.echo",
        "Echo through MCP",
        exposure=ToolExposure.DIRECT,
    )
    runtime = ToolSearchRuntime(
        platform=RecordingPlatform([]),
        store=FileAgentSessionStore(tmp_path / "state"),
        tools=ToolRegistry((direct_mcp,)),
        mcp_servers=(),
        auto_configure_browser=False,
        auto_configure_web_search=False,
    )
    try:
        assert runtime.tools.get("mcp.demo.echo").exposure is ToolExposure.DEFERRED
        assert runtime.tools.router().get("mcp.demo.echo") is None
        assert runtime.tools.router().get("tool_search") is not None
    finally:
        runtime.close()
