from __future__ import annotations

import sys
from pathlib import Path

import pytest

mcp = pytest.importorskip("mcp")

from mcp.server import MCPServer

from app.agent_runtime import MCPClientManager, MCPServerConfig, ToolContext, ToolEffect


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
