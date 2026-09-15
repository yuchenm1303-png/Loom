from __future__ import annotations

import importlib
import os

import app
from app import connector_product_config


def test_product_config_install_sets_native_oauth_defaults_without_overriding_env(monkeypatch) -> None:
    for name in (
        "LOOM_GITHUB_CLIENT_ID",
        "LOOM_GITHUB_CLIENT_SECRET",
        "LOOM_GITHUB_OAUTH_SCOPES",
        "LOOM_GITHUB_CALLBACK_PATH",
    ):
        monkeypatch.delenv(name, raising=False)

    monkeypatch.setattr(connector_product_config, "GITHUB_CLIENT_ID", "Iv1.loom-release-client")
    monkeypatch.setattr(connector_product_config, "GITHUB_CLIENT_SECRET", "native-public-credential")
    monkeypatch.setattr(connector_product_config, "GITHUB_OAUTH_SCOPES", "repo read:org")
    monkeypatch.setattr(connector_product_config, "GITHUB_OAUTH_CALLBACK_PATH", "/oauth/github/callback")

    connector_product_config.install()

    assert os.environ["LOOM_GITHUB_CLIENT_ID"] == "Iv1.loom-release-client"
    assert os.environ["LOOM_GITHUB_CLIENT_SECRET"] == "native-public-credential"
    assert os.environ["LOOM_GITHUB_OAUTH_SCOPES"] == "repo read:org"
    assert os.environ["LOOM_GITHUB_CALLBACK_PATH"] == "/oauth/github/callback"

    monkeypatch.setenv("LOOM_GITHUB_CLIENT_ID", "managed-client")
    monkeypatch.setenv("LOOM_GITHUB_CLIENT_SECRET", "managed-secret")
    monkeypatch.setenv("LOOM_GITHUB_OAUTH_SCOPES", "repo")
    monkeypatch.setenv("LOOM_GITHUB_CALLBACK_PATH", "/managed/callback")
    connector_product_config.install()
    assert os.environ["LOOM_GITHUB_CLIENT_ID"] == "managed-client"
    assert os.environ["LOOM_GITHUB_CLIENT_SECRET"] == "managed-secret"
    assert os.environ["LOOM_GITHUB_OAUTH_SCOPES"] == "repo"
    assert os.environ["LOOM_GITHUB_CALLBACK_PATH"] == "/managed/callback"


def test_app_startup_installs_release_connector_metadata(monkeypatch) -> None:
    # `app` is already imported by pytest collection. Reloading it exercises the
    # real package startup wiring without relying on a textual source assertion.
    for name in (
        "LOOM_GITHUB_CLIENT_ID",
        "LOOM_GITHUB_CLIENT_SECRET",
        "LOOM_GITHUB_OAUTH_SCOPES",
        "LOOM_GITHUB_CALLBACK_PATH",
    ):
        monkeypatch.delenv(name, raising=False)

    monkeypatch.setattr(connector_product_config, "GITHUB_CLIENT_ID", "Iv1.startup-client")
    monkeypatch.setattr(connector_product_config, "GITHUB_CLIENT_SECRET", "startup-native-credential")
    monkeypatch.setattr(connector_product_config, "GITHUB_OAUTH_SCOPES", "repo read:org")
    monkeypatch.setattr(connector_product_config, "GITHUB_OAUTH_CALLBACK_PATH", "/oauth/github/callback")

    importlib.reload(app)

    assert os.environ["LOOM_GITHUB_CLIENT_ID"] == "Iv1.startup-client"
    assert os.environ["LOOM_GITHUB_CLIENT_SECRET"] == "startup-native-credential"
    assert os.environ["LOOM_GITHUB_OAUTH_SCOPES"] == "repo read:org"
    assert os.environ["LOOM_GITHUB_CALLBACK_PATH"] == "/oauth/github/callback"
