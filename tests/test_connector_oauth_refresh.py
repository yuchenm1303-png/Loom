from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from app.connector_oauth_refresh import ConnectorOAuthStateStore, RefreshingConnectorManager
from app.connectors import ConnectorError, GitHubAPIError


class FakeVault:
    def __init__(self, values: dict[str, str] | None = None) -> None:
        self.values = dict(values or {})

    def get(self, name: str) -> str:
        return self.values.get(name, "")

    def set(self, name: str, value: str) -> None:
        self.values[name] = value

    def delete(self, name: str) -> None:
        self.values.pop(name, None)


@dataclass
class FakeCommandResult:
    returncode: int = 1
    stdout: str = ""
    stderr: str = ""


class FakeGitHubClient:
    def __init__(self, token: str) -> None:
        self.token = token

    def user(self):
        if self.token.startswith(("expired", "bad")):
            raise GitHubAPIError(401, "Bad credentials")
        return {"login": "alice"}, {"X-OAuth-Scopes": "repo, read:org"}


class OAuthFixture:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.refresh_count = 0

    def __call__(self, url: str, payload) -> dict[str, object]:
        body = dict(payload)
        self.calls.append((url, body))
        if url.endswith("/login/device/code"):
            return {
                "device_code": "device-secret",
                "user_code": "ABCD-EFGH",
                "verification_uri": "https://github.com/login/device",
                "expires_in": 900,
                "interval": 5,
            }
        grant = str(body.get("grant_type") or "")
        if grant == "urn:ietf:params:oauth:grant-type:device_code":
            return {
                "access_token": "oauth-access-1",
                "expires_in": 28_800,
                "refresh_token": "oauth-refresh-1",
                "refresh_token_expires_in": 15_897_600,
                "token_type": "bearer",
            }
        if grant == "refresh_token":
            self.refresh_count += 1
            return {
                "access_token": f"oauth-access-refresh-{self.refresh_count}",
                "expires_in": 28_800,
                "refresh_token": f"oauth-refresh-{self.refresh_count + 1}",
                "refresh_token_expires_in": 15_897_600,
                "token_type": "bearer",
            }
        raise AssertionError(f"unexpected OAuth payload: {body}")


def _manager(
    tmp_path: Path,
    *,
    vault: FakeVault | None = None,
    wall: list[float] | None = None,
    monotonic: list[float] | None = None,
    oauth: OAuthFixture | None = None,
) -> RefreshingConnectorManager:
    resolved_wall = wall or [1_800_000_000.0]
    resolved_mono = monotonic or [100.0]
    return RefreshingConnectorManager(
        tmp_path / "home",
        vault=vault or FakeVault(),
        environment={"LOOM_GITHUB_CLIENT_ID": "Iv1.loom-test-client"},
        client_factory=FakeGitHubClient,
        command_runner=lambda *args, **kwargs: FakeCommandResult(),
        clock=lambda: resolved_mono[0],
        wall_clock=lambda: resolved_wall[0],
        oauth_post=oauth or OAuthFixture(),
    )


def _complete_device_login(manager: RefreshingConnectorManager):
    started = manager.start_github_auth()
    assert started["mode"] == "device"
    assert started["status"] == "pending"
    return manager.poll_github_auth(str(started["sessionId"]))


def test_device_flow_keeps_refresh_token_only_in_vault(tmp_path: Path) -> None:
    vault = FakeVault()
    wall = [1_800_000_000.0]
    oauth = OAuthFixture()
    manager = _manager(tmp_path, vault=vault, wall=wall, oauth=oauth)

    completed = _complete_device_login(manager)
    assert completed["status"] == "connected"
    assert vault.values["github/access-token"] == "oauth-access-1"
    assert vault.values["github/refresh-token"] == "oauth-refresh-1"

    status = manager.github_status()
    assert status["connected"] is True
    assert status["credentialSource"] == "device-oauth-keyring"
    assert status["refreshable"] is True
    assert 28_000 <= int(status["accessTokenExpiresIn"]) <= 28_800
    assert "oauth-access-1" not in repr(status)
    assert "oauth-refresh-1" not in repr(status)

    durable = (tmp_path / "home" / "connector-oauth.json").read_text(encoding="utf-8")
    assert "oauth-access-1" not in durable
    assert "oauth-refresh-1" not in durable
    assert "accessExpiresAt" in durable
    assert "refreshExpiresAt" in durable


def test_new_step_rotates_access_token_before_expiration(tmp_path: Path) -> None:
    vault = FakeVault()
    wall = [1_800_000_000.0]
    oauth = OAuthFixture()
    manager = _manager(tmp_path, vault=vault, wall=wall, oauth=oauth)
    _complete_device_login(manager)
    old_binding = manager.github_status()["bindingId"]

    # GitHub's expiring access token is now inside Loom's five-minute safety
    # window. A future Step refresh check rotates it before sampling tools.
    wall[0] += 28_800 - 120
    changed = manager.refresh_if_store_changed()

    assert changed is True
    assert oauth.refresh_count == 1
    assert vault.values["github/access-token"] == "oauth-access-refresh-1"
    assert vault.values["github/refresh-token"] == "oauth-refresh-2"
    assert manager.github_status()["bindingId"] != old_binding
    assert manager.github_status()["connected"] is True


def test_restart_recovers_when_access_expired_but_refresh_token_is_valid(tmp_path: Path) -> None:
    wall = [1_800_000_000.0]
    vault = FakeVault(
        {
            "github/access-token": "expired-access",
            "github/refresh-token": "oauth-refresh-old",
        }
    )
    store = ConnectorOAuthStateStore(tmp_path / "home")
    store.set_github_expiry(
        access_expires_at=wall[0] - 10,
        refresh_expires_at=wall[0] + 10_000,
    )
    oauth = OAuthFixture()

    manager = _manager(tmp_path, vault=vault, wall=wall, oauth=oauth)

    assert oauth.refresh_count == 1
    assert manager.github_status()["connected"] is True
    assert vault.values["github/access-token"] == "oauth-access-refresh-1"
    assert vault.values["github/refresh-token"] == "oauth-refresh-2"


def test_manual_pat_breaks_old_device_refresh_chain(tmp_path: Path) -> None:
    vault = FakeVault()
    manager = _manager(tmp_path, vault=vault)
    _complete_device_login(manager)
    assert "github/refresh-token" in vault.values

    status = manager.connect_token("github_pat_manual")

    assert status["connected"] is True
    assert status["credentialSource"] == "keyring"
    assert status["refreshable"] is False
    assert vault.values["github/access-token"] == "github_pat_manual"
    assert "github/refresh-token" not in vault.values
    assert not (tmp_path / "home" / "connector-oauth.json").exists()


def test_invalid_pat_preserves_existing_device_oauth_chain(tmp_path: Path) -> None:
    vault = FakeVault()
    manager = _manager(tmp_path, vault=vault)
    _complete_device_login(manager)
    before_status = manager.github_status()
    before_metadata = (tmp_path / "home" / "connector-oauth.json").read_text(encoding="utf-8")

    with pytest.raises(GitHubAPIError, match="Bad credentials"):
        manager.connect_token("bad-pat")

    assert vault.values["github/access-token"] == "oauth-access-1"
    assert vault.values["github/refresh-token"] == "oauth-refresh-1"
    assert (tmp_path / "home" / "connector-oauth.json").read_text(encoding="utf-8") == before_metadata
    after_status = manager.github_status()
    assert after_status["connected"] is True
    assert after_status["bindingId"] == before_status["bindingId"]
    assert after_status["refreshable"] is True


def test_failed_gh_import_preserves_existing_device_oauth_chain(tmp_path: Path) -> None:
    vault = FakeVault()
    manager = _manager(tmp_path, vault=vault)
    _complete_device_login(manager)
    before_status = manager.github_status()
    before_metadata = (tmp_path / "home" / "connector-oauth.json").read_text(encoding="utf-8")
    manager._gh_token = lambda: ""  # type: ignore[method-assign]

    with pytest.raises(ConnectorError, match="not authenticated"):
        manager.import_github_cli()

    assert vault.values["github/access-token"] == "oauth-access-1"
    assert vault.values["github/refresh-token"] == "oauth-refresh-1"
    assert (tmp_path / "home" / "connector-oauth.json").read_text(encoding="utf-8") == before_metadata
    after_status = manager.github_status()
    assert after_status["connected"] is True
    assert after_status["bindingId"] == before_status["bindingId"]


def test_disconnect_clears_access_and_refresh_credentials(tmp_path: Path) -> None:
    vault = FakeVault()
    manager = _manager(tmp_path, vault=vault)
    _complete_device_login(manager)

    status = manager.disconnect_github()

    assert status["connected"] is False
    assert status["enabled"] is False
    assert "github/access-token" not in vault.values
    assert "github/refresh-token" not in vault.values
    assert not (tmp_path / "home" / "connector-oauth.json").exists()
