from __future__ import annotations

from loom_app_server import _sanitize_desktop_bridge_environment


def test_desktop_bridge_secret_is_removed_before_runtime_imports(tmp_path, monkeypatch):
    token = "browser-bridge-secret-" + "x" * 40
    electron_home = tmp_path / "electron-user-data"
    loom_home = tmp_path / "loom-home"
    monkeypatch.setenv("LOOM_DESKTOP_PYTHON", "python.exe")
    monkeypatch.setenv("LOOM_HOME", str(electron_home))
    monkeypatch.setenv("LOOM_BROWSER_EXTENSION_TOKEN", token)

    _sanitize_desktop_bridge_environment(fallback_home=loom_home)

    assert "LOOM_BROWSER_EXTENSION_TOKEN" not in __import__("os").environ
    assert "LOOM_HOME" not in __import__("os").environ
    assert (loom_home / "browser" / "current-tab-bridge.token").read_text(encoding="utf-8") == token


def test_non_desktop_launch_does_not_rewrite_runtime_environment(tmp_path, monkeypatch):
    token = "operator-token-" + "y" * 40
    configured_home = tmp_path / "operator-home"
    monkeypatch.delenv("LOOM_DESKTOP_PYTHON", raising=False)
    monkeypatch.setenv("LOOM_HOME", str(configured_home))
    monkeypatch.setenv("LOOM_BROWSER_EXTENSION_TOKEN", token)

    _sanitize_desktop_bridge_environment(fallback_home=tmp_path / "unused")

    import os

    assert os.environ["LOOM_HOME"] == str(configured_home)
    assert os.environ["LOOM_BROWSER_EXTENSION_TOKEN"] == token
    assert not (tmp_path / "unused" / "browser" / "current-tab-bridge.token").exists()
