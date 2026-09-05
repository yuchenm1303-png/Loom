from __future__ import annotations

from pathlib import Path

import pytest

from app.agent_runtime import (
    AgentEventKind,
    AgentStatus,
    AgentTool,
    CodeModeError,
    CodeModeInterpreter,
    CodeModeRuntime,
    FileAgentSessionStore,
    PermissionMode,
    ToolContext,
    ToolEffect,
    ToolExposure,
    ToolRegistry,
    ToolResult,
)
from app.ai import AGENT_FAST_ROLE, MessageRole, ModelResponse, ToolCall


class RecordingPlatform:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def execute_chat(self, _profile_id, request):
        self.requests.append(request)
        if not self.responses:
            raise AssertionError("scripted platform ran out of responses")
        return self.responses.pop(0)


def _tool(name, handler, *, effect=ToolEffect.READ_ONLY, exposure=ToolExposure.DIRECT, description=None):
    return AgentTool(
        name=name,
        description=description or f"Test tool {name}",
        input_schema={
            "type": "object",
            "properties": {"value": {"type": "string"}},
            "required": ["value"],
            "additionalProperties": False,
        },
        handler=handler,
        effect=effect,
        exposure=exposure,
    )


def _runtime(tmp_path: Path, platform, tools, *, permission_mode=PermissionMode.WORKSPACE):
    runtime = CodeModeRuntime(
        platform=platform,
        store=FileAgentSessionStore(tmp_path / "state"),
        tools=ToolRegistry(tuple(tools)),
        mcp_servers=(),
        auto_configure_browser=False,
        auto_configure_web_search=False,
    )
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    session = runtime.create_session(
        AGENT_FAST_ROLE.role_id,
        workspace_dir=workspace,
        permission_mode=permission_mode,
    )
    return runtime, session


def _tool_output_text(request) -> str:
    return "\n".join(
        str(message.content)
        for message in request.messages
        if message.role is MessageRole.TOOL
    )


def test_interpreter_rejects_imports_and_arbitrary_python():
    interpreter = CodeModeInterpreter()
    with pytest.raises(CodeModeError, match="unsupported code_mode statement"):
        interpreter.execute("import os\nemit(os.getcwd())", invoke_tool=lambda _name, _args: {})

    with pytest.raises(CodeModeError, match="arbitrary method and attribute"):
        interpreter.execute(
            "x = 'abc'.upper()\nemit(x)",
            invoke_tool=lambda _name, _args: {},
        )


def test_code_mode_composes_two_tools_in_one_model_tool_round_trip(tmp_path: Path):
    def echo(_context: ToolContext, arguments):
        return ToolResult(ok=True, content=f"echo:{arguments['value']}")

    platform = RecordingPlatform(
        [
            ModelResponse(
                tool_calls=(
                    ToolCall(
                        call_id="code-1",
                        name="code_mode",
                        arguments={
                            "code": (
                                "a = tools.echo(value='one')\n"
                                "b = tools.echo(value='two')\n"
                                "emit(a['content'] + '|' + b['content'])"
                            )
                        },
                    ),
                )
            ),
            ModelResponse(text="finished"),
        ]
    )
    runtime, session = _runtime(tmp_path, platform, [_tool("echo", echo)])
    try:
        result = runtime.start_turn(session.session_id, "Run two echoes efficiently.")

        assert result.status is AgentStatus.COMPLETED
        assert result.final_text == "finished"
        assert len(platform.requests) == 2
        assert "echo:one|echo:two" in _tool_output_text(platform.requests[1])

        events = runtime.store.events(session.session_id)
        nested_requested = [
            event
            for event in events
            if event.kind is AgentEventKind.TOOL_REQUESTED and event.data.get("nested")
        ]
        assert [event.data["tool"] for event in nested_requested] == ["echo", "echo"]
        stored = runtime.get_session(session.session_id)
        nested_tool_messages = [
            message
            for message in stored.messages
            if message.role is MessageRole.TOOL and message.name == "echo"
        ]
        assert nested_tool_messages == []
    finally:
        runtime.close()


def test_code_mode_only_tool_is_nested_but_not_direct_model_visible(tmp_path: Path):
    def nested(_context: ToolContext, arguments):
        return ToolResult(ok=True, content=f"nested:{arguments['value']}")

    platform = RecordingPlatform(
        [
            ModelResponse(
                tool_calls=(
                    ToolCall(
                        call_id="code-2",
                        name="code_mode",
                        arguments={
                            "code": "r = tools.nested_echo(value='ok')\nemit(r['content'])"
                        },
                    ),
                )
            ),
            ModelResponse(text="done"),
        ]
    )
    runtime, session = _runtime(
        tmp_path,
        platform,
        [_tool("nested_echo", nested, exposure=ToolExposure.CODE_MODE_ONLY)],
    )
    try:
        result = runtime.start_turn(session.session_id, "Use the nested-only helper.")

        assert result.status is AgentStatus.COMPLETED
        first_tool_names = {definition.name for definition in platform.requests[0].tools}
        assert "code_mode" in first_tool_names
        assert "nested_echo" not in first_tool_names
        assert "nested:ok" in _tool_output_text(platform.requests[1])
    finally:
        runtime.close()


def test_code_mode_never_bypasses_interactive_approval(tmp_path: Path):
    called = []

    def mutate(_context: ToolContext, arguments):
        called.append(arguments["value"])
        return ToolResult(ok=True, content="mutated")

    platform = RecordingPlatform(
        [
            ModelResponse(
                tool_calls=(
                    ToolCall(
                        call_id="code-3",
                        name="code_mode",
                        arguments={
                            "code": "r = tools.mutate(value='blocked')\nemit(r)"
                        },
                    ),
                )
            ),
            ModelResponse(text="I need approval outside Code Mode."),
        ]
    )
    runtime, session = _runtime(
        tmp_path,
        platform,
        [_tool("mutate", mutate, effect=ToolEffect.MUTATING)],
        permission_mode=PermissionMode.APPROVAL,
    )
    try:
        result = runtime.start_turn(session.session_id, "Try the mutation.")

        assert result.status is AgentStatus.COMPLETED
        assert called == []
        assert "requires_approval" in _tool_output_text(platform.requests[1])
        assert runtime.get_session(session.session_id).pending_approval is None
        denied = [
            event
            for event in runtime.store.events(session.session_id)
            if event.kind is AgentEventKind.TOOL_DENIED
            and event.data.get("source") == "code_mode_requires_approval"
        ]
        assert len(denied) == 1
    finally:
        runtime.close()


def test_code_mode_workspace_permission_allows_mutating_nested_tool(tmp_path: Path):
    called = []

    def mutate(_context: ToolContext, arguments):
        called.append(arguments["value"])
        return ToolResult(ok=True, content="mutated")

    platform = RecordingPlatform(
        [
            ModelResponse(
                tool_calls=(
                    ToolCall(
                        call_id="code-4",
                        name="code_mode",
                        arguments={"code": "r = tools.mutate(value='allowed')\nemit(r['content'])"},
                    ),
                )
            ),
            ModelResponse(text="done"),
        ]
    )
    runtime, session = _runtime(
        tmp_path,
        platform,
        [_tool("mutate", mutate, effect=ToolEffect.MUTATING)],
        permission_mode=PermissionMode.WORKSPACE,
    )
    try:
        result = runtime.start_turn(session.session_id, "Do the allowed mutation.")
        assert result.status is AgentStatus.COMPLETED
        assert called == ["allowed"]
        assert "mutated" in _tool_output_text(platform.requests[1])
    finally:
        runtime.close()


def test_code_mode_read_only_permission_denies_mutation(tmp_path: Path):
    called = []

    def mutate(_context: ToolContext, arguments):
        called.append(arguments["value"])
        return ToolResult(ok=True, content="mutated")

    platform = RecordingPlatform(
        [
            ModelResponse(
                tool_calls=(
                    ToolCall(
                        call_id="code-5",
                        name="code_mode",
                        arguments={"code": "r = tools.mutate(value='no')\nemit(r['content'])"},
                    ),
                )
            ),
            ModelResponse(text="blocked as expected"),
        ]
    )
    runtime, session = _runtime(
        tmp_path,
        platform,
        [_tool("mutate", mutate, effect=ToolEffect.MUTATING)],
        permission_mode=PermissionMode.READ_ONLY,
    )
    try:
        result = runtime.start_turn(session.session_id, "Do not bypass read-only.")
        assert result.status is AgentStatus.COMPLETED
        assert called == []
        assert "blocked by permissions" in _tool_output_text(platform.requests[1])
    finally:
        runtime.close()


def test_code_mode_can_activate_deferred_tool_then_call_it_in_same_cell(tmp_path: Path):
    def deferred(_context: ToolContext, arguments):
        return ToolResult(ok=True, content=f"deferred:{arguments['value']}")

    platform = RecordingPlatform(
        [
            ModelResponse(
                tool_calls=(
                    ToolCall(
                        call_id="code-6",
                        name="code_mode",
                        arguments={
                            "code": (
                                "tools.tool_search(query='special deferred echo')\n"
                                "r = tools.deferred_echo(value='activated')\n"
                                "emit(r['content'])"
                            )
                        },
                    ),
                )
            ),
            ModelResponse(text="done"),
        ]
    )
    runtime, session = _runtime(
        tmp_path,
        platform,
        [
            _tool(
                "deferred_echo",
                deferred,
                exposure=ToolExposure.DEFERRED,
                description="Special deferred echo capability",
            )
        ],
    )
    try:
        result = runtime.start_turn(session.session_id, "Find and use the deferred helper.")
        assert result.status is AgentStatus.COMPLETED
        assert "deferred:activated" in _tool_output_text(platform.requests[1])
        first_names = {definition.name for definition in platform.requests[0].tools}
        assert "deferred_echo" not in first_names
    finally:
        runtime.close()


def test_hidden_tool_cannot_be_called_from_code_mode(tmp_path: Path):
    called = []

    def hidden(_context: ToolContext, arguments):
        called.append(arguments["value"])
        return ToolResult(ok=True, content="hidden")

    platform = RecordingPlatform(
        [
            ModelResponse(
                tool_calls=(
                    ToolCall(
                        call_id="code-7",
                        name="code_mode",
                        arguments={"code": "r = tools.hidden_echo(value='x')\nemit(r['content'])"},
                    ),
                )
            ),
            ModelResponse(text="done"),
        ]
    )
    runtime, session = _runtime(
        tmp_path,
        platform,
        [_tool("hidden_echo", hidden, exposure=ToolExposure.HIDDEN)],
    )
    try:
        result = runtime.start_turn(session.session_id, "Try the hidden helper.")
        assert result.status is AgentStatus.COMPLETED
        assert called == []
        assert "Invalid or unavailable nested tool request" in _tool_output_text(platform.requests[1])
    finally:
        runtime.close()
