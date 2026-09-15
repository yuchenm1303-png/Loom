from __future__ import annotations

from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import urlopen

import pytest

from app.connector_web_oauth import WebOAuthConnectorManager
from app.connectors import ConnectorError


class FakeVault:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def get(self, name: str) -> str:
        return self.values.get(name, "")

    def set(self, name: str, value: str) -> None:
        self.values[name] = value

    def delete(self, name: str) -> None:
        self.values.pop(name, None)


class FakeGitHubClient:
    def __init__(self, token: str) -> None:
        self.token = token

    def user(self):
        if not self.token or self.token == "bad":
            raise RuntimeError("invalid credential")
        return {"login": "alice"}, {"X-OAuth-Scopes": "repo, read:org"}


class FakeCommandResult:
    returncode = 1
    stdout = ""
    stderr = ""


def _manager(
    tmp_path: Path,
    *,
    vault: FakeVault | None = None,
    token_response: dict | None = None,
    refresh_response: dict | None = None,
) -> tuple[WebOAuthConnectorManager, FakeVault, list[tuple[str, dict[str, object]]]]:
    resolved_vault = vault or FakeVault()
    calls: list[tuple[str, dict[str, object]]] = []
    initial = token_response or {
        "access_token": "web-access-token",
        "token_type": "bearer",
        "scope": "repo read:org",
        "expires_in": 28800,
        "refresh_token": "web-refresh-token",
        "refresh_token_expires_in": 15897600,
    }
    refreshed = refresh_response or {
        "access_token": "web-access-token-2",
        "token_type": "bearer",
        "scope": "repo read:org",
        "expires_in": 28800,
        "refresh_token": "web-refresh-token-2",
        "refresh_token_expires_in": 15897600,
    }

    def web_post(url: str, payload):
        calls.append((url, dict(payload)))
        return dict(initial)

    def oauth_post(url: str, payload):
        calls.append((url, dict(payload)))
        return dict(refreshed)

    manager = WebOAuthConnectorManager(
        tmp_path / "home",
        vault=resolved_vault,
        environment={
            "LOOM_GITHUB_CLIENT_ID": "loom-native-client",
            "LOOM_GITHUB_CLIENT_SECRET": "loom-public-native-secret",
            "LOOM_GITHUB_OAUTH_SCOPES": "repo read:org",
            "LOOM_GITHUB_CALLBACK_PATH": "/oauth/github/callback",
        },
        client_factory=FakeGitHubClient,
        command_runner=lambda *args, **kwargs: FakeCommandResult(),
        web_oauth_post=web_post,
        oauth_post=oauth_post,
    )
    return manager, resolved_vault, calls


def _authorize(manager: WebOAuthConnectorManager, *, state_override: str = ""):
    started = manager.start_github_auth()
    assert started["mode"] == "web"
    assert started["status"] == "pending"
    authorize_url = str(started["authorizationUrl"])
    query = parse_qs(urlsplit(authorize_url).query)
    state = state_override or query["state"][0]
    callback = str(started["redirectUrl"]) + "?" + urlencode({"code": "github-code", "state": state})
    with urlopen(callback, timeout=3) as response:
        assert response.status == 200
        assert b"Authorization received" in response.read()
    return started, query


def test_web_oauth_uses_loopback_pkce_and_never_puts_secret_in_browser_url(tmp_path: Path) -> None:
    manager, vault, calls = _manager(tmp_path)

    started, query = _authorize(manager)
    authorize_url = str(started["authorizationUrl"])
    redirect = urlsplit(str(started["redirectUrl"]))

    assert redirect.scheme == "http"
    assert redirect.hostname == "127.0.0.1"
    assert redirect.port is not None and redirect.port > 0
    assert redirect.path == "/oauth/github/callback"
    assert query["client_id"] == ["loom-native-client"]
    assert query["code_challenge_method"] == ["S256"]
    assert len(query["code_challenge"][0]) >= 43
    assert "loom-public-native-secret" not in authorize_url
    assert "code_verifier" not in query

    completed = manager.poll_github_auth(started["sessionId"])
    assert completed["status"] == "connected"
    assert completed["connector"]["account"] == "alice"
    assert completed["connector"]["credentialSource"] == "web-oauth-keyring"
    assert vault.values["github/access-token"] == "web-access-token"
    assert vault.values["github/refresh-token"] == "web-refresh-token"

    _url, exchange = calls[0]
    assert exchange["client_id"] == "loom-native-client"
    assert exchange["client_secret"] == "loom-public-native-secret"
    assert exchange["code"] == "github-code"
    assert exchange["redirect_uri"] == started["redirectUrl"]
    assert len(str(exchange["code_verifier"])) >= 43
    assert str(exchange["code_verifier"]) not in authorize_url
    assert "github-code" not in repr(completed)
    assert "web-access-token" not in repr(completed)


def test_web_oauth_rejects_loopback_callback_with_wrong_state(tmp_path: Path) -> None:
    manager, vault, calls = _manager(tmp_path)
    started, _query = _authorize(manager, state_override="attacker-state")

    with pytest.raises(ConnectorError, match="state did not match"):
        manager.poll_github_auth(started["sessionId"])

    assert "github/access-token" not in vault.values
    assert calls == []


def test_web_oauth_refresh_includes_client_secret_and_rotates_keyring_tokens(tmp_path: Path) -> None:
    manager, vault, calls = _manager(tmp_path)
    started, _query = _authorize(manager)
    connected = manager.poll_github_auth(started["sessionId"])
    assert connected["status"] == "connected"
    assert manager.github_status()["refreshable"] is True

    refreshed = manager._refresh_oauth_token()
    assert refreshed["connected"] is True
    assert refreshed["credentialSource"] == "web-oauth-keyring"
    assert vault.values["github/access-token"] == "web-access-token-2"
    assert vault.values["github/refresh-token"] == "web-refresh-token-2"

    _url, refresh = calls[-1]
    assert refresh["grant_type"] == "refresh_token"
    assert refresh["client_id"] == "loom-native-client"
    assert refresh["client_secret"] == "loom-public-native-secret"
    assert refresh["refresh_token"] == "web-refresh-token"


def test_status_marks_web_oauth_unavailable_without_client_secret(tmp_path: Path) -> None:
    manager = WebOAuthConnectorManager(
        tmp_path / "home",
        vault=FakeVault(),
        environment={"LOOM_GITHUB_CLIENT_ID": "client-only"},
        client_factory=FakeGitHubClient,
        command_runner=lambda *args, **kwargs: FakeCommandResult(),
    )

    status = manager.github_status()
    assert status["webOAuthAvailable"] is False
    assert status["deviceFlowAvailable"] is True
    assert status["preferredBrowserLogin"] == "device"
