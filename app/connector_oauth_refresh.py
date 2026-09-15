from __future__ import annotations

"""Refresh-token lifecycle for first-party OAuth connectors.

The base GitHub connector intentionally keeps credential discovery and tool
binding small. This layer adds expiring device-flow token rotation without
putting refresh tokens or access tokens into Loom's durable JSON state.

GitHub device-flow access tokens may expire (commonly after eight hours). When
GitHub returns a refresh token, Loom stores it in the same OS credential vault
as the access token and persists only non-secret expiration timestamps. A new
model Step refreshes shortly before expiration; an already sampled Step keeps
its original immutable AgentTool/client binding.
"""

import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any, Callable, Mapping

from app.connectors import ConnectorError, ConnectorManager


_GITHUB_ACCESS_TOKEN_URL = "https://github.com/login/oauth/access_token"
_KEYRING_GITHUB_REFRESH_TOKEN = "github/refresh-token"
_OAUTH_STATE_VERSION = 1
_REFRESH_SKEW_SECONDS = 300
_DEVICE_GRANT = "urn:ietf:params:oauth:grant-type:device_code"
_REFRESH_GRANT = "refresh_token"


class ConnectorOAuthStateStore:
    """Secret-free OAuth expiry metadata.

    This file deliberately contains no token material. It exists only so a
    restarted packaged desktop knows when the keychain-backed access token is
    nearing expiry and can rotate it before the next sampled Step.
    """

    def __init__(self, runtime_home: str | Path) -> None:
        self.runtime_home = Path(runtime_home).expanduser().resolve()
        self.runtime_home.mkdir(parents=True, exist_ok=True)
        self.path = self.runtime_home / "connector-oauth.json"

    def snapshot(self) -> dict[str, Any]:
        raw: dict[str, Any] = {}
        if self.path.is_file():
            try:
                parsed = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(parsed, dict):
                    raw = parsed
            except (OSError, ValueError):
                raw = {}
        github = raw.get("github") if isinstance(raw.get("github"), dict) else {}
        return {
            "schemaVersion": _OAUTH_STATE_VERSION,
            "github": {
                "kind": str(github.get("kind") or ""),
                "accessExpiresAt": max(0.0, float(github.get("accessExpiresAt") or 0.0)),
                "refreshExpiresAt": max(0.0, float(github.get("refreshExpiresAt") or 0.0)),
            },
        }

    def set_github_expiry(
        self,
        *,
        access_expires_at: float = 0.0,
        refresh_expires_at: float = 0.0,
    ) -> dict[str, Any]:
        payload = {
            "schemaVersion": _OAUTH_STATE_VERSION,
            "github": {
                "kind": "device-oauth",
                "accessExpiresAt": max(0.0, float(access_expires_at)),
                "refreshExpiresAt": max(0.0, float(refresh_expires_at)),
            },
        }
        self._write(payload)
        return payload

    def clear_github(self) -> None:
        try:
            self.path.unlink()
        except FileNotFoundError:
            return
        except OSError:
            # Metadata is not authority. A stale timestamp may cause one extra
            # refresh attempt, but it can never restore a disconnected account.
            return

    def _write(self, payload: dict[str, Any]) -> None:
        self.runtime_home.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(prefix="connector-oauth-", suffix=".json", dir=self.runtime_home)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(payload, stream, ensure_ascii=False, indent=2, sort_keys=True)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            try:
                os.chmod(temp_name, 0o600)
            except OSError:
                pass
            os.replace(temp_name, self.path)
            try:
                os.chmod(self.path, 0o600)
            except OSError:
                pass
        finally:
            try:
                if os.path.exists(temp_name):
                    os.unlink(temp_name)
            except OSError:
                pass


OAuthPost = Callable[[str, Mapping[str, Any]], dict[str, Any]]


class RefreshingConnectorManager(ConnectorManager):
    """ConnectorManager with rotating GitHub device-flow credentials."""

    def __init__(
        self,
        runtime_home: str | Path,
        *,
        wall_clock: Callable[[], float] | None = None,
        oauth_post: OAuthPost | None = None,
        **kwargs: Any,
    ) -> None:
        self.oauth_state = ConnectorOAuthStateStore(runtime_home)
        self.wall_clock = wall_clock or time.time
        self._oauth_transport = oauth_post
        self._pending_oauth_responses: dict[str, dict[str, Any]] = {}
        self._oauth_refreshing = False
        super().__init__(runtime_home, **kwargs)

    def _oauth_post(
        self,
        url: str,
        payload: Mapping[str, Any],
        *,
        timeout_seconds: float = 20.0,
    ) -> dict[str, Any]:
        if self._oauth_transport is not None:
            response = self._oauth_transport(url, payload)
        else:
            response = ConnectorManager._oauth_post(url, payload, timeout_seconds=timeout_seconds)
        if not isinstance(response, dict):
            raise ConnectorError("GitHub authorization returned an invalid response")
        grant = str(payload.get("grant_type") or "").strip()
        token = str(response.get("access_token") or "").strip()
        if token and grant in {_DEVICE_GRANT, _REFRESH_GRANT}:
            # Process-memory handoff only. connect_token consumes and removes it.
            self._pending_oauth_responses[token] = dict(response)
        return response

    def _clear_refresh_credential(self) -> None:
        self.vault.delete(_KEYRING_GITHUB_REFRESH_TOKEN)
        self.oauth_state.clear_github()

    def _store_oauth_metadata(self, response: Mapping[str, Any]) -> None:
        refresh_token = str(response.get("refresh_token") or "").strip()
        if not refresh_token:
            self._clear_refresh_credential()
            return
        # Device-flow refresh does not require a client secret. Keep the secret
        # pair in the OS vault; only timestamps are durable JSON.
        self.vault.set(_KEYRING_GITHUB_REFRESH_TOKEN, refresh_token)
        now = float(self.wall_clock())
        access_ttl = max(0, int(response.get("expires_in") or 0))
        refresh_ttl = max(0, int(response.get("refresh_token_expires_in") or 0))
        self.oauth_state.set_github_expiry(
            access_expires_at=(now + access_ttl) if access_ttl else 0.0,
            refresh_expires_at=(now + refresh_ttl) if refresh_ttl else 0.0,
        )

    def connect_token(self, token: str) -> dict[str, Any]:
        secret = str(token or "").strip()
        response = self._pending_oauth_responses.pop(secret, None)
        if response is None:
            # A PAT or imported external token is not tied to Loom's device-flow
            # refresh chain. Never leave an old refresh token attached to it.
            self._clear_refresh_credential()
        else:
            self._store_oauth_metadata(response)
        return super().connect_token(secret)

    def import_github_cli(self) -> dict[str, Any]:
        self._clear_refresh_credential()
        return super().import_github_cli()

    def disconnect_github(self) -> dict[str, Any]:
        self._clear_refresh_credential()
        self._pending_oauth_responses.clear()
        return super().disconnect_github()

    def _oauth_metadata(self) -> dict[str, Any]:
        snapshot = self.oauth_state.snapshot()
        github = snapshot.get("github")
        return dict(github) if isinstance(github, dict) else {}

    def _refresh_available(self) -> bool:
        if not str(self.environment.get("LOOM_GITHUB_CLIENT_ID") or "").strip():
            return False
        refresh_token = self.vault.get(_KEYRING_GITHUB_REFRESH_TOKEN)
        if not refresh_token:
            return False
        metadata = self._oauth_metadata()
        refresh_expires_at = float(metadata.get("refreshExpiresAt") or 0.0)
        return not refresh_expires_at or refresh_expires_at > float(self.wall_clock())

    def _access_expiring_soon(self) -> bool:
        metadata = self._oauth_metadata()
        if str(metadata.get("kind") or "") != "device-oauth":
            return False
        access_expires_at = float(metadata.get("accessExpiresAt") or 0.0)
        if not access_expires_at:
            return False
        return access_expires_at <= float(self.wall_clock()) + _REFRESH_SKEW_SECONDS

    def _refresh_oauth_token(self) -> dict[str, Any]:
        client_id = str(self.environment.get("LOOM_GITHUB_CLIENT_ID") or "").strip()
        refresh_token = self.vault.get(_KEYRING_GITHUB_REFRESH_TOKEN)
        if not client_id or not refresh_token:
            raise ConnectorError("GitHub OAuth refresh is not available")
        metadata = self._oauth_metadata()
        refresh_expires_at = float(metadata.get("refreshExpiresAt") or 0.0)
        if refresh_expires_at and refresh_expires_at <= float(self.wall_clock()):
            self._clear_refresh_credential()
            raise ConnectorError("GitHub refresh token expired; reconnect GitHub")

        self._oauth_refreshing = True
        try:
            response = self._oauth_post(
                _GITHUB_ACCESS_TOKEN_URL,
                {
                    "client_id": client_id,
                    "grant_type": _REFRESH_GRANT,
                    "refresh_token": refresh_token,
                },
            )
            error = str(response.get("error") or "").strip()
            if error:
                description = str(response.get("error_description") or error).strip()
                if error in {"bad_refresh_token", "incorrect_client_credentials"}:
                    self._clear_refresh_credential()
                raise ConnectorError(f"GitHub token refresh failed: {description}")
            access_token = str(response.get("access_token") or "").strip()
            if not access_token:
                raise ConnectorError("GitHub token refresh returned no access token")
            return self.connect_token(access_token)
        finally:
            self._oauth_refreshing = False

    def refresh(self) -> dict[str, Any]:
        # During a rotation, ConnectorManager.connect_token() calls self.refresh()
        # after saving the new access token. Avoid recursive rotation there.
        if self._oauth_refreshing:
            return super().refresh()

        enabled = bool((self.state_store.snapshot().get("github") or {}).get("enabled", True))
        bound = getattr(self, "_github", None)
        if enabled and (bound is None or getattr(bound, "source", "") == "keyring") and self._refresh_available() and self._access_expiring_soon():
            try:
                return self._refresh_oauth_token()
            except ConnectorError:
                # A transient refresh failure should not throw away a still-valid
                # access token. Validate all normal candidates before declaring
                # the connector unavailable.
                pass

        status = super().refresh()
        if status.get("connected") or not enabled or not self._refresh_available():
            return status
        # Handles restart after the access token already expired: the ordinary
        # keyring candidate fails health validation, then the refresh token gets
        # one chance to recover the account.
        try:
            return self._refresh_oauth_token()
        except ConnectorError as exc:
            self._github_error = f"{status.get('error') or 'GitHub access token is invalid'}; {exc}"
            return self.github_status()

    def refresh_if_store_changed(self) -> bool:
        before = str(self.github_status().get("bindingId") or "")
        changed = super().refresh_if_store_changed()
        if changed:
            return True
        if not self._access_expiring_soon() or not self._refresh_available():
            return False
        self.refresh()
        after = str(self.github_status().get("bindingId") or "")
        return before != after

    def github_status(self) -> dict[str, Any]:
        status = dict(super().github_status())
        metadata = self._oauth_metadata()
        expires_at = float(metadata.get("accessExpiresAt") or 0.0)
        refresh_expires_at = float(metadata.get("refreshExpiresAt") or 0.0)
        refreshable = self._refresh_available()
        if status.get("connected") and status.get("credentialSource") == "keyring" and str(metadata.get("kind") or "") == "device-oauth":
            status["credentialSource"] = "device-oauth-keyring"
        status["refreshable"] = refreshable
        status["accessTokenExpiresIn"] = max(0, int(expires_at - float(self.wall_clock()))) if expires_at else None
        status["refreshTokenExpiresIn"] = max(0, int(refresh_expires_at - float(self.wall_clock()))) if refresh_expires_at else None
        return status


__all__ = ["ConnectorOAuthStateStore", "RefreshingConnectorManager"]
