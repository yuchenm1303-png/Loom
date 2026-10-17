"""Opt-in OpenCode v2 Server adapter for privately authorized, BYOK agent tasks.

This module is intentionally NOT a free-inference relay. OpenCode hosted/free
models and Go allowances are never available through this transport. The
adapter is an internal building block, not a public multi-tenant HTTP API.
"""
from __future__ import annotations

import base64
import ipaddress
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any


class OpenCodeAgentError(RuntimeError):
    """Non-sensitive error surfaced to Loom; never includes server secrets."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _validate_server_url(raw: str) -> str:
    value = str(raw or "").strip().rstrip("/")
    url = urllib.parse.urlsplit(value)
    if (
        url.scheme not in {"http", "https"}
        or not url.hostname
        or url.username is not None
        or url.password is not None
        or url.query or url.fragment
        or url.path not in {"", "/"}
    ):
        raise ValueError("OpenCode Server URL must be an origin with no credentials or path.")
    try:
        ip = ipaddress.ip_address(url.hostname)
    except ValueError:
        ip = None
    is_loopback = ip.is_loopback if ip is not None else url.hostname.lower() == "localhost"
    if url.scheme != "https" and not is_loopback:
        raise ValueError("OpenCode Server requires HTTPS except for loopback addresses.")
    if url.port is not None and not (1 <= url.port <= 65535):
        raise ValueError("Invalid OpenCode Server port.")
    return value


_MODEL_ID = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]*/[a-zA-Z0-9][a-zA-Z0-9_.:/-]{0,180}$")
_SESSION_ID = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,150}$")


def _check_model_id(model: str) -> str:
    model = str(model or "").strip()
    if not _MODEL_ID.fullmatch(model):
        raise OpenCodeAgentError("INVALID_MODEL", "Specify a provider/model identifier.")
    provider, name = model.split("/", 1)
    # The cloud backend's OpenCode free tier is restricted to the official
    # client. Do not convert it into a shared public/free inference service.
    if (
        provider.casefold() in {"opencode", "opencode-go", "opencode-console", "zen"}
        or name.casefold().endswith("-free")
        or ":free" in name.casefold()
    ):
        raise OpenCodeAgentError(
            "HOSTED_FREE_RELAY_DISABLED",
            "OpenCode-hosted and free-tier models are not authorized for Loom relay.",
        )
    return model


@dataclass(frozen=True)
class OpenCodeAgentConfig:
    base_url: str = "http://127.0.0.1:14096"
    password: str = ""
    username: str = "opencode"
    enabled: bool = False
    permitted_models: tuple[str, ...] = ()
    timeout_seconds: float = 20.0

    def __post_init__(self) -> None:
        object.__setattr__(self, "base_url", _validate_server_url(self.base_url))
        if not self.username or ":" in self.username or "\n" in self.username:
            raise ValueError("Invalid OpenCode Server username.")
        if not (1 <= self.timeout_seconds <= 120):
            raise ValueError("OpenCode Agent timeout must be between 1 and 120 seconds.")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


@dataclass
class OpenCodeAgentClient:
    """Owner-only adapter; sessions tracked locally and never shared by ID."""

    config: OpenCodeAgentConfig
    _session_models: dict[str, str] = field(default_factory=dict, init=False, repr=False)

    def _request(self, method: str, route: str, body: dict | None = None) -> Any:
        # All route segments are constructed by this class, never direct user URLs.
        if not self.config.password:
            raise OpenCodeAgentError("OPENCODE_SERVER_NOT_CONFIGURED", "Server credential is missing.")
        if not route.startswith("/api/"):
            raise ValueError("The OpenCode adapter only allows official /api/ routes.")
        credentials = f"{self.config.username}:{self.config.password}"
        authorization = base64.b64encode(credentials.encode("utf-8")).decode("ascii")
        headers = {
            "Accept": "application/json",
            "Authorization": "Basic " + authorization,
            "User-Agent": "LoomOpenCodeAgentPilot/1",
        }
        if body is not None:
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(
            self.config.base_url + route,
            method=method,
            headers=headers,
            data=json.dumps(body).encode("utf-8") if body is not None else None,
        )
        try:
            # Explicitly forbid redirects to avoid forwarding Basic credentials.
            with urllib.request.build_opener(_NoRedirect).open(req, timeout=self.config.timeout_seconds) as res:
                raw = res.read(1024 * 1024 + 1)
                if len(raw) > 1024 * 1024:
                    raise OpenCodeAgentError("OPENCODE_RESPONSE_TOO_LARGE", "OpenCode response exceeded 1 MiB.")
                return json.loads(raw) if raw else None
        except urllib.error.HTTPError as exc:
            if exc.code == 401:
                raise OpenCodeAgentError("OPENCODE_AUTH_FAILED", "OpenCode server rejected its credential.") from None
            raise OpenCodeAgentError("OPENCODE_UPSTREAM_ERROR", f"OpenCode server returned HTTP {exc.code}.") from None
        except (OSError, urllib.error.URLError, TimeoutError):
            raise OpenCodeAgentError("OPENCODE_UNREACHABLE", "The OpenCode Server is unavailable.") from None
        except (ValueError, UnicodeDecodeError):
            raise OpenCodeAgentError("OPENCODE_INVALID_RESPONSE", "Invalid OpenCode Server response.") from None

    def status(self) -> dict[str, str]:
        """Read-only handshake. Does not list users or generate inference."""
        payload = self._request("GET", "/api/info")
        if not isinstance(payload, dict) or not str(payload.get("version", "")).startswith("2."):
            raise OpenCodeAgentError("OPENCODE_API_UNSUPPORTED", "OpenCode Server v2 is required.")
        return {
            "state": "available",
            "version": str(payload["version"]),
            "agent_enabled": "true" if self.config.enabled else "false",
        }

    def _assert_model_allowed(self, model: str) -> str:
        if not self.config.enabled:
            raise OpenCodeAgentError("OPENCODE_AGENT_DISABLED", "Cloud OpenCode Agent is disabled by default.")
        validated = _check_model_id(model)
        allowed = set(self.config.permitted_models)
        if validated not in allowed:
            raise OpenCodeAgentError(
                "OPENCODE_MODEL_NOT_APPROVED", "This BYOK model is not explicitly approved for remote use."
            )
        return validated

    def _owned_route(self, session_id: str, suffix: str = "") -> str:
        if not _SESSION_ID.fullmatch(session_id) or session_id not in self._session_models:
            raise OpenCodeAgentError("OPENCODE_SESSION_NOT_OWNED", "Unknown or unowned OpenCode session.")
        return "/api/session/" + urllib.parse.quote(session_id, safe="") + suffix

    def create_session(self, model: str, title: str = "Loom Agent") -> str:
        """Create read-only-by-default session for an explicitly approved BYOK model."""
        allowed = self._assert_model_allowed(model)
        if not isinstance(title, str) or len(title) > 96:
            raise OpenCodeAgentError("INVALID_TITLE", "Invalid session title.")
        provider, model_id = allowed.split("/", 1)
        payload = self._request("POST", "/api/session", {
            "title": title,
            "model": {"providerID": provider, "id": model_id},
            # Remote tool use must be approved through a separate security
            # review; never grant shell, file write, network or browser access.
            "permissions": [{"action": "*", "resource": "*", "effect": "deny"}],
        })
        session = payload.get("id") if isinstance(payload, dict) else None
        if not isinstance(session, str) or not _SESSION_ID.fullmatch(session):
            raise OpenCodeAgentError("OPENCODE_INVALID_RESPONSE", "OpenCode returned an invalid session ID.")
        self._session_models[session] = allowed
        return session

    def send_prompt(self, session_id: str, prompt: str) -> dict:
        route = self._owned_route(session_id, "/prompt")
        if not self.config.enabled:
            raise OpenCodeAgentError("OPENCODE_AGENT_DISABLED", "Cloud OpenCode Agent is disabled.")
        self._assert_model_allowed(self._session_models[session_id])
        if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 16000:
            raise OpenCodeAgentError("INVALID_PROMPT", "Prompt must contain 1–16000 characters.")
        result = self._request("POST", route, {"text": prompt})
        if not isinstance(result, dict):
            raise OpenCodeAgentError("OPENCODE_INVALID_RESPONSE", "Invalid prompt acknowledgement.")
        return result

    def get_messages(self, session_id: str) -> Any:
        return self._request("GET", self._owned_route(session_id, "/message") + "?limit=50")

    def get_permissions(self, session_id: str) -> Any:
        return self._request("GET", self._owned_route(session_id, "/permission"))

    def interrupt(self, session_id: str) -> Any:
        return self._request("POST", self._owned_route(session_id, "/interrupt"))

    def delete_session(self, session_id: str) -> None:
        self._request("DELETE", self._owned_route(session_id))
        self._session_models.pop(session_id, None)
