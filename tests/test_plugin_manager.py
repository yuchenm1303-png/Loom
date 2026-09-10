from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.plugin_manager import PluginManager, PluginManagerError


def test_plugin_manager_lists_empty_registry(tmp_path: Path) -> None:
    manager = PluginManager(tmp_path)
    assert manager.list() == []


def test_plugin_manager_discovers_local_plugin_manifest(tmp_path: Path) -> None:
    plugin_dir = tmp_path / "plugins" / "sample-plugin"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "loom-plugin.json").write_text(
        json.dumps(
            {
                "name": "sample-plugin",
                "version": "1.2.3",
                "description": "Sample Loom plugin",
            }
        ),
        encoding="utf-8",
    )

    plugins = PluginManager(tmp_path).list()

    assert len(plugins) == 1
    assert plugins[0]["name"] == "sample-plugin"
    assert plugins[0]["version"] == "1.2.3"
    assert plugins[0]["enabled"] is True
    assert plugins[0]["status"] == "Discovered"


def test_plugin_manager_installs_toggles_and_removes_plugin(tmp_path: Path) -> None:
    source = tmp_path / "plugin-source"
    source.mkdir()
    (source / "plugin.json").write_text(
        json.dumps({"name": "local-tools", "description": "Local tools"}),
        encoding="utf-8",
    )
    manager = PluginManager(tmp_path)

    installed = manager.install(str(source))["plugin"]
    assert installed["name"] == "local-tools"
    assert installed["enabled"] is True
    assert installed["status"] == "Enabled"

    disabled = manager.change("local-tools", "disable")["plugin"]
    assert disabled["enabled"] is False
    assert disabled["status"] == "Disabled"

    enabled = manager.change("local-tools", "enable")["plugin"]
    assert enabled["enabled"] is True
    assert enabled["status"] == "Enabled"

    removed = manager.change("local-tools", "remove")
    assert removed["removed"] is True
    assert manager.list() == []


def test_plugin_manager_rejects_duplicate_install_without_upgrade(tmp_path: Path) -> None:
    source = tmp_path / "plugin-source"
    source.mkdir()
    (source / "plugin.json").write_text(json.dumps({"name": "duplicate"}), encoding="utf-8")
    manager = PluginManager(tmp_path)
    manager.install(str(source))

    with pytest.raises(PluginManagerError):
        manager.install(str(source))

    upgraded = manager.install(str(source), upgrade=True, enabled=False)["plugin"]
    assert upgraded["name"] == "duplicate"
    assert upgraded["enabled"] is False
