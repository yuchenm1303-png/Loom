from __future__ import annotations

"""Native-desktop GitHub OAuth for Loom.

The primary product path is the authorization-code flow with PKCE and a loopback
callback bound to 127.0.0.1 on an ephemeral port. GitHub explicitly supports
this redirect shape for native desktop applications. Device flow, GitHub CLI,
and PAT import remain fallbacks in the base connector manager.

Production builds receive the OAuth application credentials at package time.
Source/development runs may provide the same application credentials through a
machine-local secret-free config + OS credential vault, so both builds exercise
the identical browser authorization flow.

No user access token, refresh token, authorization code, PKCE verifier, or OAuth
state is written to Loom JSON state. Access/refresh tokens are committed to the
OS credential vault only after GitHub returns to the loopback listener and the
state value is verified.
"""

import base64
import hashlib
import html
import secrets
import threading
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.parse import parse_qs, urlencode, urlsplit

from app.connector_local_oauth import LocalOAuthConfigStore
from app.connector_oauth_refresh import RefreshingConnectorManager
from app.connectors import ConnectorError


_GITHUB_AUTHORIZE_URL = "https://github.com/login/oauth/authorize"
_GITHUB_ACCESS_TOKEN_URL = "https://github.com/login/oauth/access_token"
_KEYRING_GITHUB_REFRESH_TOKEN = "github/refresh-token"
_REFRESH_GRANT = "refresh_token"
_DEFAULT_CALLBACK_PATH = "/oauth/github/callback"
_DEFAULT_AUTH_TTL_SECONDS = 600


@dataclass(slots=True)
class _LoopbackAuthSession:
    session_id: str
    state: str
    code_verifier: str
    redirect_uri: str
    created_at: float
    expires_at: float
    server: ThreadingHTTPServer
    callback_event: threading.Event
    callback_payload: dict[str, str]
    poll_interval: float = 1.0
    exchange_started: bool = False
    exchange_result: dict[str, Any] | None = None
    exchange_error: str = ""
    expiry_timer: threading.Timer | None = None
    lock: threading.RLock = field(default_factory=threading.RLock)


WebOAuthPost = Callable[[str, Mapping[str, Any]], dict[str, Any]]


def _pkce_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def _callback_path(value: Any) -> str:
    path = str(value or _DEFAULT_CALLBACK_PATH).strip()
    if not path.startswith("/") or path.startswith("//") or "?" in path or "#" in path:
        raise ConnectorError("GitHub OAuth callback path must be an absolute local path")
    return path


def _first_query_value(values: dict[str, list[str]], key: str) -> str:
    items = values.get(key) or []
    return str(items[0] if items else "").strip()


def _success_page() -> bytes:
    return (
        "<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' "
        "content='width=device-width,initial-scale=1'><title>Loom · GitHub</title>"
        "<style>body{font-family:system-ui,-apple-system,Segoe UI,sans-serif;background:#0d1117;"
        "color:#e6edf3;display:grid;place-items:center;min-height:100vh;margin:0}.card{max-width:560px;"
        "padding:32px;border:1px solid #30363d;border-radius:16px;background:#161b22}h1{font-size:22px;"
        "margin:0 0 10px}p{line-height:1.55;color:#9da7b3;margin:0}</style></head><body><div class='card'>"
        "<h1>Authorization received</h1><p>Loom is finishing the GitHub connection automatically. "
        "You can close this tab and return to Loom.</p></div></body></html>"
    ).encode("utf-8")


def _error_page(message: str) -> bytes:
    safe = html.escape(str(message or "GitHub authorization could not be completed")[:500])
    return (
        "<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' "
        "content='width=device-width,initial-scale=1'><title>Loom · GitHub</title>"
        "<style>body{font-family:system-ui,-apple-system,Segoe UI,sans-serif;background:#0d1117;"
        "color:#e6edf3;display:grid;place-items:center;min-height:100vh;margin:0}.card{max-width:560px;"
        "padding:32px;border:1px solid #f85149;border-radius:16px;background:#161b22}h1{font-size:22px;"
        "margin:0 0 10px}p{line-height:1.55;color:#9da7b3;margin:0}</style></head><body><div class='card'>"
        f"<h1>GitHub authorization stopped</h1><p>{safe}</p></div></body></html>"
    ).encode("utf-8")


class WebOAuthConnectorManager(RefreshingConnectorManager):
    """RefreshingConnectorManager with one-click desktop GitHub web OAuth."""

    def __init__(
        self,
        runtime_home: str | Path,
        *,
        web_oauth_post: WebOAuthPost | None = None,
        **kwargs: Any,
    ) -> None:
        self._web_oauth_transport = web_oauth_post
        self._web_auth_sessions: dict[str, _LoopbackAuthSession] = {}
        super().__init__(runtime_home, **kwargs)
        self.local_oauth = LocalOAuthConfigStore(self.runtime_home, self.vault)

    def _environment_web_credentials(self) -> tuple[str, str]:
        return (
            str(self.environment.get("LOOM_GITHUB_CLIENT_ID") or "").strip(),
            str(self.environment.get("LOOM_GITHUB_CLIENT_SECRET") or "").strip(),
        )

    def _web_credentials(self) -> tuple[str, str, str]:
        environment_id, environment_secret = self._environment_web_credentials()
        if environment_id and environment_secret:
            return environment_id, environment_secret, "release-or-env"
        local_id, local_secret = self.local_oauth.github_credentials()
        if local_id and local_secret:
            return local_id, local_secret, "local"
        return "", "", ""

    def _web_client_id(self) -> str:
        client_id, _secret, _source = self._web_credentials()
        if client_id:
            return client_id
        environment_id, _environment_secret = self._environment_web_credentials()
        if environment_id:
            return environment_id
        return str(self.local_oauth.github_status().get("clientId") or "").strip()

    def _web_client_secret(self) -> str:
        _client_id, client_secret, _source = self._web_credentials()
        return client_secret

    def _web_oauth_source(self) -> str:
        _client_id, _client_secret, source = self._web_credentials()
        return source

    def _web_oauth_available(self) -> bool:
        client_id, client_secret, _source = self._web_credentials()
        return bool(client_id and client_secret)

    def configure_local_web_oauth(self, client_id: str, client_secret: str) -> dict[str, Any]:
        self.local_oauth.configure_github(client_id, client_secret)
        return self.github_status()

    def clear_local_web_oauth(self) -> dict[str, Any]:
        self.local_oauth.clear_github()
        return self.github_status()

    def _cleanup_expired_web_sessions(self) -> None:
        now = float(self.clock())
        for session_id, session in list(self._web_auth_sessions.items()):
            if now >= session.expires_at:
                self._close_web_session(session_id)

    def _close_web_session(self, session_id: str) -> None:
        session = self._web_auth_sessions.pop(str(session_id or ""), None)
        if session is None:
            return
        if session.expiry_timer is not None:
            try:
                session.expiry_timer.cancel()
            except Exception:
                pass
        try:
            session.server.shutdown()
        except Exception:
            pass
        try:
            session.server.server_close()
        except Exception:
            pass

    @staticmethod
    def _handler(
        callback_path: str,
        expected_state: str,
        callback_event: threading.Event,
        callback_payload: dict[str, str],
    ) -> type[BaseHTTPRequestHandler]:
        expected_path = callback_path
        expected_oauth_state = expected_state
        event = callback_event
        payload = callback_payload

        class Handler(BaseHTTPRequestHandler):
            def _send(self, status: int, body: bytes) -> None:
                self.send_response(status)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("Pragma", "no-cache")
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self) -> None:  # noqa: N802 - stdlib callback name
                parsed = urlsplit(self.path)
                if parsed.path != expected_path:
                    self._send(404, _error_page("This local callback path does not belong to the active Loom sign-in."))
                    return

                values = parse_qs(parsed.query, keep_blank_values=True)
                payload.clear()
                for key in ("code", "state", "error", "error_description"):
                    payload[key] = _first_query_value(values, key)

                returned_state = str(payload.get("state") or "")
                if not returned_state or not secrets.compare_digest(returned_state, expected_oauth_state):
                    payload["error"] = "state_mismatch"
                    payload["error_description"] = "The OAuth state did not match the active Loom sign-in."
                    event.set()
                    self._send(400, _error_page(payload["error_description"]))
                    return

                event.set()
                if payload.get("error"):
                    body = _error_page(payload.get("error_description") or payload.get("error") or "Authorization cancelled")
                    self._send(400, body)
                    return
                self._send(200, _success_page())

            def log_message(self, _format: str, *_args: Any) -> None:
                return

        return Handler

    def start_github_auth(self) -> dict[str, Any]:
        if not self._web_oauth_available():
            return super().start_github_auth()

        self._cleanup_expired_web_sessions()
        callback_path = _callback_path(self.environment.get("LOOM_GITHUB_CALLBACK_PATH"))
        callback_event = threading.Event()
        callback_payload: dict[str, str] = {}
        session_id = secrets.token_urlsafe(18)
        oauth_state = secrets.token_urlsafe(32)
        verifier = secrets.token_urlsafe(64)
        handler = self._handler(callback_path, oauth_state, callback_event, callback_payload)
        try:
            server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        except OSError as exc:
            raise ConnectorError(f"could not open the local GitHub OAuth callback: {type(exc).__name__}") from exc
        server.daemon_threads = True

        port = int(server.server_address[1])
        redirect_uri = f"http://127.0.0.1:{port}{callback_path}"
        challenge = _pkce_challenge(verifier)
        now = float(self.clock())
        ttl = _DEFAULT_AUTH_TTL_SECONDS
        scopes = str(self.environment.get("LOOM_GITHUB_OAUTH_SCOPES") or "repo read:org").strip()
        authorize_url = _GITHUB_AUTHORIZE_URL + "?" + urlencode(
            {
                "client_id": self._web_client_id(),
                "redirect_uri": redirect_uri,
                "scope": scopes,
                "state": oauth_state,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
            }
        )

        session = _LoopbackAuthSession(
            session_id=session_id,
            state=oauth_state,
            code_verifier=verifier,
            redirect_uri=redirect_uri,
            created_at=now,
            expires_at=now + ttl,
            server=server,
            callback_event=callback_event,
            callback_payload=callback_payload,
        )
        self._web_auth_sessions[session_id] = session
        thread = threading.Thread(
            target=server.serve_forever,
            kwargs={"poll_interval": 0.1},
            name=f"loom-github-oauth-{session_id[:8]}",
            daemon=True,
        )
        thread.start()
        timer = threading.Timer(ttl, self._close_web_session, args=(session_id,))
        timer.daemon = True
        session.expiry_timer = timer
        timer.start()

        return {
            "sessionId": session_id,
            "mode": "web",
            "status": "pending",
            "authorizationUrl": authorize_url,
            "redirectUrl": redirect_uri,
            "expiresIn": ttl,
            "pollInterval": 1,
        }

    def _web_exchange(self, session: _LoopbackAuthSession, code: str) -> dict[str, Any]:
        payload = {
            "client_id": self._web_client_id(),
            "client_secret": self._web_client_secret(),
            "code": code,
            "redirect_uri": session.redirect_uri,
            "code_verifier": session.code_verifier,
        }
        if self._web_oauth_transport is not None:
            response = self._web_oauth_transport(_GITHUB_ACCESS_TOKEN_URL, payload)
        else:
            response = self._oauth_post(_GITHUB_ACCESS_TOKEN_URL, payload)
        if not isinstance(response, dict):
            raise ConnectorError("GitHub authorization returned an invalid token response")
        return response

    def _persist_web_oauth_response(self, response: Mapping[str, Any]) -> dict[str, Any]:
        access_token = str(response.get("access_token") or "").strip()
        if not access_token:
            error = str(response.get("error_description") or response.get("error") or "").strip()
            raise ConnectorError(error or "GitHub authorization returned no access token")

        self._base_connect_token(access_token)
        refresh_token = str(response.get("refresh_token") or "").strip()
        refresh_warning = ""
        if refresh_token:
            try:
                self.vault.set(_KEYRING_GITHUB_REFRESH_TOKEN, refresh_token)
            except ConnectorError as exc:
                refresh_warning = f"Automatic GitHub token renewal is unavailable: {exc}"
                self.vault.delete(_KEYRING_GITHUB_REFRESH_TOKEN)
                refresh_token = ""
        else:
            self.vault.delete(_KEYRING_GITHUB_REFRESH_TOKEN)

        now = float(self.wall_clock())
        access_ttl = max(0, int(response.get("expires_in") or 0))
        refresh_ttl = max(0, int(response.get("refresh_token_expires_in") or 0)) if refresh_token else 0
        self.oauth_state._write(
            {
                "schemaVersion": 1,
                "github": {
                    "kind": "web-oauth",
                    "accessExpiresAt": (now + access_ttl) if access_ttl else 0.0,
                    "refreshExpiresAt": (now + refresh_ttl) if refresh_ttl else 0.0,
                },
            }
        )
        self._oauth_refresh_warning = refresh_warning
        return self.github_status()

    def poll_github_auth(self, session_id: str) -> dict[str, Any]:
        key = str(session_id or "").strip()
        session = self._web_auth_sessions.get(key)
        if session is None:
            return super().poll_github_auth(key)

        if float(self.clock()) >= session.expires_at:
            self._close_web_session(key)
            raise ConnectorError("GitHub browser authorization expired; try connecting again")

        if not session.callback_event.is_set():
            return {"sessionId": key, "mode": "web", "status": "pending", "pollInterval": 1}

        with session.lock:
            if session.exchange_result is not None:
                return dict(session.exchange_result)
            if session.exchange_error:
                raise ConnectorError(session.exchange_error)
            if session.exchange_started:
                return {"sessionId": key, "mode": "web", "status": "pending", "pollInterval": 1}
            session.exchange_started = True

        try:
            payload = dict(session.callback_payload)
            returned_state = str(payload.get("state") or "")
            if not returned_state or not secrets.compare_digest(returned_state, session.state):
                raise ConnectorError("GitHub authorization state did not match; the sign-in was rejected")
            error = str(payload.get("error") or "").strip()
            if error:
                description = str(payload.get("error_description") or error).strip()
                raise ConnectorError(f"GitHub authorization failed: {description}")
            code = str(payload.get("code") or "").strip()
            if not code:
                raise ConnectorError("GitHub authorization returned no authorization code")

            response = self._web_exchange(session, code)
            status = self._persist_web_oauth_response(response)
            result = {
                "sessionId": key,
                "mode": "web",
                "status": "connected",
                "connector": status,
            }
            with session.lock:
                session.exchange_result = dict(result)
            return result
        except ConnectorError as exc:
            with session.lock:
                session.exchange_error = str(exc)
            raise
        finally:
            self._close_web_session(key)

    def _access_expiring_soon(self) -> bool:
        metadata = self._oauth_metadata()
        if str(metadata.get("kind") or "") != "web-oauth":
            return super()._access_expiring_soon()
        expires_at = float(metadata.get("accessExpiresAt") or 0.0)
        if not expires_at:
            return False
        return expires_at <= float(self.wall_clock()) + 300

    def _refresh_available(self) -> bool:
        metadata = self._oauth_metadata()
        if str(metadata.get("kind") or "") == "web-oauth" and not self._web_client_secret():
            return False
        return super()._refresh_available()

    def _refresh_oauth_token(self) -> dict[str, Any]:
        metadata = self._oauth_metadata()
        if str(metadata.get("kind") or "") != "web-oauth":
            return super()._refresh_oauth_token()

        client_id = self._web_client_id()
        client_secret = self._web_client_secret()
        refresh_token = self.vault.get(_KEYRING_GITHUB_REFRESH_TOKEN)
        if not client_id or not client_secret or not refresh_token:
            raise ConnectorError("GitHub web OAuth refresh is not available")
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
                    "client_secret": client_secret,
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
            token = str(response.get("access_token") or "").strip()
            if token:
                self._pending_oauth_responses.pop(token, None)
            return self._persist_web_oauth_response(response)
        finally:
            self._oauth_refreshing = False

    def disconnect_github(self) -> dict[str, Any]:
        for session_id in list(self._web_auth_sessions):
            self._close_web_session(session_id)
        return super().disconnect_github()

    def github_status(self) -> dict[str, Any]:
        status = dict(super().github_status())
        metadata = self._oauth_metadata()
        if (
            status.get("connected")
            and status.get("credentialSource") == "keyring"
            and str(metadata.get("kind") or "") == "web-oauth"
        ):
            status["credentialSource"] = "web-oauth-keyring"
        local_status = self.local_oauth.github_status()
        web_available = self._web_oauth_available()
        status["webOAuthAvailable"] = web_available
        status["webOAuthSource"] = self._web_oauth_source()
        status["localWebOAuthConfigured"] = bool(local_status.get("configured"))
        status["localWebOAuthClientId"] = str(local_status.get("clientId") or "")
        status["preferredBrowserLogin"] = "web" if web_available else (
            "device" if status.get("deviceFlowAvailable") else "github-cli"
        )
        return status


__all__ = ["WebOAuthConnectorManager"]
