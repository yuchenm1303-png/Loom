from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.agent_runtime.mcp_runtime import (
    MCPCatalogChangedError,
    MCPClientManager,
    MCPServerConfig,
    MCPToolDescriptor,
    MCPUnavailableError,
    McpBinding,
    _ConnectedServer,
)
from app.agent_runtime.contracts import ToolEffect
from app.agent_runtime.tools import ToolContext, ToolExposure


class _Runner:
    def run(self, coroutine, *, timeout: float, cancel_check=None):
        if cancel_check is not None and cancel_check():
            raise RuntimeError("agent turn cancellation requested")
        return asyncio.run(coroutine)


class _Client:
    def __init__(self, label: str) -> None:
        self.label = label
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.listed_tools: list[object] = []

    async def call_tool(self, name: str, arguments: dict[str, object]):
        self.calls.append((name, dict(arguments)))
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text=f"{self.label}:{name}")],
            structured_content=None,
            is_error=False,
        )

    async def list_tools(self):
        return SimpleNamespace(tools=list(self.listed_tools))



def _descriptor(*, schema_type: str = "string") -> MCPToolDescriptor:
    return MCPToolDescriptor(
        canonical_name="mcp.demo.echo",
        server_name="demo",
        remote_name="echo",
        description="[MCP:demo] echo",
        input_schema={
            "type": "object",
            "properties": {"value": {"type": schema_type}},
        },
        effect=ToolEffect.READ_ONLY,
        exposure=ToolExposure.DIRECT,
    )



def _connected(label: str = "old") -> _ConnectedServer:
    return _ConnectedServer(
        config=MCPServerConfig(name="demo", transport="stdio", command="python"),
        client=_Client(label),
        descriptors=(_descriptor(),),
        protocol_version="2026-07-28",
        server_info=f"demo@{label}",
    )



def _manager(connected: _ConnectedServer) -> MCPClientManager:
    manager = MCPClientManager(())
    manager._runner = _Runner()
    manager._servers["demo"] = connected
    return manager



def test_same_catalog_reuses_same_binding_object():
    manager = _manager(_connected())

    first = manager.capture_binding()
    second = manager.capture_binding()

    assert first is second



def test_catalog_revision_change_creates_new_binding():
    connected = _connected()
    manager = _manager(connected)
    first = manager.capture_binding()

    with connected.catalog_lock:
        connected.catalog_revision += 1
        connected.descriptors = (_descriptor(schema_type="integer"),)

    second = manager.capture_binding()

    assert second is not first
    assert second.tools()[0].input_schema["properties"]["value"]["type"] == "integer"



def test_stale_prepared_call_rejects_before_preparation_runs():
    connected = _connected()
    manager = _manager(connected)
    prepared = manager.capture_binding().prepare_call("demo", "echo")
    assert prepared is not None
    prepared_side_effects: list[str] = []

    with connected.catalog_lock:
        connected.catalog_revision += 1

    with pytest.raises(MCPCatalogChangedError, match="catalog changed"):
        prepared.call_with_preparation(
            {"value": "x"},
            prepare=lambda: prepared_side_effects.append("ran"),
        )

    assert prepared_side_effects == []
    assert connected.client.calls == []



def test_old_binding_never_reroutes_to_replacement_client():
    old = _connected("old")
    manager = _manager(old)
    old_binding = manager.capture_binding()
    old_call = old_binding.prepare_call("demo", "echo")
    assert old_call is not None

    new = _connected("new")
    manager._servers["demo"] = new
    manager._invalidate_binding_cache()
    new_call = manager.capture_binding().prepare_call("demo", "echo")
    assert new_call is not None

    old_result = old_call.call({"value": "before"})
    new_result = new_call.call({"value": "after"})

    assert old_result.content == "old:echo"
    assert new_result.content == "new:echo"
    assert old.client.calls == [("echo", {"value": "before"})]
    assert new.client.calls == [("echo", {"value": "after"})]



def test_captured_agent_tool_handler_uses_exact_prepared_client(tmp_path: Path):
    old = _connected("old")
    manager = _manager(old)
    tool = manager.agent_tools()[0]

    manager._servers["demo"] = _connected("new")
    manager._invalidate_binding_cache()

    result = tool.handler(
        ToolContext(session_id="s", turn_id="t", workspace=tmp_path),
        {"value": "x"},
    )

    assert result.content == "old:echo"
    assert manager._servers["demo"].client.calls == []



def test_closed_captured_client_fails_instead_of_rerouting():
    old = _connected("old")
    manager = _manager(old)
    prepared = manager.capture_binding().prepare_call("demo", "echo")
    assert prepared is not None

    manager._servers["demo"] = _connected("new")
    manager._invalidate_binding_cache()
    with old.catalog_lock:
        old.closed = True

    with pytest.raises(MCPUnavailableError, match="shut down"):
        prepared.call({"value": "x"})

    assert manager._servers["demo"].client.calls == []



def test_server_and_tool_identity_are_part_of_prepared_call():
    binding = McpBinding((_connected(),), runner=_Runner(), max_result_chars=20_000)

    assert binding.prepare_call("demo", "echo") is not None
    assert binding.prepare_call("demo", "other") is None
    assert binding.prepare_call("other", "echo") is None



def test_binding_identity_does_not_embed_runtime_secret(monkeypatch):
    monkeypatch.setenv("LOOM_MCP_SECRET", "super-secret-value")
    connected = _ConnectedServer(
        config=MCPServerConfig(
            name="demo",
            transport="stdio",
            command="python",
            env_from=(("API_KEY", "LOOM_MCP_SECRET"),),
        ),
        client=_Client("old"),
        descriptors=(_descriptor(),),
    )

    binding = McpBinding((connected,), runner=_Runner(), max_result_chars=20_000)

    assert "super-secret-value" not in binding.identity
    assert "super-secret-value" not in repr(binding.tools())
