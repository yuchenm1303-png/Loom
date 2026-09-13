from __future__ import annotations

from app.agent_runtime.contracts import ToolEffect
from app.agent_runtime.mcp_runtime import (
    MCPClientManager,
    MCPServerConfig,
    MCPToolDescriptor,
    _ConnectedServer,
)
from app.agent_runtime.tools import ToolExposure


class _Runner:
    pass


def _descriptor() -> MCPToolDescriptor:
    return MCPToolDescriptor(
        canonical_name="mcp.demo.echo",
        server_name="demo",
        remote_name="echo",
        description="[MCP:demo] echo",
        input_schema={"type": "object", "properties": {}},
        effect=ToolEffect.READ_ONLY,
        exposure=ToolExposure.DIRECT,
    )


def test_prepared_call_freezes_config_and_server_metadata():
    original_config = MCPServerConfig(
        name="demo",
        transport="stdio",
        command="old-python",
        timeout_seconds=30,
    )
    connected = _ConnectedServer(
        config=original_config,
        client=object(),
        descriptors=(_descriptor(),),
        protocol_version="v1",
        server_info="demo@old",
    )
    manager = MCPClientManager(())
    manager._runner = _Runner()
    manager._servers["demo"] = connected

    first_binding = manager.capture_binding()
    prepared = first_binding.prepare_call("demo", "echo")
    assert prepared is not None

    connected.config = MCPServerConfig(
        name="demo",
        transport="stdio",
        command="new-python",
        timeout_seconds=1,
    )
    connected.protocol_version = "v2"
    connected.server_info = "demo@new"

    second_binding = manager.capture_binding()

    assert prepared.config is original_config
    assert prepared.config.command == "old-python"
    assert prepared.server_metadata.protocol_version == "v1"
    assert prepared.server_metadata.server_info == "demo@old"
    assert second_binding is not first_binding
