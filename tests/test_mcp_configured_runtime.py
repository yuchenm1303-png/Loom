from __future__ import annotations

import pathlib
from pathlib import Path

import pytest

from app.agent_runtime import ConfiguredMCPRuntime, FileAgentSessionStore, ToolRegistry


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
