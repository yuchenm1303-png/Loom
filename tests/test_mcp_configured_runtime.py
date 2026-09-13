from __future__ import annotations

import json
import pathlib
from pathlib import Path

import pytest

from app.ai import AGENT_FAST_ROLE
from app.agent_runtime import (
    ConfiguredMCPRuntime,
    FileAgentSessionStore,
    MCPServerConfig,
    PermissionMode,
    ToolRegistry,
)


class _UnusedPlatform:
    def execute_chat(self, profile_id, request):  # pragma: no cover - initialization test only
        raise AssertionError("model platform must not be called during runtime initialization")


@pytest.fixture(autouse=True)
def isolated_operator_configs(monkeypatch, tmp_path: Path):
    """Hide the developer's own Claude/Cursor MCP configs from these tests.

    Loom deliberately adopts MCP servers already configured in Claude Desktop or
    Cursor (see ``app/runtime_capability_defaults.py``). That is a feature, but
    it makes discovery depend on what the machine happens to have installed:
    without this, the same commit passes on a bare CI box and fails on any
    workstation with Claude Desktop. These tests are about Loom's *own*
    discovery order, so the host's configs are pointed somewhere empty.
    """
    elsewhere = tmp_path / "not-a-real-home"
    elsewhere.mkdir()
    monkeypatch.delenv("LOOM_CONFIG", raising=False)
    monkeypatch.setenv("APPDATA", str(elsewhere))
    monkeypatch.setattr(pathlib.Path, "home", classmethod(lambda cls: elsewhere))


def _runtime(tmp_path: Path, **kwargs):
    return ConfiguredMCPRuntime(
        platform=_UnusedPlatform(),
        store=FileAgentSessionStore(tmp_path),
        tools=ToolRegistry(),
        auto_configure_browser=False,
        auto_configure_web_search=False,
        **kwargs,
    )


def _step(runtime: ConfiguredMCPRuntime, root: Path):
    workspace = root / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    session = runtime.create_session(
        AGENT_FAST_ROLE.role_id,
        workspace_dir=workspace,
        permission_mode=PermissionMode.APPROVAL,
    )
    return runtime._build_step_context(session, next_model_step=True)


# "enabled" now means the MCP subsystem is available, which it always is: Loom
# ships local MCP tools of its own. Whether any server was discovered moved to
# "configured", so that is what these assert.
def test_default_runtime_discovers_config_from_runtime_home(tmp_path: Path):
    runtime = _runtime(tmp_path)
    try:
        status = runtime.mcp_status()
        assert status["configured"] is False
        assert status["config_path"] == str((tmp_path / "config.toml").resolve())
    finally:
        runtime.close()


def test_default_runtime_honors_loom_config_environment(monkeypatch, tmp_path: Path):
    selected = tmp_path / "operator.toml"
    selected.write_text("[mcp_servers]\n", encoding="utf-8")
    monkeypatch.setenv("LOOM_CONFIG", str(selected))

    runtime = _runtime(tmp_path)
    try:
        status = runtime.mcp_status()
        assert status["configured"] is False
        assert status["config_path"] == str(selected.resolve())
    finally:
        runtime.close()


def test_explicit_empty_server_list_disables_auto_discovery(monkeypatch, tmp_path: Path):
    selected = tmp_path / "would-have-been-loaded.toml"
    selected.write_text(
        "[mcp_servers.bad]\ntransport='stdio'\ncommand='missing-command'\nrequired=true\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("LOOM_CONFIG", str(selected))

    runtime = _runtime(tmp_path, mcp_servers=())
    try:
        status = runtime.mcp_status()
        assert status["configured"] is False
        assert status["config_path"] == ""
    finally:
        runtime.close()


def test_step_mcp_binding_snapshot_never_contains_resolved_secret(monkeypatch, tmp_path: Path):
    secret = "synthetic-mcp-secret-value"
    monkeypatch.setenv("LOOM_TEST_MCP_SECRET", secret)
    config = MCPServerConfig(
        name="demo",
        transport="stdio",
        command="python",
        args=("server.py",),
        env_from=(("API_KEY", "LOOM_TEST_MCP_SECRET"),),
    )
    runtime = _runtime(
        tmp_path / "state",
        mcp_servers=(config,),
        auto_connect_mcp=False,
    )
    try:
        step = _step(runtime, tmp_path / "project")
        raw = step.request_state.mcp_binding_json
        payload = json.loads(raw)

        assert secret not in raw
        assert payload["servers"][0]["name"] == "demo"
        assert payload["servers"][0]["transport"] == "stdio"
        assert len(payload["servers"][0]["config_sha256"]) == 64
    finally:
        runtime.close()


def test_mcp_config_identity_changes_frozen_request_digest(tmp_path: Path):
    first_config = MCPServerConfig(
        name="demo",
        transport="stdio",
        command="python",
        args=("server-a.py",),
    )
    second_config = MCPServerConfig(
        name="demo",
        transport="stdio",
        command="python",
        args=("server-b.py",),
    )
    first = _runtime(
        tmp_path / "state-a",
        mcp_servers=(first_config,),
        auto_connect_mcp=False,
    )
    second = _runtime(
        tmp_path / "state-b",
        mcp_servers=(second_config,),
        auto_connect_mcp=False,
    )
    try:
        first_step = _step(first, tmp_path / "project-a")
        second_step = _step(second, tmp_path / "project-b")

        assert first_step.request_state.mcp_binding_json != second_step.request_state.mcp_binding_json
        assert first_step.request_state.digest() != second_step.request_state.digest()
    finally:
        first.close()
        second.close()


def test_transient_mcp_connected_state_does_not_change_binding_identity(monkeypatch, tmp_path: Path):
    config = MCPServerConfig(
        name="demo",
        transport="stdio",
        command="python",
        args=("server.py",),
    )
    runtime = _runtime(
        tmp_path / "state",
        mcp_servers=(config,),
        auto_connect_mcp=False,
    )
    try:
        def status(connected: bool):
            return {
                "enabled": True,
                "sdk_available": True,
                "connected_servers": 1 if connected else 0,
                "tool_count": 0,
                "servers": [
                    {
                        "name": "demo",
                        "transport": "stdio",
                        "connected": connected,
                        "protocol_version": "2026-07-28",
                        "server_info": "demo-server/1",
                        "tool_count": 0,
                        "error": "" if connected else "temporary disconnect",
                    }
                ],
            }

        monkeypatch.setattr(runtime.mcp_clients, "status", lambda: status(True))
        connected_snapshot = runtime._mcp_binding_snapshot()
        monkeypatch.setattr(runtime.mcp_clients, "status", lambda: status(False))
        disconnected_snapshot = runtime._mcp_binding_snapshot()

        assert connected_snapshot == disconnected_snapshot
    finally:
        runtime.close()
