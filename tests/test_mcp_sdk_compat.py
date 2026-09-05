from __future__ import annotations

import sys
from pathlib import Path

import pytest

mcp = pytest.importorskip("mcp")

from mcp.server import MCPServer

from app.agent_runtime import (
    AgentStatus,
    FileAgentSessionStore,
    MCPClientManager,
    MCPRuntime,
    MCPServerConfig,
    PermissionMode,
    ToolContext,
    ToolEffect,
    ToolRegistry,
)
from app.ai import AGENT_FAST_ROLE, ModelResponse, ToolCall


class _ScriptedPlatform:
    def __init__(self, responses):
        self.responses = list(responses)

    def execute_chat(self, _profile_id, _request):
        if not self.responses:
            raise AssertionError("scripted platform ran out of responses")
        return self.responses.pop(0)


def test_mcp_v2_inprocess_tools_list_and_call_round_trip(tmp_path: Path):
    server = MCPServer(
        "Loom MCP Test",
        instructions="Use echo for deterministic compatibility checks.",
    )

    @server.tool()
    def echo(text: str) -> str:
        """Echo one string."""
        return f"echo:{text}"

    manager = MCPClientManager(
        (
            MCPServerConfig(
                name="demo",
                transport="stdio",
                command="ignored-by-inprocess-test",
                default_effect=ToolEffect.READ_ONLY,
            ),
        ),
        target_factory=lambda _config: server,
    )
    try:
        manager.connect()
        tools = manager.agent_tools()

        assert len(tools) == 1
        tool = tools[0]
        assert tool.name == "mcp.demo.echo"
        assert tool.effect is ToolEffect.READ_ONLY
        assert "Echo one string" in tool.description
        assert "Server guidance" in tool.description
        assert tool.input_schema["type"] == "object"
        assert tool.input_schema["properties"]["text"]["type"] == "string"

        result = tool.handler(
            ToolContext(
                session_id="session-1",
                turn_id="turn-1",
                workspace=tmp_path,
            ),
            {"text": "hello"},
        )
        assert result.ok is True
        assert "echo:hello" in result.content
        assert result.data == {
            "mcp": True,
            "is_error": False,
            "server": "demo",
            "tool": "echo",
        }

        status = manager.status()
        assert status["sdk_available"] is True
        assert status["connected_servers"] == 1
        assert status["tool_count"] == 1
        assert status["servers"][0]["connected"] is True
        assert status["servers"][0]["protocol_version"]
    finally:
        manager.close()


def test_mcp_v2_real_stdio_subprocess_round_trip(tmp_path: Path):
    fixture = Path(__file__).parent / "fixtures" / "mcp_stdio_server.py"
    manager = MCPClientManager(
        (
            MCPServerConfig(
                name="stdio",
                transport="stdio",
                command=sys.executable,
                args=(str(fixture),),
                default_effect=ToolEffect.READ_ONLY,
                timeout_seconds=20.0,
            ),
        )
    )
    try:
        manager.connect()
        tools = manager.agent_tools()
        assert [tool.name for tool in tools] == ["mcp.stdio.echo"]

        result = tools[0].handler(
            ToolContext(
                session_id="session-stdio",
                turn_id="turn-stdio",
                workspace=tmp_path,
            ),
            {"text": "process"},
        )
        assert result.ok is True
        assert "stdio:process" in result.content
    finally:
        manager.close()


def test_mcp_sensitive_tool_still_crosses_loom_approval_boundary(tmp_path: Path):
    calls: list[str] = []
    server = MCPServer("Approval fixture")

    @server.tool()
    def external_write(value: str) -> str:
        """Simulate a write to an external system."""
        calls.append(value)
        return f"wrote:{value}"

    platform = _ScriptedPlatform(
        [
            ModelResponse(
                tool_calls=(
                    ToolCall(
                        call_id="mcp-call-1",
                        name="mcp.approval.external_write",
                        arguments={"value": "approved"},
                    ),
                )
            ),
            ModelResponse(text="done"),
        ]
    )
    runtime = MCPRuntime(
        platform=platform,
        store=FileAgentSessionStore(tmp_path / "state"),
        tools=ToolRegistry(),
        mcp_servers=(
            MCPServerConfig(
                name="approval",
                transport="stdio",
                command="ignored-by-inprocess-test",
                default_effect=ToolEffect.SENSITIVE,
            ),
        ),
        mcp_target_factory=lambda _config: server,
        auto_configure_browser=False,
        auto_configure_web_search=False,
    )
    try:
        session = runtime.create_session(
            AGENT_FAST_ROLE.role_id,
            workspace_dir=tmp_path,
            permission_mode=PermissionMode.APPROVAL,
        )

        waiting = runtime.start_turn(session.session_id, "Write to the external system.")
        assert waiting.status is AgentStatus.WAITING_APPROVAL
        assert waiting.pending_approval is not None
        assert waiting.pending_approval.call_id == "mcp-call-1"
        assert waiting.pending_approval.tool_name == "mcp.approval.external_write"
        assert waiting.pending_approval.effect is ToolEffect.SENSITIVE
        assert calls == []

        completed = runtime.resume_approval(
            session.session_id,
            "mcp-call-1",
            approved=True,
        )
        assert completed.status is AgentStatus.COMPLETED
        assert completed.final_text == "done"
        assert calls == ["approved"]
    finally:
        runtime.close()
