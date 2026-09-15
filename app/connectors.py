from __future__ import annotations

import base64
import getpass
import hashlib
import hmac
import json
import os
import re
import secrets
import shutil
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from app.agent_runtime import AgentTool, ToolEffect, ToolExposure, ToolRegistry, ToolResult


_GITHUB_API = "https://api.github.com"
_GITHUB_DEVICE_CODE_URL = "https://github.com/login/device/code"
_GITHUB_ACCESS_TOKEN_URL = "https://github.com/login/oauth/access_token"
_GITHUB_HOST = "github.com"
_GITHUB_API_VERSION = "2022-11-28"
_GITHUB_MAX_RESPONSE_BYTES = 4_000_000
_GITHUB_MAX_FILE_BYTES = 1_000_000
_GITHUB_OWNER_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$")
_GITHUB_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]{1,100}$")
_KEYRING_SERVICE = "loom-agent/connectors"
_KEYRING_GITHUB_TOKEN = "github/access-token"
_CONNECTOR_STATE_VERSION = 1
_DEFAULT_GITHUB_OAUTH_SCOPES = "repo read:org"


class ConnectorError(RuntimeError):
    pass


class GitHubAPIError(ConnectorError):
    def __init__(self, status: int, message: str) -> None:
        self.status = int(status)
        super().__init__(str(message or f"GitHub API returned HTTP {status}"))


@dataclass(frozen=True, slots=True)
class BoundGitHubAuth:
    token: str
    source: str
    login: str
    scopes: str
    binding_id: str


@dataclass(slots=True)
class _AuthSession:
    session_id: str
    mode: str
    created_at: float
    expires_at: float
    interval: float
    device_code: str = ""
    user_code: str = ""
    verification_uri: str = ""
    process: subprocess.Popen[Any] | None = None
    last_poll_at: float = 0.0


class ConnectorStateStore:
    """Secret-free connector preferences.

    Tokens never enter this file. It only records whether Loom is allowed to use
    a connector and bumps a generation marker so a running desktop can refresh
    connector bindings at the next sampling step after a CLI login/logout.
    """

    def __init__(self, runtime_home: str | Path) -> None:
        self.runtime_home = Path(runtime_home).expanduser().resolve()
        self.runtime_home.mkdir(parents=True, exist_ok=True)
        self.path = self.runtime_home / "connectors.json"
        self._guard = threading.RLock()

    def snapshot(self) -> dict[str, Any]:
        with self._guard:
            raw: dict[str, Any] = {}
            if self.path.is_file():
                try:
                    value = json.loads(self.path.read_text(encoding="utf-8"))
                    if isinstance(value, dict):
                        raw = value
                except (OSError, ValueError):
                    raw = {}
            github = raw.get("github") if isinstance(raw.get("github"), dict) else {}
            return {
                "schemaVersion": _CONNECTOR_STATE_VERSION,
                "generation": int(raw.get("generation") or 0),
                "github": {
                    # Existing environment / gh credentials are deliberately
                    # auto-discovered on first run. An explicit disconnect sets
                    # enabled=false so Loom does not silently reconnect.
                    "enabled": bool(github.get("enabled", True)),
                },
            }

    def set_github_enabled(self, enabled: bool) -> dict[str, Any]:
        with self._guard:
            current = self.snapshot()
            current["generation"] = int(current.get("generation") or 0) + 1
            current["github"] = {"enabled": bool(enabled)}
            self._write(current)
            return current

    def touch(self) -> dict[str, Any]:
        with self._guard:
            current = self.snapshot()
            current["generation"] = int(current.get("generation") or 0) + 1
            self._write(current)
            return current

    def mtime_ns(self) -> int:
        try:
            return self.path.stat().st_mtime_ns
        except OSError:
            return 0

    def _write(self, payload: dict[str, Any]) -> None:
        self.runtime_home.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(prefix="connectors-", suffix=".json", dir=self.runtime_home)
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


class CredentialVault:
    """OS-keychain backed secret storage for connector credentials."""

    def get(self, name: str) -> str:
        try:
            import keyring

            value = keyring.get_password(_KEYRING_SERVICE, name)
        except Exception:
            return ""
        return str(value or "").strip()

    def set(self, name: str, value: str) -> None:
        secret = str(value or "").strip()
        if not secret:
            raise ConnectorError("connector credential must not be empty")
        try:
            import keyring

            keyring.set_password(_KEYRING_SERVICE, name, secret)
        except Exception as exc:
            raise ConnectorError(f"OS credential store is unavailable: {type(exc).__name__}") from exc

    def delete(self, name: str) -> None:
        try:
            import keyring
            from keyring.errors import PasswordDeleteError

            try:
                keyring.delete_password(_KEYRING_SERVICE, name)
            except PasswordDeleteError:
                pass
        except Exception:
            pass


class GitHubClient:
    """Small fixed-host GitHub REST client.

    The connector never accepts arbitrary URLs from the model, so a GitHub tool
    cannot become a generic network/SSRF primitive. Tokens are only placed in an
    Authorization header for api.github.com.
    """

    def __init__(
        self,
        token: str,
        *,
        timeout_seconds: float = 20.0,
        opener: Callable[..., Any] | None = None,
    ) -> None:
        self._token = str(token or "").strip()
        if not self._token:
            raise ConnectorError("GitHub token is empty")
        self.timeout_seconds = max(1.0, min(120.0, float(timeout_seconds)))
        self._open = opener or urlopen

    def request(
        self,
        method: str,
        path: str,
        *,
        query: Mapping[str, Any] | None = None,
        body: Mapping[str, Any] | None = None,
    ) -> tuple[Any, Mapping[str, str]]:
        relative = str(path or "").strip()
        if not relative.startswith("/") or relative.startswith("//"):
            raise ValueError("GitHub API path must be an absolute relative path")
        url = _GITHUB_API + relative
        if query:
            pairs = [(str(key), str(value)) for key, value in query.items() if value is not None and str(value) != ""]
            if pairs:
                url += "?" + urlencode(pairs)
        payload = None
        headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {self._token}",
            "User-Agent": "Loom-Agent/0.1",
            "X-GitHub-Api-Version": _GITHUB_API_VERSION,
        }
        if body is not None:
            payload = json.dumps(dict(body), ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = Request(url, data=payload, headers=headers, method=str(method or "GET").upper())
        try:
            with self._open(request, timeout=self.timeout_seconds) as response:
                raw = response.read(_GITHUB_MAX_RESPONSE_BYTES + 1)
                if len(raw) > _GITHUB_MAX_RESPONSE_BYTES:
                    raise ConnectorError("GitHub response exceeded Loom's 4 MB connector limit")
                response_headers = {str(key): str(value) for key, value in response.headers.items()}
                if not raw:
                    return None, response_headers
                return json.loads(raw.decode("utf-8")), response_headers
        except HTTPError as exc:
            try:
                raw = exc.read(65_536)
                parsed = json.loads(raw.decode("utf-8", errors="replace")) if raw else {}
                message = str(parsed.get("message") or "") if isinstance(parsed, dict) else ""
            except Exception:
                message = ""
            if not message:
                message = f"GitHub API returned HTTP {exc.code}"
            raise GitHubAPIError(int(exc.code), message[:1000]) from exc
        except URLError as exc:
            reason = type(getattr(exc, "reason", None)).__name__ or "network error"
            raise ConnectorError(f"GitHub request failed: {reason}") from exc
        except TimeoutError as exc:
            raise ConnectorError("GitHub request timed out") from exc
        except json.JSONDecodeError as exc:
            raise ConnectorError("GitHub returned an invalid JSON response") from exc

    def user(self) -> tuple[dict[str, Any], Mapping[str, str]]:
        payload, headers = self.request("GET", "/user")
        if not isinstance(payload, dict):
            raise ConnectorError("GitHub user response was not an object")
        return payload, headers


def _validate_repo_slug(value: Any) -> tuple[str, str, str]:
    slug = str(value or "").strip().strip("/")
    if slug.count("/") != 1:
        raise ValueError("repository must be in owner/name form")
    owner, repo = slug.split("/", 1)
    if not _GITHUB_OWNER_RE.fullmatch(owner) or not _GITHUB_REPO_RE.fullmatch(repo):
        raise ValueError("repository must be a valid GitHub owner/name")
    return owner, repo, f"{owner}/{repo}"


def _validate_branch(value: Any, *, field: str = "branch") -> str:
    branch = str(value or "").strip()
    if not branch or len(branch) > 255:
        raise ValueError(f"{field} must not be empty")
    forbidden = ("..", "~", "^", ":", "?", "*", "[", "\\", "//")
    if any(item in branch for item in forbidden) or branch.startswith("/") or branch.endswith("/") or branch.endswith("."):
        raise ValueError(f"invalid GitHub {field}")
    if any(ord(char) < 32 or ord(char) == 127 for char in branch):
        raise ValueError(f"invalid GitHub {field}")
    return branch


def _validate_repo_path(value: Any) -> str:
    text = str(value or "").replace("\\", "/").strip().lstrip("/")
    if not text:
        raise ValueError("repository path must not be empty")
    parts = [part for part in text.split("/") if part and part != "."]
    if not parts or any(part == ".." for part in parts):
        raise ValueError("repository path must stay inside the repository")
    result = "/".join(parts)
    if len(result) > 4096:
        raise ValueError("repository path is too long")
    return result


def _bounded_text(value: Any, *, field: str, limit: int, allow_empty: bool = False) -> str:
    text = str(value or "").strip()
    if not text and not allow_empty:
        raise ValueError(f"{field} must not be empty")
    if len(text) > limit:
        raise ValueError(f"{field} exceeds {limit} characters")
    return text


def _limit(value: Any, *, default: int = 30, maximum: int = 100) -> int:
    if value in {None, ""}:
        return default
    try:
        resolved = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("limit must be an integer") from exc
    if not 1 <= resolved <= maximum:
        raise ValueError(f"limit must be within 1..{maximum}")
    return resolved


def _pick_repo(record: Mapping[str, Any]) -> dict[str, Any]:
    owner = record.get("owner") if isinstance(record.get("owner"), dict) else {}
    return {
        "id": record.get("id"),
        "name": record.get("name"),
        "full_name": record.get("full_name"),
        "private": bool(record.get("private")),
        "archived": bool(record.get("archived")),
        "default_branch": record.get("default_branch"),
        "description": record.get("description"),
        "html_url": record.get("html_url"),
        "owner": owner.get("login"),
        "permissions": record.get("permissions") if isinstance(record.get("permissions"), dict) else {},
        "updated_at": record.get("updated_at"),
    }


def _pick_issue(record: Mapping[str, Any]) -> dict[str, Any]:
    user = record.get("user") if isinstance(record.get("user"), dict) else {}
    return {
        "number": record.get("number"),
        "title": record.get("title"),
        "state": record.get("state"),
        "html_url": record.get("html_url"),
        "user": user.get("login"),
        "created_at": record.get("created_at"),
        "updated_at": record.get("updated_at"),
        "body": str(record.get("body") or "")[:12_000],
        "is_pull_request": isinstance(record.get("pull_request"), dict),
    }


def _pick_pull(record: Mapping[str, Any]) -> dict[str, Any]:
    user = record.get("user") if isinstance(record.get("user"), dict) else {}
    head = record.get("head") if isinstance(record.get("head"), dict) else {}
    base = record.get("base") if isinstance(record.get("base"), dict) else {}
    return {
        "number": record.get("number"),
        "title": record.get("title"),
        "state": record.get("state"),
        "draft": bool(record.get("draft")),
        "html_url": record.get("html_url"),
        "user": user.get("login"),
        "head": head.get("ref"),
        "base": base.get("ref"),
        "mergeable": record.get("mergeable"),
        "merged": bool(record.get("merged")),
        "created_at": record.get("created_at"),
        "updated_at": record.get("updated_at"),
        "body": str(record.get("body") or "")[:12_000],
    }


class ConnectorManager:
    """Product-level connector lifecycle plus immutable per-step tool bindings."""

    def __init__(
        self,
        runtime_home: str | Path,
        *,
        vault: CredentialVault | None = None,
        environment: Mapping[str, str] | None = None,
        client_factory: Callable[[str], GitHubClient] | None = None,
        command_runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
        popen_factory: Callable[..., subprocess.Popen[Any]] | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self.runtime_home = Path(runtime_home).expanduser().resolve()
        self.state_store = ConnectorStateStore(self.runtime_home)
        self.vault = vault or CredentialVault()
        self.environment = os.environ if environment is None else environment
        self.client_factory = client_factory or (lambda token: GitHubClient(token))
        self.command_runner = command_runner or subprocess.run
        self.popen_factory = popen_factory or subprocess.Popen
        self.clock = clock or time.monotonic
        self._guard = threading.RLock()
        self._binding_secret = secrets.token_bytes(32)
        self._github: BoundGitHubAuth | None = None
        self._github_error = ""
        self._auth_sessions: dict[str, _AuthSession] = {}
        self._runtime: Any | None = None
        self._last_store_mtime_ns = self.state_store.mtime_ns()
        self.refresh()

    def _binding_id(self, token: str) -> str:
        digest = hmac.new(self._binding_secret, token.encode("utf-8"), hashlib.sha256).hexdigest()
        return digest[:24]

    def _gh_binary(self) -> str:
        return shutil.which("gh") or ""

    def _gh_token(self) -> str:
        binary = self._gh_binary()
        if not binary:
            return ""
        try:
            result = self.command_runner(
                [binary, "auth", "token", "--hostname", _GITHUB_HOST],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=6,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return ""
        if int(getattr(result, "returncode", 1) or 0) != 0:
            return ""
        return str(getattr(result, "stdout", "") or "").strip()

    def _candidate_tokens(self) -> list[tuple[str, str]]:
        values = [
            ("keyring", self.vault.get(_KEYRING_GITHUB_TOKEN)),
            ("GH_TOKEN", str(self.environment.get("GH_TOKEN") or "").strip()),
            ("GITHUB_TOKEN", str(self.environment.get("GITHUB_TOKEN") or "").strip()),
            ("github-cli", self._gh_token()),
        ]
        unique: list[tuple[str, str]] = []
        seen: set[str] = set()
        for source, token in values:
            if not token or token in seen:
                continue
            seen.add(token)
            unique.append((source, token))
        return unique

    def refresh(self) -> dict[str, Any]:
        with self._guard:
            state = self.state_store.snapshot()
            self._last_store_mtime_ns = self.state_store.mtime_ns()
            if not bool((state.get("github") or {}).get("enabled", True)):
                self._github = None
                self._github_error = "Disconnected by user"
                return self.github_status()

            last_error = ""
            for source, token in self._candidate_tokens():
                try:
                    client = self.client_factory(token)
                    user, headers = client.user()
                    login = str(user.get("login") or "").strip()
                    if not login:
                        raise ConnectorError("GitHub authentication succeeded without a user login")
                    scopes = str(headers.get("X-OAuth-Scopes") or headers.get("x-oauth-scopes") or "")
                    self._github = BoundGitHubAuth(
                        token=token,
                        source=source,
                        login=login,
                        scopes=scopes,
                        binding_id=self._binding_id(token),
                    )
                    self._github_error = ""
                    return self.github_status()
                except Exception as exc:
                    last_error = f"{type(exc).__name__}: {exc}"
            self._github = None
            self._github_error = last_error or "No GitHub credential found"
            return self.github_status()

    def refresh_if_store_changed(self) -> bool:
        current = self.state_store.mtime_ns()
        if current == self._last_store_mtime_ns:
            return False
        self.refresh()
        return True

    def github_status(self) -> dict[str, Any]:
        with self._guard:
            bound = self._github
            return {
                "id": "github",
                "name": "GitHub",
                "connected": bound is not None,
                "enabled": bool((self.state_store.snapshot().get("github") or {}).get("enabled", True)),
                "account": bound.login if bound is not None else "",
                "credentialSource": bound.source if bound is not None else "",
                "scopes": bound.scopes if bound is not None else "",
                "bindingId": f"github:{bound.binding_id}" if bound is not None else "github:disconnected",
                "githubCliAvailable": bool(self._gh_binary()),
                "deviceFlowAvailable": bool(str(self.environment.get("LOOM_GITHUB_CLIENT_ID") or "").strip()),
                "error": self._github_error,
            }

    def list(self) -> list[dict[str, Any]]:
        return [self.github_status()]

    def connect_token(self, token: str) -> dict[str, Any]:
        secret = str(token or "").strip()
        if not secret:
            raise ConnectorError("GitHub token must not be empty")
        client = self.client_factory(secret)
        user, _headers = client.user()
        if not str(user.get("login") or "").strip():
            raise ConnectorError("GitHub token validation did not return a user")
        self.vault.set(_KEYRING_GITHUB_TOKEN, secret)
        self.state_store.set_github_enabled(True)
        return self.refresh()

    def import_github_cli(self) -> dict[str, Any]:
        token = self._gh_token()
        if not token:
            raise ConnectorError("GitHub CLI is not authenticated; run `gh auth login` first")
        self.vault.set(_KEYRING_GITHUB_TOKEN, token)
        self.state_store.set_github_enabled(True)
        return self.refresh()

    def disconnect_github(self) -> dict[str, Any]:
        self.vault.delete(_KEYRING_GITHUB_TOKEN)
        self.state_store.set_github_enabled(False)
        return self.refresh()

    def enable_github(self) -> dict[str, Any]:
        self.state_store.set_github_enabled(True)
        return self.refresh()

    @staticmethod
    def _oauth_post(url: str, payload: Mapping[str, Any], *, timeout_seconds: float = 20.0) -> dict[str, Any]:
        data = urlencode({str(key): str(value) for key, value in payload.items()}).encode("utf-8")
        request = Request(
            url,
            data=data,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/x-www-form-urlencoded",
                "User-Agent": "Loom-Agent/0.1",
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=timeout_seconds) as response:
                raw = response.read(256_000)
        except HTTPError as exc:
            raise ConnectorError(f"GitHub authorization returned HTTP {exc.code}") from exc
        except URLError as exc:
            reason = type(getattr(exc, "reason", None)).__name__ or "network error"
            raise ConnectorError(f"GitHub authorization failed: {reason}") from exc
        try:
            parsed = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ConnectorError("GitHub authorization returned invalid JSON") from exc
        if not isinstance(parsed, dict):
            raise ConnectorError("GitHub authorization returned an invalid response")
        return parsed

    def start_github_auth(self) -> dict[str, Any]:
        client_id = str(self.environment.get("LOOM_GITHUB_CLIENT_ID") or "").strip()
        now = self.clock()
        session_id = secrets.token_urlsafe(18)
        if client_id:
            scopes = str(self.environment.get("LOOM_GITHUB_OAUTH_SCOPES") or _DEFAULT_GITHUB_OAUTH_SCOPES).strip()
            response = self._oauth_post(
                _GITHUB_DEVICE_CODE_URL,
                {"client_id": client_id, "scope": scopes},
            )
            device_code = str(response.get("device_code") or "").strip()
            user_code = str(response.get("user_code") or "").strip()
            verification_uri = str(response.get("verification_uri") or "https://github.com/login/device").strip()
            expires_in = max(60, int(response.get("expires_in") or 900))
            interval = max(5, int(response.get("interval") or 5))
            if not device_code or not user_code:
                raise ConnectorError("GitHub device authorization did not return a device code")
            self._auth_sessions[session_id] = _AuthSession(
                session_id=session_id,
                mode="device",
                created_at=now,
                expires_at=now + expires_in,
                interval=float(interval),
                device_code=device_code,
                user_code=user_code,
                verification_uri=verification_uri,
            )
            return {
                "sessionId": session_id,
                "mode": "device",
                "status": "pending",
                "userCode": user_code,
                "verificationUrl": verification_uri,
                "expiresIn": expires_in,
                "pollInterval": interval,
            }

        binary = self._gh_binary()
        if not binary:
            raise ConnectorError(
                "GitHub browser login needs either LOOM_GITHUB_CLIENT_ID or GitHub CLI (`gh`). "
                "You can also use `loom connector github token` to store a token in the OS credential store."
            )
        creationflags = 0
        if os.name == "nt":
            creationflags = int(getattr(subprocess, "CREATE_NO_WINDOW", 0) or 0)
        try:
            process = self.popen_factory(
                [
                    binary,
                    "auth",
                    "login",
                    "--hostname",
                    _GITHUB_HOST,
                    "--git-protocol",
                    "https",
                    "--web",
                    "--clipboard",
                    "--skip-ssh-key",
                ],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=creationflags,
            )
        except OSError as exc:
            raise ConnectorError(f"could not start GitHub CLI login: {type(exc).__name__}") from exc
        self._auth_sessions[session_id] = _AuthSession(
            session_id=session_id,
            mode="github-cli",
            created_at=now,
            expires_at=now + 900,
            interval=2.0,
            process=process,
        )
        return {
            "sessionId": session_id,
            "mode": "github-cli",
            "status": "pending",
            "verificationUrl": "https://github.com/login/device",
            "userCode": "copied-to-clipboard",
            "expiresIn": 900,
            "pollInterval": 2,
        }

    def poll_github_auth(self, session_id: str) -> dict[str, Any]:
        key = str(session_id or "").strip()
        session = self._auth_sessions.get(key)
        if session is None:
            raise ConnectorError("GitHub authorization session was not found or has expired")
        now = self.clock()
        if now >= session.expires_at:
            self._auth_sessions.pop(key, None)
            raise ConnectorError("GitHub authorization session expired")

        if session.mode == "github-cli":
            process = session.process
            if process is None:
                raise ConnectorError("GitHub CLI login process is unavailable")
            code = process.poll()
            if code is None:
                return {"sessionId": key, "mode": session.mode, "status": "pending", "pollInterval": session.interval}
            self._auth_sessions.pop(key, None)
            if int(code) != 0:
                raise ConnectorError("GitHub CLI login did not complete successfully")
            status = self.import_github_cli()
            return {"sessionId": key, "mode": session.mode, "status": "connected", "connector": status}

        wait = session.interval - max(0.0, now - session.last_poll_at)
        if session.last_poll_at and wait > 0:
            return {
                "sessionId": key,
                "mode": session.mode,
                "status": "pending",
                "pollInterval": max(1, int(round(wait))),
            }
        session.last_poll_at = now
        client_id = str(self.environment.get("LOOM_GITHUB_CLIENT_ID") or "").strip()
        if not client_id:
            raise ConnectorError("LOOM_GITHUB_CLIENT_ID is no longer configured")
        response = self._oauth_post(
            _GITHUB_ACCESS_TOKEN_URL,
            {
                "client_id": client_id,
                "device_code": session.device_code,
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            },
        )
        token = str(response.get("access_token") or "").strip()
        if token:
            self._auth_sessions.pop(key, None)
            status = self.connect_token(token)
            return {"sessionId": key, "mode": session.mode, "status": "connected", "connector": status}
        error = str(response.get("error") or "").strip()
        if error in {"authorization_pending", "slow_down"}:
            if error == "slow_down":
                session.interval += 5
            return {"sessionId": key, "mode": session.mode, "status": "pending", "pollInterval": int(session.interval)}
        if error:
            description = str(response.get("error_description") or error).strip()
            self._auth_sessions.pop(key, None)
            raise ConnectorError(f"GitHub authorization failed: {description}")
        return {"sessionId": key, "mode": session.mode, "status": "pending", "pollInterval": int(session.interval)}

    def _client_for_bound(self, bound: BoundGitHubAuth | None) -> GitHubClient | None:
        if bound is None:
            return None
        return self.client_factory(bound.token)

    @staticmethod
    def _tool_error(message: str) -> ToolResult:
        return ToolResult(ok=False, content=message, data={"connector": "github", "connected": False})

    def _require_client(self, client: GitHubClient | None) -> GitHubClient | ToolResult:
        if client is None:
            return self._tool_error(
                "GitHub is not connected. Connect it with `loom connector github login` or the Loom connector settings, then start a new model step."
            )
        return client

    def agent_tools(self) -> tuple[AgentTool, ...]:
        with self._guard:
            bound = self._github
            status = self.github_status()
        binding = f"connector:github:{bound.binding_id if bound is not None else 'disconnected'}"
        client = self._client_for_bound(bound)

        def status_handler(_context: Any, _arguments: dict[str, Any]) -> ToolResult:
            snapshot = dict(status)
            return ToolResult(
                ok=True,
                content=json.dumps(snapshot, ensure_ascii=False, indent=2),
                data={"connector": snapshot},
            )

        def repo_get(_context: Any, arguments: dict[str, Any]) -> ToolResult:
            resolved = self._require_client(client)
            if isinstance(resolved, ToolResult):
                return resolved
            _owner, _repo, slug = _validate_repo_slug(arguments.get("repository"))
            payload, _ = resolved.request("GET", f"/repos/{slug}")
            record = _pick_repo(payload if isinstance(payload, dict) else {})
            return ToolResult(True, json.dumps(record, ensure_ascii=False, indent=2), {"repository": record})

        def repo_list(_context: Any, arguments: dict[str, Any]) -> ToolResult:
            resolved = self._require_client(client)
            if isinstance(resolved, ToolResult):
                return resolved
            limit = _limit(arguments.get("limit"), default=30)
            visibility = str(arguments.get("visibility") or "all").strip().casefold()
            if visibility not in {"all", "public", "private"}:
                raise ValueError("visibility must be all, public, or private")
            payload, _ = resolved.request(
                "GET",
                "/user/repos",
                query={
                    "per_page": limit,
                    "sort": "updated",
                    "direction": "desc",
                    "visibility": visibility,
                    "affiliation": "owner,collaborator,organization_member",
                },
            )
            rows = [_pick_repo(item) for item in payload or [] if isinstance(item, dict)] if isinstance(payload, list) else []
            return ToolResult(True, json.dumps(rows, ensure_ascii=False, indent=2), {"repositories": rows})

        def file_read(_context: Any, arguments: dict[str, Any]) -> ToolResult:
            resolved = self._require_client(client)
            if isinstance(resolved, ToolResult):
                return resolved
            _owner, _repo, slug = _validate_repo_slug(arguments.get("repository"))
            path = _validate_repo_path(arguments.get("path"))
            ref = str(arguments.get("ref") or "").strip()
            payload, _ = resolved.request(
                "GET",
                f"/repos/{slug}/contents/{quote(path, safe='/')}",
                query={"ref": ref or None},
            )
            if not isinstance(payload, dict) or str(payload.get("type") or "") != "file":
                raise ConnectorError("GitHub path is not a file")
            encoding = str(payload.get("encoding") or "").casefold()
            raw_content = str(payload.get("content") or "")
            if encoding != "base64":
                raise ConnectorError(f"unsupported GitHub content encoding: {encoding or 'unknown'}")
            data = base64.b64decode(raw_content.encode("ascii"), validate=False)
            if len(data) > _GITHUB_MAX_FILE_BYTES:
                raise ConnectorError("GitHub file exceeds Loom's 1 MB read limit")
            text = data.decode("utf-8", errors="replace")
            result = {"repository": slug, "path": path, "ref": ref, "sha": payload.get("sha"), "content": text}
            return ToolResult(True, text, result)

        def code_search(_context: Any, arguments: dict[str, Any]) -> ToolResult:
            resolved = self._require_client(client)
            if isinstance(resolved, ToolResult):
                return resolved
            _owner, _repo, slug = _validate_repo_slug(arguments.get("repository"))
            query_text = _bounded_text(arguments.get("query"), field="query", limit=512)
            limit = _limit(arguments.get("limit"), default=20)
            payload, _ = resolved.request(
                "GET",
                "/search/code",
                query={"q": f"{query_text} repo:{slug}", "per_page": limit},
            )
            items = payload.get("items") if isinstance(payload, dict) else []
            rows = [
                {
                    "name": item.get("name"),
                    "path": item.get("path"),
                    "sha": item.get("sha"),
                    "html_url": item.get("html_url"),
                    "repository": ((item.get("repository") or {}).get("full_name") if isinstance(item.get("repository"), dict) else slug),
                }
                for item in items or []
                if isinstance(item, dict)
            ]
            return ToolResult(True, json.dumps(rows, ensure_ascii=False, indent=2), {"results": rows})

        def issue_list(_context: Any, arguments: dict[str, Any]) -> ToolResult:
            resolved = self._require_client(client)
            if isinstance(resolved, ToolResult):
                return resolved
            _owner, _repo, slug = _validate_repo_slug(arguments.get("repository"))
            state = str(arguments.get("state") or "open").strip().casefold()
            if state not in {"open", "closed", "all"}:
                raise ValueError("state must be open, closed, or all")
            limit = _limit(arguments.get("limit"), default=30)
            include_prs = bool(arguments.get("include_pull_requests", False))
            payload, _ = resolved.request("GET", f"/repos/{slug}/issues", query={"state": state, "per_page": limit})
            rows = [_pick_issue(item) for item in payload or [] if isinstance(item, dict)] if isinstance(payload, list) else []
            if not include_prs:
                rows = [row for row in rows if not row["is_pull_request"]]
            return ToolResult(True, json.dumps(rows, ensure_ascii=False, indent=2), {"issues": rows})

        def issue_create(_context: Any, arguments: dict[str, Any]) -> ToolResult:
            resolved = self._require_client(client)
            if isinstance(resolved, ToolResult):
                return resolved
            _owner, _repo, slug = _validate_repo_slug(arguments.get("repository"))
            title = _bounded_text(arguments.get("title"), field="title", limit=256)
            body = _bounded_text(arguments.get("body"), field="body", limit=100_000, allow_empty=True)
            payload, _ = resolved.request("POST", f"/repos/{slug}/issues", body={"title": title, "body": body})
            record = _pick_issue(payload if isinstance(payload, dict) else {})
            return ToolResult(True, json.dumps(record, ensure_ascii=False, indent=2), {"issue": record})

        def issue_comment(_context: Any, arguments: dict[str, Any]) -> ToolResult:
            resolved = self._require_client(client)
            if isinstance(resolved, ToolResult):
                return resolved
            _owner, _repo, slug = _validate_repo_slug(arguments.get("repository"))
            number = int(arguments.get("number"))
            if number < 1:
                raise ValueError("number must be positive")
            body = _bounded_text(arguments.get("body"), field="body", limit=100_000)
            payload, _ = resolved.request("POST", f"/repos/{slug}/issues/{number}/comments", body={"body": body})
            record = {
                "id": payload.get("id") if isinstance(payload, dict) else None,
                "html_url": payload.get("html_url") if isinstance(payload, dict) else None,
                "body": str(payload.get("body") or "")[:12_000] if isinstance(payload, dict) else "",
            }
            return ToolResult(True, json.dumps(record, ensure_ascii=False, indent=2), {"comment": record})

        def pull_list(_context: Any, arguments: dict[str, Any]) -> ToolResult:
            resolved = self._require_client(client)
            if isinstance(resolved, ToolResult):
                return resolved
            _owner, _repo, slug = _validate_repo_slug(arguments.get("repository"))
            state = str(arguments.get("state") or "open").strip().casefold()
            if state not in {"open", "closed", "all"}:
                raise ValueError("state must be open, closed, or all")
            limit = _limit(arguments.get("limit"), default=30)
            payload, _ = resolved.request("GET", f"/repos/{slug}/pulls", query={"state": state, "per_page": limit})
            rows = [_pick_pull(item) for item in payload or [] if isinstance(item, dict)] if isinstance(payload, list) else []
            return ToolResult(True, json.dumps(rows, ensure_ascii=False, indent=2), {"pull_requests": rows})

        def pull_get(_context: Any, arguments: dict[str, Any]) -> ToolResult:
            resolved = self._require_client(client)
            if isinstance(resolved, ToolResult):
                return resolved
            _owner, _repo, slug = _validate_repo_slug(arguments.get("repository"))
            number = int(arguments.get("number"))
            if number < 1:
                raise ValueError("number must be positive")
            payload, _ = resolved.request("GET", f"/repos/{slug}/pulls/{number}")
            record = _pick_pull(payload if isinstance(payload, dict) else {})
            return ToolResult(True, json.dumps(record, ensure_ascii=False, indent=2), {"pull_request": record})

        def pull_create(_context: Any, arguments: dict[str, Any]) -> ToolResult:
            resolved = self._require_client(client)
            if isinstance(resolved, ToolResult):
                return resolved
            _owner, _repo, slug = _validate_repo_slug(arguments.get("repository"))
            title = _bounded_text(arguments.get("title"), field="title", limit=256)
            head = _validate_branch(arguments.get("head"), field="head")
            base = _validate_branch(arguments.get("base"), field="base")
            body = _bounded_text(arguments.get("body"), field="body", limit=100_000, allow_empty=True)
            draft = bool(arguments.get("draft", False))
            payload, _ = resolved.request(
                "POST",
                f"/repos/{slug}/pulls",
                body={"title": title, "head": head, "base": base, "body": body, "draft": draft},
            )
            record = _pick_pull(payload if isinstance(payload, dict) else {})
            return ToolResult(True, json.dumps(record, ensure_ascii=False, indent=2), {"pull_request": record})

        def branch_create(_context: Any, arguments: dict[str, Any]) -> ToolResult:
            resolved = self._require_client(client)
            if isinstance(resolved, ToolResult):
                return resolved
            _owner, _repo, slug = _validate_repo_slug(arguments.get("repository"))
            branch = _validate_branch(arguments.get("branch"))
            from_branch = _validate_branch(arguments.get("from_branch") or "main", field="from_branch")
            source, _ = resolved.request("GET", f"/repos/{slug}/git/ref/heads/{quote(from_branch, safe='')}")
            obj = source.get("object") if isinstance(source, dict) and isinstance(source.get("object"), dict) else {}
            sha = str(obj.get("sha") or "").strip()
            if not sha:
                raise ConnectorError("GitHub did not return a source branch SHA")
            payload, _ = resolved.request("POST", f"/repos/{slug}/git/refs", body={"ref": f"refs/heads/{branch}", "sha": sha})
            record = {"repository": slug, "branch": branch, "from_branch": from_branch, "sha": ((payload.get("object") or {}).get("sha") if isinstance(payload, dict) and isinstance(payload.get("object"), dict) else sha)}
            return ToolResult(True, json.dumps(record, ensure_ascii=False, indent=2), record)

        def file_write(_context: Any, arguments: dict[str, Any]) -> ToolResult:
            resolved = self._require_client(client)
            if isinstance(resolved, ToolResult):
                return resolved
            _owner, _repo, slug = _validate_repo_slug(arguments.get("repository"))
            path = _validate_repo_path(arguments.get("path"))
            branch = _validate_branch(arguments.get("branch") or "main")
            message = _bounded_text(arguments.get("message"), field="message", limit=500)
            content = str(arguments.get("content") or "")
            if len(content.encode("utf-8")) > _GITHUB_MAX_FILE_BYTES:
                raise ValueError("content exceeds Loom's 1 MB GitHub write limit")
            existing_sha = ""
            try:
                existing, _ = resolved.request(
                    "GET",
                    f"/repos/{slug}/contents/{quote(path, safe='/')}",
                    query={"ref": branch},
                )
                if isinstance(existing, dict):
                    existing_sha = str(existing.get("sha") or "")
            except GitHubAPIError as exc:
                if exc.status != 404:
                    raise
            body: dict[str, Any] = {
                "message": message,
                "content": base64.b64encode(content.encode("utf-8")).decode("ascii"),
                "branch": branch,
            }
            if existing_sha:
                body["sha"] = existing_sha
            payload, _ = resolved.request("PUT", f"/repos/{slug}/contents/{quote(path, safe='/')}", body=body)
            commit = payload.get("commit") if isinstance(payload, dict) and isinstance(payload.get("commit"), dict) else {}
            record = {
                "repository": slug,
                "path": path,
                "branch": branch,
                "updated": bool(existing_sha),
                "commit_sha": commit.get("sha"),
                "html_url": commit.get("html_url"),
            }
            return ToolResult(True, json.dumps(record, ensure_ascii=False, indent=2), record)

        def workflow_runs(_context: Any, arguments: dict[str, Any]) -> ToolResult:
            resolved = self._require_client(client)
            if isinstance(resolved, ToolResult):
                return resolved
            _owner, _repo, slug = _validate_repo_slug(arguments.get("repository"))
            branch = str(arguments.get("branch") or "").strip()
            if branch:
                branch = _validate_branch(branch)
            limit = _limit(arguments.get("limit"), default=20)
            payload, _ = resolved.request(
                "GET",
                f"/repos/{slug}/actions/runs",
                query={"per_page": limit, "branch": branch or None},
            )
            runs = payload.get("workflow_runs") if isinstance(payload, dict) else []
            rows = [
                {
                    "id": item.get("id"),
                    "name": item.get("name"),
                    "event": item.get("event"),
                    "status": item.get("status"),
                    "conclusion": item.get("conclusion"),
                    "head_branch": item.get("head_branch"),
                    "head_sha": item.get("head_sha"),
                    "html_url": item.get("html_url"),
                    "created_at": item.get("created_at"),
                    "updated_at": item.get("updated_at"),
                }
                for item in runs or []
                if isinstance(item, dict)
            ]
            return ToolResult(True, json.dumps(rows, ensure_ascii=False, indent=2), {"workflow_runs": rows})

        object_schema = {"type": "object", "additionalProperties": False}
        repository_property = {"type": "string", "description": "GitHub repository in owner/name form."}
        tools = (
            AgentTool(
                name="github_connection_status",
                description="Report Loom's current GitHub connector account, credential source, and connection health without exposing secrets.",
                input_schema={**object_schema, "properties": {}},
                handler=status_handler,
                effect=ToolEffect.READ_ONLY,
                exposure=ToolExposure.DIRECT,
                binding_key=binding,
            ),
            AgentTool(
                name="github_repository_get",
                description="Read metadata for one GitHub repository that the connected account can access.",
                input_schema={**object_schema, "properties": {"repository": repository_property}, "required": ["repository"]},
                handler=repo_get,
                effect=ToolEffect.READ_ONLY,
                exposure=ToolExposure.DEFERRED,
                binding_key=binding,
            ),
            AgentTool(
                name="github_repository_list",
                description="List repositories visible to the connected GitHub account, ordered by recent updates.",
                input_schema={**object_schema, "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 100}, "visibility": {"type": "string", "enum": ["all", "public", "private"]}}},
                handler=repo_list,
                effect=ToolEffect.READ_ONLY,
                exposure=ToolExposure.DEFERRED,
                binding_key=binding,
            ),
            AgentTool(
                name="github_file_read",
                description="Read one UTF-8 repository file from GitHub at an optional branch, tag, or commit ref.",
                input_schema={**object_schema, "properties": {"repository": repository_property, "path": {"type": "string"}, "ref": {"type": "string"}}, "required": ["repository", "path"]},
                handler=file_read,
                effect=ToolEffect.READ_ONLY,
                exposure=ToolExposure.DEFERRED,
                binding_key=binding,
            ),
            AgentTool(
                name="github_code_search",
                description="Search code inside one connected GitHub repository.",
                input_schema={**object_schema, "properties": {"repository": repository_property, "query": {"type": "string"}, "limit": {"type": "integer", "minimum": 1, "maximum": 100}}, "required": ["repository", "query"]},
                handler=code_search,
                effect=ToolEffect.READ_ONLY,
                exposure=ToolExposure.DEFERRED,
                binding_key=binding,
            ),
            AgentTool(
                name="github_issue_list",
                description="List GitHub issues in a repository, with optional closed/all state and pull-request inclusion.",
                input_schema={**object_schema, "properties": {"repository": repository_property, "state": {"type": "string", "enum": ["open", "closed", "all"]}, "limit": {"type": "integer", "minimum": 1, "maximum": 100}, "include_pull_requests": {"type": "boolean"}}, "required": ["repository"]},
                handler=issue_list,
                effect=ToolEffect.READ_ONLY,
                exposure=ToolExposure.DEFERRED,
                binding_key=binding,
            ),
            AgentTool(
                name="github_issue_create",
                description="Create a GitHub issue. This writes to an external system and therefore crosses Loom's approval boundary.",
                input_schema={**object_schema, "properties": {"repository": repository_property, "title": {"type": "string"}, "body": {"type": "string"}}, "required": ["repository", "title"]},
                handler=issue_create,
                effect=ToolEffect.SENSITIVE,
                exposure=ToolExposure.DEFERRED,
                binding_key=binding,
            ),
            AgentTool(
                name="github_issue_comment",
                description="Comment on a GitHub issue or pull request discussion. This writes externally and requires approval under approval mode.",
                input_schema={**object_schema, "properties": {"repository": repository_property, "number": {"type": "integer", "minimum": 1}, "body": {"type": "string"}}, "required": ["repository", "number", "body"]},
                handler=issue_comment,
                effect=ToolEffect.SENSITIVE,
                exposure=ToolExposure.DEFERRED,
                binding_key=binding,
            ),
            AgentTool(
                name="github_pull_request_list",
                description="List pull requests for a GitHub repository.",
                input_schema={**object_schema, "properties": {"repository": repository_property, "state": {"type": "string", "enum": ["open", "closed", "all"]}, "limit": {"type": "integer", "minimum": 1, "maximum": 100}}, "required": ["repository"]},
                handler=pull_list,
                effect=ToolEffect.READ_ONLY,
                exposure=ToolExposure.DEFERRED,
                binding_key=binding,
            ),
            AgentTool(
                name="github_pull_request_get",
                description="Read one GitHub pull request and its current merge metadata.",
                input_schema={**object_schema, "properties": {"repository": repository_property, "number": {"type": "integer", "minimum": 1}}, "required": ["repository", "number"]},
                handler=pull_get,
                effect=ToolEffect.READ_ONLY,
                exposure=ToolExposure.DEFERRED,
                binding_key=binding,
            ),
            AgentTool(
                name="github_pull_request_create",
                description="Open a GitHub pull request from an existing head branch to a base branch. This writes externally and requires approval under approval mode.",
                input_schema={**object_schema, "properties": {"repository": repository_property, "title": {"type": "string"}, "head": {"type": "string"}, "base": {"type": "string"}, "body": {"type": "string"}, "draft": {"type": "boolean"}}, "required": ["repository", "title", "head", "base"]},
                handler=pull_create,
                effect=ToolEffect.SENSITIVE,
                exposure=ToolExposure.DEFERRED,
                binding_key=binding,
            ),
            AgentTool(
                name="github_branch_create",
                description="Create a GitHub branch from an existing source branch. This writes externally and requires approval under approval mode.",
                input_schema={**object_schema, "properties": {"repository": repository_property, "branch": {"type": "string"}, "from_branch": {"type": "string"}}, "required": ["repository", "branch"]},
                handler=branch_create,
                effect=ToolEffect.SENSITIVE,
                exposure=ToolExposure.DEFERRED,
                binding_key=binding,
            ),
            AgentTool(
                name="github_file_write",
                description="Create or replace one UTF-8 file in a GitHub repository and commit it to the selected branch. This writes externally and requires approval under approval mode.",
                input_schema={**object_schema, "properties": {"repository": repository_property, "path": {"type": "string"}, "branch": {"type": "string"}, "message": {"type": "string"}, "content": {"type": "string"}}, "required": ["repository", "path", "message", "content"]},
                handler=file_write,
                effect=ToolEffect.SENSITIVE,
                exposure=ToolExposure.DEFERRED,
                binding_key=binding,
            ),
            AgentTool(
                name="github_workflow_runs_list",
                description="List recent GitHub Actions workflow runs for a repository, optionally filtered by branch.",
                input_schema={**object_schema, "properties": {"repository": repository_property, "branch": {"type": "string"}, "limit": {"type": "integer", "minimum": 1, "maximum": 100}}, "required": ["repository"]},
                handler=workflow_runs,
                effect=ToolEffect.READ_ONLY,
                exposure=ToolExposure.DEFERRED,
                binding_key=binding,
            ),
        )
        return tools

    def refresh_runtime_tools(self, registry: ToolRegistry) -> None:
        """Replace only future-step connector tool projections.

        ToolRouter copies AgentTool instances when a Step is captured. Replacing
        the long-lived registry therefore cannot retarget a previously sampled
        Step to a new GitHub token/account; the old Step keeps its old handler.
        """
        tools = self.agent_tools()
        current = getattr(registry, "_tools", None)
        if not isinstance(current, dict):
            for tool in tools:
                if registry.get(tool.name) is None:
                    registry.register(tool)
            return
        for tool in tools:
            current[tool.name] = tool

    def install_runtime(self, runtime: Any) -> None:
        with self._guard:
            self._runtime = runtime
            self.refresh_runtime_tools(runtime.tools)
            if getattr(runtime, "_loom_connector_step_refresh_installed", False):
                return
            original = runtime._build_step_context

            def build_step_context(session: Any, *, next_model_step: bool, step_id: str | None = None):
                if self.refresh_if_store_changed():
                    self.refresh_runtime_tools(runtime.tools)
                return original(session, next_model_step=next_model_step, step_id=step_id)

            runtime._build_step_context = build_step_context
            runtime._loom_connector_step_refresh_installed = True

    def refresh_bound_runtime_tools(self) -> None:
        runtime = self._runtime
        if runtime is not None:
            self.refresh_runtime_tools(runtime.tools)


__all__ = [
    "BoundGitHubAuth",
    "ConnectorError",
    "ConnectorManager",
    "ConnectorStateStore",
    "CredentialVault",
    "GitHubAPIError",
    "GitHubClient",
]
