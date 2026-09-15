from __future__ import annotations

import importlib
import os

import app
from app import connector_product_config


def test_product_config_install_sets_public_defaults_without_overriding_env(monkeypatch) -> None:
    monkeypatch.delenv("LOOM_GITHUB_CLIENT_ID", raising=False)
    monkeypatch.delenv("LOOM_GITHUB_OAUTH_SCOPES", raising=False)
    monkeypatch.setattr(connector_product_config, "GITHUB_CLIENT_ID", "Iv1.loom-release-client")
    monkeypatch.setattr(connector_product_config, "GITHUB_OAUTH_SCOPES", "repo read:org")

    connector_product_config.install()

    assert os.environ["LOOM_GITHUB_CLIENT_ID"] == "Iv1.loom-release-client"
    assert os.environ["LOOM_GITHUB_OAUTH_SCOPES"] == "repo read:org"

    monkeypatch.setenv("LOOM_GITHUB_CLIENT_ID", "managed-client")
    monkeypatch.setenv("LOOM_GITHUB_OAUTH_SCOPES", "repo")
    connector_product_config.install()
    assert os.environ["LOOM_GITHUB_CLIENT_ID"] == "managed-client"
    assert os.environ["LOOM_GITHUB_OAUTH_SCOPES"] == "repo"


def test_app_startup_installs_release_connector_metadata(monkeypatch) -> None:
    # `app` is already imported by pytest collection. Reloading it exercises the
    # real package startup wiring without relying on a textual source assertion.
    monkeypatch.delenv("LOOM_GITHUB_CLIENT_ID", raising=False)
    monkeypatch.delenv("LOOM_GITHUB_OAUTH_SCOPES", raising=False)
    monkeypatch.setattr(connector_product_config, "GITHUB_CLIENT_ID", "Iv1.startup-client")
    monkeypatch.setattr(connector_product_config, "GITHUB_OAUTH_SCOPES", "repo read:org")

    importlib.reload(app)

    assert os.environ["LOOM_GITHUB_CLIENT_ID"] == "Iv1.startup-client"
    assert os.environ["LOOM_GITHUB_OAUTH_SCOPES"] == "repo read:org"
