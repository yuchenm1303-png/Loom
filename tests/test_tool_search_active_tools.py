"""A search for a tool the model already holds must say so instead of coming back empty.

One real browser run spent 16 of its 76 tool calls searching for tools that were in its tool list
from the first request: the search only looked at deferred tools, so looking up a tool it already
had returned nothing or unrelated matches, and the model concluded the tool did not exist.
"""
from __future__ import annotations

import json
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
        return self.responses.pop(0)


def tool(name: str, description: str, exposure: ToolExposure) -> AgentTool:
    return AgentTool(
        name=name,
        description=description,
        input_schema={"type": "object", "properties": {}, "additionalProperties": False},
        handler=lambda _context, _arguments: ToolResult(ok=True, content=f"called:{name}"),
        exposure=exposure,
    )


def search_result(platform, request_index: int) -> dict:
    """The newest tool_search result the model saw in the given request."""
    messages = [m for m in platform.requests[request_index].messages if m.role.value == "tool" and m.name == "tool_search"]
    return json.loads(messages[-1].content)


def run_search(tmp_path: Path, *queries: str, extra_steps: int = 0):
    platform = RecordingPlatform([
        *[ModelResponse(tool_calls=(ToolCall(f"s{i}", "tool_search", {"query": query, "limit": 10}),))
          for i, query in enumerate(queries)],
        ModelResponse(text="done"),
    ])
    runtime = ToolSearchRuntime(
        platform=platform,
        store=FileAgentSessionStore(tmp_path / "state"),
        tools=ToolRegistry((
            tool("browser_open", "Open a page in the browser", ToolExposure.DIRECT),
            tool("spreadsheet_edit", "Edit spreadsheet cells", ToolExposure.DIRECT),
            tool("browser_tabs", "List the open browser tabs", ToolExposure.DEFERRED),
            tool("calendar_events", "Read calendar events", ToolExposure.DEFERRED),
        )),
        mcp_servers=(),
        auto_configure_browser=False,
        auto_configure_web_search=False,
    )
    try:
        session = runtime.create_session(AGENT_FAST_ROLE.role_id, workspace_dir=tmp_path, permission_mode=PermissionMode.FULL_ACCESS)
        assert runtime.start_turn(session.session_id, "Find tools.").status is AgentStatus.COMPLETED
    finally:
        runtime.close()
    return platform


def test_searching_for_a_tool_already_in_the_list_says_to_call_it_directly(tmp_path):
    platform = run_search(tmp_path, "spreadsheet_edit")
    result = search_result(platform, 1)
    assert result["data"]["already_available"] == ["spreadsheet_edit"]
    assert result["data"]["activated"] == []
    assert "Already in your tool list" in result["content"] and "spreadsheet_edit" in result["content"]
    assert "No tools matched" not in result["content"]
    # Nothing was activated, so the tool surface did not change.
    assert {t.name for t in platform.requests[1].tools} == {t.name for t in platform.requests[0].tools}


def test_a_mixed_match_activates_deferred_tools_and_names_the_ones_already_held(tmp_path):
    platform = run_search(tmp_path, "browser")
    result = search_result(platform, 1)
    assert result["data"]["activated"] == ["browser_tabs"]
    assert "browser_open" in result["data"]["already_available"]
    sources = {record["name"]: record["source"] for record in result["data"]["tools"]}
    assert sources["browser_open"] == "active" and sources["browser_tabs"] == "deferred"
    assert "browser_tabs" in {t.name for t in platform.requests[1].tools}


def test_repeating_a_search_reports_the_earlier_activation_as_already_available(tmp_path):
    platform = run_search(tmp_path, "browser tabs", "browser tabs")
    first, second = search_result(platform, 1), search_result(platform, 2)
    assert "browser_tabs" in first["data"]["activated"]
    assert second["data"]["activated"] == [] and "browser_tabs" in second["data"]["already_available"]


def test_a_query_nothing_matches_explains_how_tools_are_called(tmp_path):
    platform = run_search(tmp_path, "zzzz unrelated capability")
    result = search_result(platform, 1)
    assert result["data"]["count"] == 0
    assert result["content"].startswith("No tools matched")
    assert "called by name" in result["content"]
