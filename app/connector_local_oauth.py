from __future__ import annotations

"""Machine-local OAuth application configuration for Loom development builds.

Production packages receive the GitHub OAuth application credentials at build
time. Source/development runs cannot safely keep a client secret in the public
repository, so they may opt into the same Web OAuth flow with a machine-local
configuration:

* the public client id is stored in a secret-free JSON file under runtime_home;
* the client secret is stored only in Loom's OS credential vault.

No user access token, refresh token, or OAuth authorization state is stored in
this file.
"""

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from app.connectors import ConnectorError


_LOCAL_CONFIG_VERSION = 1
_LOCAL_CONFIG_FILENAME = "connector-local-oauth.json"
_KEYRING_GITHUB_OAUTH_CLIENT_SECRET = "github/oauth-client-secret"


class LocalOAuthConfigStore:
    def __init__(self, runtime_home: str | Path, vault: Any) -> None:
        self.runtime_home = Path(runtime_home).expanduser().resolve()
        self.runtime_home.mkdir(parents=True, exist_ok=True)
        self.path = self.runtime_home / _LOCAL_CONFIG_FILENAME
        self.vault = vault

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
            "schemaVersion": _LOCAL_CONFIG_VERSION,
            "github": {
                "clientId": str(github.get("clientId") or "").strip(),
            },
        }

    def github_status(self) -> dict[str, Any]:
        client_id = str(self.snapshot()["github"]["clientId"] or "").strip()
        client_secret = self.vault.get(_KEYRING_GITHUB_OAUTH_CLIENT_SECRET)
        return {
            "configured": bool(client_id and client_secret),
            "clientId": client_id,
            "clientSecretConfigured": bool(client_secret),
        }

    def github_credentials(self) -> tuple[str, str]:
        status = self.github_status()
        if not status["configured"]:
            return "", ""
        return str(status["clientId"]), self.vault.get(_KEYRING_GITHUB_OAUTH_CLIENT_SECRET)

    def configure_github(self, client_id: str, client_secret: str) -> dict[str, Any]:
        resolved_id = str(client_id or "").strip()
        resolved_secret = str(client_secret or "").strip()
        if not resolved_id:
            raise ConnectorError("GitHub OAuth Client ID must not be empty")
        if not resolved_secret:
            raise ConnectorError("GitHub OAuth Client Secret must not be empty")
        if any(character.isspace() for character in resolved_id):
            raise ConnectorError("GitHub OAuth Client ID must not contain whitespace")

        previous = self.snapshot()
        previous_secret = self.vault.get(_KEYRING_GITHUB_OAUTH_CLIENT_SECRET)
        try:
            self.vault.set(_KEYRING_GITHUB_OAUTH_CLIENT_SECRET, resolved_secret)
            self._write(
                {
                    "schemaVersion": _LOCAL_CONFIG_VERSION,
                    "github": {"clientId": resolved_id},
                }
            )
        except Exception as exc:
            try:
                if previous_secret:
                    self.vault.set(_KEYRING_GITHUB_OAUTH_CLIENT_SECRET, previous_secret)
                else:
                    self.vault.delete(_KEYRING_GITHUB_OAUTH_CLIENT_SECRET)
            except Exception:
                pass
            try:
                self._write(previous)
            except Exception:
                pass
            if isinstance(exc, ConnectorError):
                raise
            raise ConnectorError(f"Could not save local GitHub OAuth configuration: {type(exc).__name__}") from exc
        return self.github_status()

    def clear_github(self) -> dict[str, Any]:
        self.vault.delete(_KEYRING_GITHUB_OAUTH_CLIENT_SECRET)
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass
        except OSError as exc:
            raise ConnectorError(f"Could not remove local GitHub OAuth configuration: {type(exc).__name__}") from exc
        return self.github_status()

    def _write(self, payload: dict[str, Any]) -> None:
        self.runtime_home.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(
            prefix="connector-local-oauth-",
            suffix=".json",
            dir=self.runtime_home,
        )
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


__all__ = ["LocalOAuthConfigStore"]
