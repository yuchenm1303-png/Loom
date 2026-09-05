from __future__ import annotations

from pathlib import Path

import pytest

from app.agent_runtime import (
    MCPConfigurationError,
    MCPServerConfig,
    ToolEffect,
    ToolExposure,
    canonical_mcp_tool_name,
    load_mcp_server_configs,
)


def test_canonical_mcp_tool_name_is_stable_and_model_safe():
    assert canonical_mcp_tool_name("Git Hub", "issues/create") == "mcp.Git_Hub.issues_create"
    assert canonical_mcp_tool_name("123 server", "123 tool").startswith("mcp._123_server._123_tool")
    assert len(canonical_mcp_tool_name("s" * 200, "t" * 200)) <= 128


def test_mcp_server_config_defaults_to_sensitive_direct_tools(monkeypatch):
    monkeypatch.setenv("LOOM_TEST_MCP_KEY", "runtime-secret")
    config = MCPServerConfig(
        name="demo",
        transport="stdio",
        command="python",
        args=("server.py",),
        env_from=(("API_KEY", "LOOM_TEST_MCP_KEY"),),
    )

    assert config.default_effect is ToolEffect.SENSITIVE
    assert config.exposure is ToolExposure.DIRECT
    assert config.resolved_env() == {"API_KEY": "runtime-secret"}


def test_mcp_server_config_supports_per_tool_effect_overrides():
    config = MCPServerConfig(
        name="demo",
        transport="stdio",
        command="python",
        tool_effects=(("search", ToolEffect.READ_ONLY),),
    )

    assert config.effect_for("search") is ToolEffect.READ_ONLY
    assert config.effect_for("delete") is ToolEffect.SENSITIVE


def test_http_mcp_requires_https_except_loopback():
    with pytest.raises(MCPConfigurationError):
        MCPServerConfig(name="bad", transport="http", url="http://example.com/mcp")

    assert MCPServerConfig(name="local", transport="http", url="http://127.0.0.1:9000/mcp").url
    assert MCPServerConfig(name="secure", transport="http", url="https://example.com/mcp").url


def test_load_mcp_server_configs_uses_environment_references_not_secret_values(tmp_path: Path):
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        """
[mcp_servers.github]
transport = "stdio"
command = "npx"
args = ["-y", "example-mcp"]
default_effect = "sensitive"

[mcp_servers.github.env_from]
GITHUB_TOKEN = "LOOM_GITHUB_TOKEN"

[mcp_servers.github.tool_effects]
search_issues = "read_only"

[mcp_servers.docs]
transport = "http"
url = "https://mcp.example.com/mcp"
bearer_token_env = "LOOM_DOCS_MCP_TOKEN"
required = true
""".strip(),
        encoding="utf-8",
    )

    configs = load_mcp_server_configs(config_path)

    assert [item.name for item in configs] == ["github", "docs"]
    github = configs[0]
    docs = configs[1]
    assert github.args == ("-y", "example-mcp")
    assert github.env_from == (("GITHUB_TOKEN", "LOOM_GITHUB_TOKEN"),)
    assert github.effect_for("search_issues") is ToolEffect.READ_ONLY
    assert docs.bearer_token_env == "LOOM_DOCS_MCP_TOKEN"
    assert docs.required is True


def test_load_mcp_server_configs_rejects_inline_credentials(tmp_path: Path):
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        """
[mcp_servers.bad]
transport = "stdio"
command = "python"
env = { API_KEY = "do-not-put-secrets-here" }
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(MCPConfigurationError, match="must not store credential values"):
        load_mcp_server_configs(config_path)


def test_missing_config_file_means_no_mcp_servers(tmp_path: Path):
    assert load_mcp_server_configs(tmp_path / "missing.toml") == ()
