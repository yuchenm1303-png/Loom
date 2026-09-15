from __future__ import annotations

from pathlib import Path

import pytest

from app import codex_mcp_discovery, runtime_capability_defaults
from app.agent_runtime import ToolEffect, ToolExposure
from app.agent_runtime import mcp_runtime


def test_runtime_import_chain_installs_default_and_codex_mcp_patches() -> None:
    assert getattr(mcp_runtime, "_loom_mcp_backend_defaults", False) is True
    assert getattr(mcp_runtime, "_loom_codex_mcp_loader", False) is True
    assert getattr(runtime_capability_defaults._mcp_config_paths, "_loom_codex_discovery", False) is True


def test_loads_codex_stdio_and_http_mcp_without_copying_secrets(monkeypatch, tmp_path: Path) -> None:
    codex_home = tmp_path / ".codex"
    codex_home.mkdir()
    config = codex_home / "config.toml"
    config.write_text(
        """
[mcp_servers.local]
command = "python"
args = ["server.py"]
cwd = "."
env_vars = ["SHARED_TOKEN"]

[mcp_servers.local.env]
API_TOKEN = "$SHARED_TOKEN"

[mcp_servers.remote]
url = "https://mcp.example.test/v1"
bearer_token_env_var = "REMOTE_MCP_TOKEN"
tool_timeout_sec = 45
""".strip()
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("CODEX_HOME", str(codex_home))

    servers = codex_mcp_discovery.load_codex_mcp_server_configs(mcp_runtime, config)
    assert [server.name for server in servers] == ["local", "remote"]

    local = servers[0]
    assert local.transport == "stdio"
    assert local.command == "python"
    assert local.args == ("server.py",)
    assert local.env_from == (("SHARED_TOKEN", "SHARED_TOKEN"), ("API_TOKEN", "SHARED_TOKEN"))
    assert local.default_effect is ToolEffect.SENSITIVE
    assert local.exposure is ToolExposure.DIRECT

    remote = servers[1]
    assert remote.transport == "http"
    assert remote.url == "https://mcp.example.test/v1"
    assert remote.bearer_token_env == "REMOTE_MCP_TOKEN"
    assert remote.timeout_seconds == 45

    raw = config.read_text(encoding="utf-8")
    assert "REMOTE_MCP_TOKEN" in raw
    assert all("literal-secret-value" not in repr(server) for server in servers)

    # The public loader that ConfiguredMCPRuntime actually calls must route this
    # path through the Codex adapter, not only the helper used by this test.
    routed = mcp_runtime.load_mcp_server_configs(config)
    assert [server.name for server in routed] == ["local", "remote"]

    discovered = tuple(runtime_capability_defaults._mcp_config_paths(tmp_path / "loom-home"))
    assert config.resolve() in discovered


def test_rejects_literal_codex_env_values(tmp_path: Path) -> None:
    config = tmp_path / "config.toml"
    config.write_text(
        """
[mcp_servers.unsafe]
command = "server"
[mcp_servers.unsafe.env]
API_TOKEN = "literal-secret-value"
""".strip()
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(mcp_runtime.MCPConfigurationError, match="literal env value"):
        codex_mcp_discovery.load_codex_mcp_server_configs(mcp_runtime, config)


def test_rejects_codex_http_headers_instead_of_silently_dropping_them(tmp_path: Path) -> None:
    config = tmp_path / "config.toml"
    config.write_text(
        """
[mcp_servers.remote]
url = "https://mcp.example.test/v1"
http_headers = { Authorization = "Bearer literal-secret" }
""".strip()
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(mcp_runtime.MCPConfigurationError, match="http_headers"):
        codex_mcp_discovery.load_codex_mcp_server_configs(mcp_runtime, config)


def test_codex_config_is_added_only_when_it_contains_mcp_servers(monkeypatch, tmp_path: Path) -> None:
    codex_home = tmp_path / "codex"
    codex_home.mkdir()
    monkeypatch.setenv("CODEX_HOME", str(codex_home))

    config = codex_home / "config.toml"
    config.write_text('model = "gpt-5.6"\n', encoding="utf-8")
    assert codex_mcp_discovery._has_codex_mcp_servers(config) is False
    assert config.resolve() not in tuple(runtime_capability_defaults._mcp_config_paths(tmp_path / "loom-home"))

    config.write_text(
        "[mcp_servers.demo]\ncommand = \"python\"\nargs = [\"server.py\"]\n",
        encoding="utf-8",
    )
    assert codex_mcp_discovery._has_codex_mcp_servers(config) is True
    assert codex_mcp_discovery._is_codex_config(config) is True
    assert config.resolve() in tuple(runtime_capability_defaults._mcp_config_paths(tmp_path / "loom-home"))
