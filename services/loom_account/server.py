"""Loom account service."""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import ipaddress
import json
import os
import re
import secrets
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Collection


_EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
_PASSWORD_ITERATIONS = 600_000

# Peers allowed to set the client address through a forwarding header. The
# service is meant to sit behind a TLS-terminating reverse proxy, so loopback is
# trusted by default. Override with LOOM_ACCOUNT_TRUSTED_PROXIES
# (comma-separated addresses or CIDR ranges); set it to an empty value to trust
# nobody and always rate limit on the raw peer address.
#
# CIDR support matters when the proxy runs in Docker: the peer address is then
# the proxy container's address on a bridge network, which is neither loopback
# nor stable across restarts. Trust the network instead of one address.
_DEFAULT_TRUSTED_PROXIES = ("127.0.0.1", "::1")


def _trusted_proxy_networks(
    values: Collection[str],
) -> tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...]:
    """Parse trusted-proxy entries, accepting bare addresses and CIDR ranges.

    Unparseable entries are skipped rather than raising: a typo in an optional
    environment variable should not stop the service from starting.
    """
    networks: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = []
    for value in values:
        text = str(value).strip()
        if not text:
            continue
        try:
            networks.append(ipaddress.ip_network(text, strict=False))
        except ValueError:
            continue
    return tuple(networks)


def _is_trusted_proxy(
    peer: str, networks: Collection[ipaddress.IPv4Network | ipaddress.IPv6Network]
) -> bool:
    try:
        address = ipaddress.ip_address(peer)
    except ValueError:
        return False
    return any(address in network for network in networks)


class AccountError(RuntimeError):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = int(status)
        self.code = str(code)
        self.message = str(message)


def _now() -> int:
    return int(time.time())


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _new_token(prefix: str) -> str:
    return f"{prefix}_{secrets.token_urlsafe(32)}"


def _password_hash(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _PASSWORD_ITERATIONS)
    return "$".join([
        "pbkdf2_sha256",
        str(_PASSWORD_ITERATIONS),
        base64.urlsafe_b64encode(salt).decode("ascii"),
        base64.urlsafe_b64encode(digest).decode("ascii"),
    ])


def _password_matches(password: str, encoded: str) -> bool:
    try:
        algorithm, iterations_text, salt_text, digest_text = str(encoded).split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        iterations = int(iterations_text)
        salt = base64.urlsafe_b64decode(salt_text.encode("ascii"))
        expected = base64.urlsafe_b64decode(digest_text.encode("ascii"))
    except (TypeError, ValueError, base64.binascii.Error):
        return False
    actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return hmac.compare_digest(actual, expected)


_cached_decoy_hash: str | None = None


def _decoy_password_hash() -> str:
    """A genuine PBKDF2 record used only to equalise failed-login timing.

    :meth:`AccountStore.authenticate` runs the full key-derivation function when
    the address is known. Running it for an unknown address as well keeps the
    response time from revealing whether an email is registered. Built lazily so
    importing this module does not pay for 600k iterations.
    """
    global _cached_decoy_hash
    if _cached_decoy_hash is None:
        _cached_decoy_hash = _password_hash(secrets.token_urlsafe(32))
    return _cached_decoy_hash


def _normalize_email(value: str) -> str:
    email = str(value or "").strip().casefold()
    if len(email) > 254 or not _EMAIL_RE.fullmatch(email):
        raise AccountError(HTTPStatus.BAD_REQUEST, "INVALID_EMAIL", "Enter a valid email address.")
    return email


def _validate_password(value: str) -> str:
    password = str(value or "")
    if len(password) < 8:
        raise AccountError(HTTPStatus.BAD_REQUEST, "WEAK_PASSWORD", "Password must be at least 8 characters.")
    if len(password) > 512:
        raise AccountError(HTTPStatus.BAD_REQUEST, "WEAK_PASSWORD", "Password is too long.")
    return password


@dataclass(frozen=True)
class AccountConfig:
    db_path: Path
    access_ttl_seconds: int = 15 * 60
    refresh_ttl_seconds: int = 30 * 24 * 60 * 60


class AccountStore:
    def __init__(self, config: AccountConfig) -> None:
        self.config = config
        self.config.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._guard = threading.RLock()
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.config.db_path, timeout=15, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        return connection

    def _initialize(self) -> None:
        with self._guard, self._connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    email TEXT NOT NULL UNIQUE,
                    password_hash TEXT NOT NULL,
                    display_name TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'active',
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY,
                    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    access_hash TEXT NOT NULL UNIQUE,
                    access_expires_at INTEGER NOT NULL,
                    refresh_hash TEXT NOT NULL UNIQUE,
                    refresh_expires_at INTEGER NOT NULL,
                    created_at INTEGER NOT NULL,
                    last_used_at INTEGER NOT NULL,
                    revoked_at INTEGER
                );

                CREATE INDEX IF NOT EXISTS idx_sessions_access_hash ON sessions(access_hash);
                CREATE INDEX IF NOT EXISTS idx_sessions_refresh_hash ON sessions(refresh_hash);
                CREATE INDEX IF NOT EXISTS idx_sessions_user_id ON sessions(user_id);
                """
            )

    @staticmethod
    def _safe_user(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": int(row["id"]),
            "email": str(row["email"]),
            "display_name": str(row["display_name"] or ""),
            "status": str(row["status"]),
            "created_at": int(row["created_at"]),
        }

    def register(self, email: str, password: str) -> dict[str, Any]:
        email = _normalize_email(email)
        password = _validate_password(password)
        now = _now()
        try:
            with self._guard, self._connect() as db:
                cursor = db.execute(
                    "INSERT INTO users(email, password_hash, created_at, updated_at) VALUES (?, ?, ?, ?)",
                    (email, _password_hash(password), now, now),
                )
                row = db.execute("SELECT * FROM users WHERE id = ?", (int(cursor.lastrowid),)).fetchone()
        except sqlite3.IntegrityError as exc:
            raise AccountError(
                HTTPStatus.CONFLICT,
                "EMAIL_EXISTS",
                "An account with this email already exists.",
            ) from exc
        if row is None:
            raise AccountError(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                "ACCOUNT_CREATE_FAILED",
                "Account could not be created.",
            )
        return self._safe_user(row)

    def authenticate(self, email: str, password: str) -> dict[str, Any]:
        email = _normalize_email(email)
        with self._connect() as db:
            row = db.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        if row is None:
            # Burn the same KDF cost as a real account. Short-circuiting here
            # would turn response latency into an email-enumeration oracle.
            _password_matches(str(password or ""), _decoy_password_hash())
            raise AccountError(
                HTTPStatus.UNAUTHORIZED,
                "INVALID_CREDENTIALS",
                "Email or password is incorrect.",
            )
        if not _password_matches(str(password or ""), str(row["password_hash"])):
            raise AccountError(
                HTTPStatus.UNAUTHORIZED,
                "INVALID_CREDENTIALS",
                "Email or password is incorrect.",
            )
        if str(row["status"]) != "active":
            raise AccountError(
                HTTPStatus.FORBIDDEN,
                "ACCOUNT_DISABLED",
                "This account is not active.",
            )
        return self._safe_user(row)

    def _issue_session(self, user_id: int, *, session_id: str | None = None) -> dict[str, Any]:
        now = _now()
        access_token = _new_token("loom_access")
        refresh_token = _new_token("loom_refresh")
        sid = session_id or str(uuid.uuid4())
        access_expires_at = now + self.config.access_ttl_seconds
        refresh_expires_at = now + self.config.refresh_ttl_seconds

        with self._guard, self._connect() as db:
            if session_id:
                db.execute(
                    """
                    UPDATE sessions
                    SET access_hash = ?, access_expires_at = ?, refresh_hash = ?,
                        refresh_expires_at = ?, last_used_at = ?, revoked_at = NULL
                    WHERE id = ? AND user_id = ?
                    """,
                    (
                        _token_hash(access_token),
                        access_expires_at,
                        _token_hash(refresh_token),
                        refresh_expires_at,
                        now,
                        sid,
                        user_id,
                    ),
                )
            else:
                db.execute(
                    """
                    INSERT INTO sessions(
                        id, user_id, access_hash, access_expires_at,
                        refresh_hash, refresh_expires_at, created_at, last_used_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        sid,
                        user_id,
                        _token_hash(access_token),
                        access_expires_at,
                        _token_hash(refresh_token),
                        refresh_expires_at,
                        now,
                        now,
                    ),
                )

        return {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "expires_in": self.config.access_ttl_seconds,
            "token_type": "Bearer",
        }

    def create_session(self, user_id: int) -> dict[str, Any]:
        return self._issue_session(user_id)

    def user_for_access_token(self, token: str) -> dict[str, Any]:
        hashed = _token_hash(str(token or ""))
        now = _now()
        with self._guard, self._connect() as db:
            row = db.execute(
                """
                SELECT users.*, sessions.id AS session_id
                FROM sessions
                JOIN users ON users.id = sessions.user_id
                WHERE sessions.access_hash = ?
                  AND sessions.revoked_at IS NULL
                  AND sessions.access_expires_at > ?
                """,
                (hashed, now),
            ).fetchone()
            if row is not None:
                db.execute(
                    "UPDATE sessions SET last_used_at = ? WHERE id = ?",
                    (now, str(row["session_id"])),
                )
        if row is None:
            raise AccountError(
                HTTPStatus.UNAUTHORIZED,
                "INVALID_TOKEN",
                "Your session has expired. Sign in again.",
            )
        if str(row["status"]) != "active":
            raise AccountError(
                HTTPStatus.FORBIDDEN,
                "ACCOUNT_DISABLED",
                "This account is not active.",
            )
        return self._safe_user(row)

    def refresh(self, refresh_token: str) -> tuple[dict[str, Any], dict[str, Any]]:
        hashed = _token_hash(str(refresh_token or ""))
        now = _now()
        with self._guard, self._connect() as db:
            row = db.execute(
                """
                SELECT sessions.id AS session_id, sessions.user_id, users.*
                FROM sessions
                JOIN users ON users.id = sessions.user_id
                WHERE sessions.refresh_hash = ?
                  AND sessions.revoked_at IS NULL
                  AND sessions.refresh_expires_at > ?
                """,
                (hashed, now),
            ).fetchone()
            if row is None:
                raise AccountError(
                    HTTPStatus.UNAUTHORIZED,
                    "INVALID_REFRESH_TOKEN",
                    "This sign-in session is no longer valid.",
                )
            if str(row["status"]) != "active":
                raise AccountError(
                    HTTPStatus.FORBIDDEN,
                    "ACCOUNT_DISABLED",
                    "This account is not active.",
                )

            access_token = _new_token("loom_access")
            next_refresh = _new_token("loom_refresh")
            updated = db.execute(
                """
                UPDATE sessions
                SET access_hash = ?, access_expires_at = ?, refresh_hash = ?,
                    refresh_expires_at = ?, last_used_at = ?
                WHERE id = ?
                  AND refresh_hash = ?
                  AND revoked_at IS NULL
                  AND refresh_expires_at > ?
                """,
                (
                    _token_hash(access_token),
                    now + self.config.access_ttl_seconds,
                    _token_hash(next_refresh),
                    now + self.config.refresh_ttl_seconds,
                    now,
                    str(row["session_id"]),
                    hashed,
                    now,
                ),
            )
            if updated.rowcount != 1:
                raise AccountError(
                    HTTPStatus.UNAUTHORIZED,
                    "INVALID_REFRESH_TOKEN",
                    "This sign-in session is no longer valid.",
                )

        tokens = {
            "access_token": access_token,
            "refresh_token": next_refresh,
            "expires_in": self.config.access_ttl_seconds,
            "token_type": "Bearer",
        }
        return tokens, self._safe_user(row)

    def revoke_refresh_token(self, refresh_token: str) -> None:
        hashed = _token_hash(str(refresh_token or ""))
        with self._guard, self._connect() as db:
            db.execute(
                "UPDATE sessions SET revoked_at = ? WHERE refresh_hash = ? AND revoked_at IS NULL",
                (_now(), hashed),
            )


class SlidingWindowLimiter:
    """In-process sliding-window limiter keyed by client address.

    Keys are pruned once their window drains. Without that, a public deployment
    would grow ``_events`` without bound simply by being scanned from many
    addresses, since every new key would otherwise be retained forever.
    """

    _SWEEP_EVERY = 512

    def __init__(self) -> None:
        self._guard = threading.Lock()
        self._events: dict[str, list[float]] = {}
        self._widest_window = 0
        self._calls_since_sweep = 0

    def _sweep(self, now: float) -> None:
        cutoff = now - self._widest_window
        for key in [key for key, stamps in self._events.items() if all(stamp < cutoff for stamp in stamps)]:
            del self._events[key]

    def check(self, key: str, limit: int, window_seconds: int) -> None:
        now = time.monotonic()
        cutoff = now - window_seconds
        with self._guard:
            self._widest_window = max(self._widest_window, window_seconds)
            events = [stamp for stamp in self._events.get(key, []) if stamp >= cutoff]
            if len(events) >= limit:
                # Keep the pruned list so the window keeps sliding while the
                # caller is being throttled.
                self._events[key] = events
                raise AccountError(
                    HTTPStatus.TOO_MANY_REQUESTS,
                    "RATE_LIMITED",
                    "Too many attempts. Try again shortly.",
                )
            events.append(now)
            self._events[key] = events
            self._calls_since_sweep += 1
            if self._calls_since_sweep >= self._SWEEP_EVERY:
                self._calls_since_sweep = 0
                self._sweep(now)


class AccountApplication:
    def __init__(self, store: AccountStore) -> None:
        self.store = store
        self.limiter = SlidingWindowLimiter()

    def register(self, body: dict[str, Any], client_key: str) -> dict[str, Any]:
        self.limiter.check(f"register:{client_key}", 5, 60)
        user = self.store.register(str(body.get("email") or ""), str(body.get("password") or ""))
        return {**self.store.create_session(int(user["id"])), "user": user}

    def login(self, body: dict[str, Any], client_key: str) -> dict[str, Any]:
        self.limiter.check(f"login:{client_key}", 20, 60)
        user = self.store.authenticate(str(body.get("email") or ""), str(body.get("password") or ""))
        return {**self.store.create_session(int(user["id"])), "user": user}

    def refresh(self, body: dict[str, Any], client_key: str) -> dict[str, Any]:
        self.limiter.check(f"refresh:{client_key}", 30, 60)
        tokens, user = self.store.refresh(str(body.get("refresh_token") or ""))
        return {**tokens, "user": user}

    def logout(self, body: dict[str, Any]) -> dict[str, Any]:
        token = str(body.get("refresh_token") or "")
        if token:
            self.store.revoke_refresh_token(token)
        return {"ok": True}

    def me(self, authorization: str) -> dict[str, Any]:
        scheme, _, token = str(authorization or "").partition(" ")
        if scheme.casefold() != "bearer" or not token.strip():
            raise AccountError(
                HTTPStatus.UNAUTHORIZED,
                "MISSING_TOKEN",
                "Sign in to continue.",
            )
        return {"user": self.store.user_for_access_token(token.strip())}


class AccountRequestHandler(BaseHTTPRequestHandler):
    server_version = "LoomAccount/1"

    # Requests larger than _MAX_BODY_BYTES are refused before parsing. A bounded
    # prefix is still drained so the client can finish writing and read the 413;
    # answering without draining makes the client observe a connection reset.
    _MAX_BODY_BYTES = 64 * 1024
    _MAX_DRAIN_BYTES = 1024 * 1024

    @property
    def application(self) -> AccountApplication:
        return self.server.application  # type: ignore[attr-defined]

    def _drain(self, length: int) -> None:
        remaining = min(length, self._MAX_DRAIN_BYTES)
        while remaining > 0:
            chunk = self.rfile.read(min(remaining, 64 * 1024))
            if not chunk:
                return
            remaining -= len(chunk)

    def _json_body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        if length > self._MAX_BODY_BYTES:
            self._drain(length)
            raise AccountError(
                HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                "REQUEST_TOO_LARGE",
                "Request is too large.",
            )
        raw = self.rfile.read(length) if length else b"{}"
        try:
            value = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise AccountError(
                HTTPStatus.BAD_REQUEST,
                "INVALID_JSON",
                "Request body must be valid JSON.",
            ) from exc
        if not isinstance(value, dict):
            raise AccountError(
                HTTPStatus.BAD_REQUEST,
                "INVALID_JSON",
                "Request body must be a JSON object.",
            )
        return value

    def _write_json(self, status: int, payload: dict[str, Any]) -> None:
        data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _client_key(self) -> str:
        peer = str(self.client_address[0] if self.client_address else "unknown")
        if not _is_trusted_proxy(peer, getattr(self.server, "trusted_proxies", ())):
            # Any caller can send X-Real-IP, so only believe it when the
            # immediate peer is a proxy we control. Otherwise every request
            # could mint itself a fresh rate-limit bucket.
            return peer
        # The proxy overwrites these headers, so the leftmost entry is the
        # address it actually observed.
        forwarded = self.headers.get("X-Real-IP") or self.headers.get("X-Forwarded-For") or ""
        candidate = forwarded.split(",")[0].strip()
        return candidate or peer

    def _dispatch(self) -> dict[str, Any]:
        path = self.path.split("?", 1)[0].rstrip("/") or "/"
        if self.command == "GET" and path == "/healthz":
            return {"ok": True}
        if self.command == "GET" and path == "/v1/auth/me":
            return self.application.me(self.headers.get("Authorization") or "")
        if self.command != "POST":
            raise AccountError(HTTPStatus.NOT_FOUND, "NOT_FOUND", "Endpoint not found.")

        body = self._json_body()
        if path == "/v1/auth/register":
            return self.application.register(body, self._client_key())
        if path == "/v1/auth/login":
            return self.application.login(body, self._client_key())
        if path == "/v1/auth/refresh":
            return self.application.refresh(body, self._client_key())
        if path == "/v1/auth/logout":
            return self.application.logout(body)
        raise AccountError(HTTPStatus.NOT_FOUND, "NOT_FOUND", "Endpoint not found.")

    def do_GET(self) -> None:
        self._serve()

    def do_POST(self) -> None:
        self._serve()

    def _serve(self) -> None:
        try:
            self._write_json(HTTPStatus.OK, self._dispatch())
        except AccountError as exc:
            self._write_json(
                exc.status,
                {"error": {"code": exc.code, "message": exc.message}},
            )
        except Exception:
            self._write_json(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                {"error": {"code": "INTERNAL_ERROR", "message": "Internal server error."}},
            )

    def log_message(self, format: str, *args: Any) -> None:
        if os.environ.get("LOOM_ACCOUNT_QUIET") == "1":
            return
        super().log_message(format, *args)


class LoomAccountServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(
        self,
        address: tuple[str, int],
        application: AccountApplication,
        trusted_proxies: Collection[str] | None = None,
    ) -> None:
        super().__init__(address, AccountRequestHandler)
        self.application = application
        self.trusted_proxies = _trusted_proxy_networks(
            _DEFAULT_TRUSTED_PROXIES if trusted_proxies is None else trusted_proxies
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Loom standalone account service")
    parser.add_argument("--host", default=os.environ.get("LOOM_ACCOUNT_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("LOOM_ACCOUNT_PORT", "8787")))
    parser.add_argument(
        "--db",
        default=os.environ.get(
            "LOOM_ACCOUNT_DB",
            str(Path.home() / ".loom-account" / "accounts.db"),
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config = AccountConfig(
        db_path=Path(args.db).expanduser().resolve(),
        access_ttl_seconds=max(
            60,
            int(os.environ.get("LOOM_ACCOUNT_ACCESS_TTL", str(15 * 60))),
        ),
        refresh_ttl_seconds=max(
            300,
            int(os.environ.get("LOOM_ACCOUNT_REFRESH_TTL", str(30 * 24 * 60 * 60))),
        ),
    )
    application = AccountApplication(AccountStore(config))
    trusted_proxies_setting = os.environ.get("LOOM_ACCOUNT_TRUSTED_PROXIES")
    trusted_proxies = (
        _DEFAULT_TRUSTED_PROXIES
        if trusted_proxies_setting is None
        else tuple(part.strip() for part in trusted_proxies_setting.split(",") if part.strip())
    )
    server = LoomAccountServer((str(args.host), int(args.port)), application, trusted_proxies)
    print(f"Loom Account Service listening on http://{args.host}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
