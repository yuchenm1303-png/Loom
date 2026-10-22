import pytest

from app.ai import AGENT_FAST_ROLE, ModelResponse, ToolCall
from app.agent_runtime import (
    AgentStatus, AgentTool, CodeModeRuntime, FileAgentSessionStore, PermissionMode,
    ToolExposure, ToolRegistry, ToolResult, ToolSearchRuntime,
)
from app.agent_runtime.tool_selection import parse_tool_selection
from test_app_server import _build_service, _wait_until
from test_tool_search_runtime import RecordingPlatform


def tool(name, calls, exposure=ToolExposure.DIRECT):
    return AgentTool(name=name, description="test tool", input_schema={"type": "object", "properties": {}},
                     handler=lambda _context, _args: (calls.append(name) or ToolResult(ok=True, content="ok")),
                     exposure=exposure)


@pytest.mark.parametrize("value", [False, "shell", {}, [None], [""], ["x" * 257]])
def test_selection_rejects_invalid_values(value):
    with pytest.raises(ValueError):
        parse_tool_selection(value)


def test_catalog_and_turn_selection_roundtrip(tmp_path):
    calls = []
    service, runtime, store, platform, _ = _build_service(tmp_path, [ModelResponse(text="done")], tools=(
        tool("read_file", calls), tool("hidden", calls, ToolExposure.HIDDEN)))
    try:
        thread = service.thread_start({})["thread"]
        catalog = service.tools_catalog({"threadId": thread["id"]})
        names = {entry["name"] for entry in catalog["tools"]}
        assert "read_file" in names and "hidden" not in names
        service.turn_start({"threadId": thread["id"], "input": "Just talk", "toolSelection": []})
        _wait_until(lambda: not service._is_active(thread["id"]))
        assert not platform.requests[0].tools
        assert store.load(thread["id"]).tool_selection == []
        assert service.thread_read({"threadId": thread["id"]})["thread"]["toolSelection"] == []
    finally:
        runtime.close()


def make_runtime(tmp_path, selection, responses, runtime_class=ToolSearchRuntime):
    calls = []
    platform = RecordingPlatform(responses)
    runtime = runtime_class(platform=platform, store=FileAgentSessionStore(tmp_path / "state"),
        tools=ToolRegistry((tool("read_file", calls), tool("secret", calls, ToolExposure.DEFERRED))),
        mcp_servers=(), auto_configure_browser=False, auto_configure_web_search=False)
    session = runtime.create_session(AGENT_FAST_ROLE.role_id, workspace_dir=tmp_path, permission_mode=PermissionMode.FULL_ACCESS)
    session.tool_selection = selection
    runtime.store.save(session)
    return runtime, session, platform, calls


def test_explicit_deferred_selection_is_exposed_and_unselected_tool_cannot_run(tmp_path):
    runtime, session, platform, calls = make_runtime(tmp_path, ["secret"], [
        ModelResponse(tool_calls=(ToolCall(call_id="blocked", name="read_file", arguments={}),)),
        ModelResponse(tool_calls=(ToolCall(call_id="selected", name="secret", arguments={}),)),
        ModelResponse(text="done"),
    ])
    try:
        assert runtime.start_turn(session.session_id, "work").status is AgentStatus.COMPLETED
        assert {definition.name for definition in platform.requests[0].tools} == {"secret"}
        assert calls == ["secret"]
    finally:
        runtime.close()


def test_search_cannot_reactivate_unselected_deferred_tools(tmp_path):
    runtime, session, platform, calls = make_runtime(tmp_path, ["tool_search"], [
        ModelResponse(tool_calls=(ToolCall(call_id="search", name="tool_search", arguments={"query": "secret"}),)),
        ModelResponse(tool_calls=(ToolCall(call_id="direct", name="secret", arguments={}),)),
        ModelResponse(text="done"),
    ])
    try:
        runtime.start_turn(session.session_id, "work")
        assert calls == []
        assert all({definition.name for definition in request.tools} == {"tool_search"} for request in platform.requests)
    finally:
        runtime.close()


def test_code_mode_cannot_invoke_unselected_nested_tools(tmp_path):
    runtime, session, _platform, calls = make_runtime(tmp_path, ["code_mode"], [
        ModelResponse(tool_calls=(ToolCall(call_id="code", name="code_mode", arguments={"code": "tools.read_file()"}),)),
        ModelResponse(text="done"),
    ], runtime_class=CodeModeRuntime)
    try:
        runtime.start_turn(session.session_id, "work")
        assert calls == []
    finally:
        runtime.close()


def test_auto_preserves_default_discovery(tmp_path):
    runtime, session, platform, _ = make_runtime(tmp_path, None, [ModelResponse(text="done")])
    try:
        runtime.start_turn(session.session_id, "work")
        names = {definition.name for definition in platform.requests[0].tools}
        assert "read_file" in names and "tool_search" in names and "secret" not in names
    finally:
        runtime.close()
