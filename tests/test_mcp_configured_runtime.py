from __future__ import annotations

from pathlib import Path

from app.agent_runtime import ConfiguredMCPRuntime, FileAgentSessionStore, ToolRegistry


class _UnusedPlatform:
    def execute_chat(self, profile_id, request):  # pragma: no cover - initialization test only
        raise AssertionError("model platform must not be called during runtime initialization")


def _runtime(tmp_path: Path, **kwargs):
    return ConfiguredMCPRuntime(
        platform=_UnusedPlatform(),
        store=FileAgentSessionStore(tmp_path),
        tools=ToolRegistry(),
        auto_configure_browser=False,
        auto_configure_web_search=False,
        **kwargs,
    )


def test_default_runtime_discovers_config_from_runtime_home(tmp_path: Path):
    runtime = _runtime(tmp_path)
    try:
        status = runtime.mcp_status()
        assert status["enabled"] is False
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
        assert status["enabled"] is False
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
        assert status["enabled"] is False
        assert status["config_path"] == ""
    finally:
        runtime.close()
