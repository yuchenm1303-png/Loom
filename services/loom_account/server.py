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
import smtplib
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass
from email.message import EmailMessage
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Collection
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit
from urllib.request import Request as UrlRequest, urlopen
from .search import SearchServiceError, search as shared_search


_EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
_PASSWORD_ITERATIONS = 600_000
_DEFAULT_BUILTIN_MODEL_IDS = (
    "Ling-3.0-flash",
    "Ling-3.0-flash-VL",
    "Ling-3.0-tiny",
    "Ling-2.6-1T",
    "Ring-2.6-1T",
    "Ling-2.6-flash",
)
_PAIR_TTL_SECONDS = 120
_EMAIL_CHALLENGE_TTL_SECONDS = 10 * 60
_EMAIL_RESEND_SECONDS = 60
_OAUTH_STATE_TTL_SECONDS = 10 * 60
_OAUTH_EXCHANGE_TTL_SECONDS = 2 * 60

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


def _challenge_code_hash(challenge_id: str, code: str) -> str:
    # Six-digit verification codes have a tiny search space. A deliberately
    # slower KDF makes an offline database leak much less useful while keeping
    # each legitimate verification comfortably fast.
    return hashlib.pbkdf2_hmac(
        "sha256",
        str(code).encode("utf-8"),
        str(challenge_id).encode("utf-8"),
        120_000,
    ).hex()


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
    public_base_url: str = "https://account.smirel.com/v1"
    web_origin: str = "https://loom.smirel.com"
    email_provider: str = ""
    email_from: str = ""
    resend_api_key: str = ""
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_tls: bool = True
    google_client_id: str = ""
    google_client_secret: str = ""
    github_client_id: str = ""
    github_client_secret: str = ""
    legacy_registration_enabled: bool = True


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
                    role TEXT NOT NULL DEFAULT 'user',
                    email_verified_at INTEGER,
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

                CREATE TABLE IF NOT EXISTS auth_identities (
                    provider TEXT NOT NULL,
                    subject TEXT NOT NULL,
                    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    email TEXT NOT NULL DEFAULT '',
                    email_verified INTEGER NOT NULL DEFAULT 0,
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL,
                    PRIMARY KEY(provider, subject)
                );
                CREATE INDEX IF NOT EXISTS idx_auth_identities_user ON auth_identities(user_id);

                CREATE TABLE IF NOT EXISTS email_challenges (
                    id TEXT PRIMARY KEY,
                    email TEXT NOT NULL,
                    purpose TEXT NOT NULL,
                    code_hash TEXT NOT NULL,
                    payload_json TEXT NOT NULL DEFAULT '{}',
                    expires_at INTEGER NOT NULL,
                    resend_after INTEGER NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    consumed_at INTEGER,
                    created_at INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_email_challenges_email ON email_challenges(email, purpose, created_at DESC);

                CREATE TABLE IF NOT EXISTS oauth_states (
                    state_hash TEXT PRIMARY KEY,
                    provider TEXT NOT NULL,
                    return_to TEXT NOT NULL,
                    expires_at INTEGER NOT NULL,
                    created_at INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS oauth_exchange_codes (
                    code_hash TEXT PRIMARY KEY,
                    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    expires_at INTEGER NOT NULL,
                    consumed_at INTEGER,
                    created_at INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS audit_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    actor_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
                    action TEXT NOT NULL,
                    target_type TEXT NOT NULL DEFAULT '',
                    target_id TEXT NOT NULL DEFAULT '',
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    created_at INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_audit_logs_created_at ON audit_logs(created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_audit_logs_actor ON audit_logs(actor_user_id);

                CREATE TABLE IF NOT EXISTS feature_flags (
                    key TEXT PRIMARY KEY,
                    enabled INTEGER NOT NULL DEFAULT 0,
                    value_json TEXT NOT NULL DEFAULT '{}',
                    updated_at INTEGER NOT NULL,
                    updated_by INTEGER REFERENCES users(id) ON DELETE SET NULL
                );

                CREATE TABLE IF NOT EXISTS model_entitlements (
                    user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    models_json TEXT NOT NULL DEFAULT '[]',
                    updated_at INTEGER NOT NULL,
                    updated_by INTEGER REFERENCES users(id) ON DELETE SET NULL
                );

                CREATE TABLE IF NOT EXISTS model_credentials (
                    token_hash TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    expires_at INTEGER NOT NULL,
                    created_at INTEGER NOT NULL,
                    last_used_at INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_model_credentials_session ON model_credentials(session_id);
                CREATE INDEX IF NOT EXISTS idx_model_credentials_user ON model_credentials(user_id);
                """
            )
            columns = {str(row[1]) for row in db.execute("PRAGMA table_info(users)")}
            if "role" not in columns:
                db.execute("ALTER TABLE users ADD COLUMN role TEXT NOT NULL DEFAULT 'user'")
            legacy_without_verification = "email_verified_at" not in columns
            if legacy_without_verification:
                db.execute("ALTER TABLE users ADD COLUMN email_verified_at INTEGER")
            db.execute("UPDATE users SET role = 'user' WHERE role NOT IN ('user', 'admin', 'owner')")
            if legacy_without_verification:
                # Accounts that predate verified-email support are grandfathered
                # exactly once. New OAuth-only users must not acquire a fake
                # password identity on later service restarts.
                db.execute("UPDATE users SET email_verified_at = COALESCE(email_verified_at, created_at)")
                db.execute(
                    """INSERT OR IGNORE INTO auth_identities(provider, subject, user_id, email, email_verified, created_at, updated_at)
                    SELECT 'password', email, id, email, 1, created_at, updated_at FROM users"""
                )

    @staticmethod
    def _safe_user(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": int(row["id"]),
            "email": str(row["email"]),
            "display_name": str(row["display_name"] or ""),
            "status": str(row["status"]),
            "role": str(row["role"] or "user"),
            "email_verified": bool(row["email_verified_at"]),
            "created_at": int(row["created_at"]),
        }

    def register(self, email: str, password: str) -> dict[str, Any]:
        email = _normalize_email(email)
        password = _validate_password(password)
        return self.register_verified(email, _password_hash(password))

    def register_verified(self, email: str, password_hash: str, *, display_name: str = "") -> dict[str, Any]:
        email = _normalize_email(email)
        now = _now()
        try:
            with self._guard, self._connect() as db:
                cursor = db.execute(
                    """INSERT INTO users(email, password_hash, display_name, email_verified_at, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?)""",
                    (email, str(password_hash), str(display_name or ""), now, now, now),
                )
                user_id = int(cursor.lastrowid)
                db.execute(
                    """INSERT INTO auth_identities(provider, subject, user_id, email, email_verified, created_at, updated_at)
                    VALUES ('password', ?, ?, ?, 1, ?, ?)""",
                    (email, user_id, email, now, now),
                )
                row = db.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        except sqlite3.IntegrityError as exc:
            raise AccountError(
                HTTPStatus.CONFLICT,
                "EMAIL_EXISTS",
                "An account with this email already exists.",
            ) from exc
        if row is None:
            raise AccountError(HTTPStatus.INTERNAL_SERVER_ERROR, "ACCOUNT_CREATE_FAILED", "Account could not be created.")
        return self._safe_user(row)

    def user_by_email(self, email: str) -> dict[str, Any] | None:
        email = _normalize_email(email)
        with self._connect() as db:
            row = db.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        return self._safe_user(row) if row is not None else None

    def create_email_challenge(
        self,
        email: str,
        purpose: str,
        payload: dict[str, Any],
        *,
        ttl: int = _EMAIL_CHALLENGE_TTL_SECONDS,
    ) -> tuple[dict[str, Any], str]:
        email = _normalize_email(email)
        challenge_id = uuid.uuid4().hex
        code = f"{secrets.randbelow(1_000_000):06d}"
        now = _now()
        expires_at = now + max(60, int(ttl))
        resend_after = now + _EMAIL_RESEND_SECONDS
        with self._guard, self._connect() as db:
            db.execute(
                """INSERT INTO email_challenges(id, email, purpose, code_hash, payload_json, expires_at, resend_after, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    challenge_id,
                    email,
                    str(purpose),
                    _challenge_code_hash(challenge_id, code),
                    json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                    expires_at,
                    resend_after,
                    now,
                ),
            )
        return {
            "id": challenge_id,
            "email": email,
            "purpose": str(purpose),
            "expires_in": expires_at - now,
            "resend_after": _EMAIL_RESEND_SECONDS,
        }, code

    def resend_email_challenge(self, challenge_id: str) -> tuple[dict[str, Any], str]:
        now = _now()
        code = f"{secrets.randbelow(1_000_000):06d}"
        with self._guard, self._connect() as db:
            row = db.execute(
                "SELECT * FROM email_challenges WHERE id = ? AND consumed_at IS NULL",
                (str(challenge_id or ""),),
            ).fetchone()
            if row is None or int(row["expires_at"]) <= now:
                raise AccountError(HTTPStatus.BAD_REQUEST, "CHALLENGE_EXPIRED", "This verification request has expired.")
            if int(row["resend_after"]) > now:
                raise AccountError(HTTPStatus.TOO_MANY_REQUESTS, "RESEND_TOO_SOON", "Please wait before requesting another code.")
            expires_at = now + _EMAIL_CHALLENGE_TTL_SECONDS
            resend_after = now + _EMAIL_RESEND_SECONDS
            db.execute(
                """UPDATE email_challenges SET code_hash = ?, expires_at = ?, resend_after = ?, attempts = 0
                WHERE id = ?""",
                (_challenge_code_hash(str(row["id"]), code), expires_at, resend_after, str(row["id"])),
            )
        return {
            "id": str(row["id"]),
            "email": str(row["email"]),
            "purpose": str(row["purpose"]),
            "expires_in": _EMAIL_CHALLENGE_TTL_SECONDS,
            "resend_after": _EMAIL_RESEND_SECONDS,
        }, code

    def consume_email_challenge(self, challenge_id: str, code: str, purpose: str) -> tuple[str, dict[str, Any]]:
        now = _now()
        cid = str(challenge_id or "").strip()
        supplied = re.sub(r"\D", "", str(code or ""))
        with self._guard, self._connect() as db:
            row = db.execute("SELECT * FROM email_challenges WHERE id = ?", (cid,)).fetchone()
            if (
                row is None
                or str(row["purpose"]) != str(purpose)
                or row["consumed_at"] is not None
                or int(row["expires_at"]) <= now
                or int(row["attempts"]) >= 6
            ):
                raise AccountError(HTTPStatus.BAD_REQUEST, "INVALID_CODE", "The verification code is invalid or has expired.")
            expected = str(row["code_hash"])
            actual = _challenge_code_hash(cid, supplied)
            if len(supplied) != 6 or not hmac.compare_digest(actual, expected):
                db.execute("UPDATE email_challenges SET attempts = attempts + 1 WHERE id = ?", (cid,))
                raise AccountError(HTTPStatus.BAD_REQUEST, "INVALID_CODE", "The verification code is invalid or has expired.")
            db.execute("UPDATE email_challenges SET consumed_at = ? WHERE id = ?", (now, cid))
            try:
                payload = json.loads(str(row["payload_json"] or "{}"))
            except json.JSONDecodeError:
                payload = {}
        return str(row["email"]), payload if isinstance(payload, dict) else {}

    def set_password(self, user_id: int, password: str) -> dict[str, Any]:
        password = _validate_password(password)
        now = _now()
        with self._guard, self._connect() as db:
            row = db.execute("SELECT * FROM users WHERE id = ?", (int(user_id),)).fetchone()
            if row is None:
                raise AccountError(HTTPStatus.BAD_REQUEST, "INVALID_CODE", "The verification code is invalid or has expired.")
            email = str(row["email"])
            db.execute(
                "UPDATE users SET password_hash = ?, email_verified_at = COALESCE(email_verified_at, ?), updated_at = ? WHERE id = ?",
                (_password_hash(password), now, now, int(user_id)),
            )
            db.execute(
                """INSERT INTO auth_identities(provider, subject, user_id, email, email_verified, created_at, updated_at)
                VALUES ('password', ?, ?, ?, 1, ?, ?)
                ON CONFLICT(provider, subject) DO UPDATE SET user_id=excluded.user_id, email=excluded.email, email_verified=1, updated_at=excluded.updated_at""",
                (email, int(user_id), email, now, now),
            )
            db.execute("UPDATE sessions SET revoked_at = ? WHERE user_id = ? AND revoked_at IS NULL", (now, int(user_id)))
            updated = db.execute("SELECT * FROM users WHERE id = ?", (int(user_id),)).fetchone()
        return self._safe_user(updated)

    def upsert_oauth_user(
        self,
        provider: str,
        subject: str,
        email: str,
        *,
        email_verified: bool,
        display_name: str = "",
    ) -> dict[str, Any]:
        provider = str(provider).strip().casefold()
        subject = str(subject).strip()
        email = _normalize_email(email)
        if provider not in {"google", "github"} or not subject:
            raise AccountError(HTTPStatus.BAD_REQUEST, "OAUTH_IDENTITY_INVALID", "The sign-in identity is invalid.")
        if not email_verified:
            raise AccountError(HTTPStatus.BAD_REQUEST, "OAUTH_EMAIL_UNVERIFIED", "The provider did not return a verified email address.")
        now = _now()
        with self._guard, self._connect() as db:
            identity = db.execute(
                "SELECT user_id FROM auth_identities WHERE provider = ? AND subject = ?",
                (provider, subject),
            ).fetchone()
            if identity is not None:
                user_id = int(identity["user_id"])
                db.execute(
                    "UPDATE auth_identities SET email = ?, email_verified = 1, updated_at = ? WHERE provider = ? AND subject = ?",
                    (email, now, provider, subject),
                )
            else:
                user = db.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
                if user is None:
                    cursor = db.execute(
                        """INSERT INTO users(email, password_hash, display_name, email_verified_at, created_at, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?)""",
                        (email, _password_hash(_new_token("oauth_only")), str(display_name or ""), now, now, now),
                    )
                    user_id = int(cursor.lastrowid)
                else:
                    user_id = int(user["id"])
                    db.execute(
                        "UPDATE users SET email_verified_at = COALESCE(email_verified_at, ?), display_name = CASE WHEN display_name = '' THEN ? ELSE display_name END, updated_at = ? WHERE id = ?",
                        (now, str(display_name or ""), now, user_id),
                    )
                db.execute(
                    """INSERT INTO auth_identities(provider, subject, user_id, email, email_verified, created_at, updated_at)
                    VALUES (?, ?, ?, ?, 1, ?, ?)""",
                    (provider, subject, user_id, email, now, now),
                )
            row = db.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        if row is None:
            raise AccountError(HTTPStatus.INTERNAL_SERVER_ERROR, "ACCOUNT_CREATE_FAILED", "Account could not be created.")
        return self._safe_user(row)

    def create_oauth_state(self, provider: str, return_to: str) -> str:
        state = _new_token("loom_oauth_state")
        now = _now()
        with self._guard, self._connect() as db:
            db.execute(
                "INSERT INTO oauth_states(state_hash, provider, return_to, expires_at, created_at) VALUES (?, ?, ?, ?, ?)",
                (_token_hash(state), str(provider), str(return_to), now + _OAUTH_STATE_TTL_SECONDS, now),
            )
        return state

    def consume_oauth_state(self, state: str, provider: str) -> str:
        now = _now()
        hashed = _token_hash(str(state or ""))
        with self._guard, self._connect() as db:
            row = db.execute("SELECT * FROM oauth_states WHERE state_hash = ? AND provider = ?", (hashed, str(provider))).fetchone()
            db.execute("DELETE FROM oauth_states WHERE state_hash = ?", (hashed,))
        if row is None or int(row["expires_at"]) <= now:
            raise AccountError(HTTPStatus.BAD_REQUEST, "OAUTH_STATE_INVALID", "This sign-in request has expired. Please try again.")
        return str(row["return_to"])

    def create_oauth_exchange(self, user_id: int) -> str:
        code = _new_token("loom_oauth_exchange")
        now = _now()
        with self._guard, self._connect() as db:
            db.execute(
                "INSERT INTO oauth_exchange_codes(code_hash, user_id, expires_at, created_at) VALUES (?, ?, ?, ?)",
                (_token_hash(code), int(user_id), now + _OAUTH_EXCHANGE_TTL_SECONDS, now),
            )
        return code

    def consume_oauth_exchange(self, code: str) -> dict[str, Any]:
        now = _now()
        hashed = _token_hash(str(code or ""))
        with self._guard, self._connect() as db:
            row = db.execute(
                """SELECT oauth_exchange_codes.*, users.* FROM oauth_exchange_codes
                JOIN users ON users.id = oauth_exchange_codes.user_id
                WHERE oauth_exchange_codes.code_hash = ? AND oauth_exchange_codes.consumed_at IS NULL""",
                (hashed,),
            ).fetchone()
            if row is None or int(row["expires_at"]) <= now:
                raise AccountError(HTTPStatus.UNAUTHORIZED, "OAUTH_CODE_INVALID", "This sign-in code is invalid or has expired.")
            db.execute("UPDATE oauth_exchange_codes SET consumed_at = ? WHERE code_hash = ?", (now, hashed))
        return self._safe_user(row)

    def authenticate(self, email: str, password: str) -> dict[str, Any]:
        email = _normalize_email(email)
        with self._connect() as db:
            row = db.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        if row is None:
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

    def issue_model_credential(self, access_token: str) -> dict[str, Any]:
        hashed = _token_hash(str(access_token or ""))
        now = _now()
        with self._guard, self._connect() as db:
            row = db.execute(
                """SELECT sessions.id AS session_id, sessions.user_id, sessions.refresh_expires_at, users.status
                FROM sessions JOIN users ON users.id = sessions.user_id
                WHERE sessions.access_hash = ? AND sessions.revoked_at IS NULL
                  AND sessions.access_expires_at > ? AND sessions.refresh_expires_at > ?""",
                (hashed, now, now),
            ).fetchone()
            if row is None:
                raise AccountError(HTTPStatus.UNAUTHORIZED, "INVALID_TOKEN", "Your session has expired. Sign in again.")
            if str(row["status"]) != "active":
                raise AccountError(HTTPStatus.FORBIDDEN, "ACCOUNT_DISABLED", "This account is not active.")
            token = _new_token("loom_model")
            expires_at = int(row["refresh_expires_at"])
            db.execute(
                "INSERT INTO model_credentials(token_hash, session_id, user_id, expires_at, created_at, last_used_at) VALUES (?, ?, ?, ?, ?, ?)",
                (_token_hash(token), str(row["session_id"]), int(row["user_id"]), expires_at, now, now),
            )
            db.execute("DELETE FROM model_credentials WHERE expires_at <= ?", (now,))
        return {"model_token": token, "expires_in": max(1, expires_at - now), "token_type": "Bearer"}

    def user_for_model_token(self, token: str) -> dict[str, Any]:
        hashed = _token_hash(str(token or ""))
        now = _now()
        with self._guard, self._connect() as db:
            row = db.execute(
                """SELECT users.*, model_credentials.session_id AS credential_session_id
                FROM model_credentials
                JOIN sessions ON sessions.id = model_credentials.session_id
                JOIN users ON users.id = model_credentials.user_id
                WHERE model_credentials.token_hash = ?
                  AND model_credentials.expires_at > ?
                  AND sessions.revoked_at IS NULL
                  AND sessions.refresh_expires_at > ?
                  AND sessions.user_id = model_credentials.user_id""",
                (hashed, now, now),
            ).fetchone()
            if row is not None:
                db.execute("UPDATE model_credentials SET last_used_at = ? WHERE token_hash = ?", (now, hashed))
        if row is None:
            raise AccountError(HTTPStatus.UNAUTHORIZED, "INVALID_MODEL_TOKEN", "Built-in model authorization has expired. Sign in again.")
        if str(row["status"]) != "active":
            raise AccountError(HTTPStatus.FORBIDDEN, "ACCOUNT_DISABLED", "This account is not active.")
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

    def _audit(self, db: sqlite3.Connection, actor_user_id: int | None, action: str, *, target_type: str = "", target_id: str = "", metadata: dict[str, Any] | None = None) -> None:
        db.execute(
            "INSERT INTO audit_logs(actor_user_id, action, target_type, target_id, metadata_json, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (actor_user_id, action, target_type, target_id, json.dumps(metadata or {}, ensure_ascii=False, separators=(",", ":")), _now()),
        )

    def admin_overview(self) -> dict[str, Any]:
        now = _now()
        day_ago = now - 86400
        with self._connect() as db:
            users = int(db.execute("SELECT COUNT(*) FROM users").fetchone()[0])
            active_users = int(db.execute("SELECT COUNT(*) FROM users WHERE status = 'active'").fetchone()[0])
            return {
                "users": users,
                "active_users": active_users,
                "disabled_users": users - active_users,
                "active_sessions": int(db.execute("SELECT COUNT(*) FROM sessions WHERE revoked_at IS NULL AND refresh_expires_at > ?", (now,)).fetchone()[0]),
                "active_24h": int(db.execute("SELECT COUNT(DISTINCT user_id) FROM sessions WHERE last_used_at >= ?", (day_ago,)).fetchone()[0]),
                "registrations_24h": int(db.execute("SELECT COUNT(*) FROM users WHERE created_at >= ?", (day_ago,)).fetchone()[0]),
                "admins": int(db.execute("SELECT COUNT(*) FROM users WHERE role = 'admin'").fetchone()[0]),
                "owners": int(db.execute("SELECT COUNT(*) FROM users WHERE role = 'owner'").fetchone()[0]),
                "total_sessions": int(db.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]),
                "audit_events_24h": int(db.execute("SELECT COUNT(*) FROM audit_logs WHERE created_at >= ?", (day_ago,)).fetchone()[0]),
                "generated_at": now,
            }

    def admin_users(self, limit: int = 200) -> list[dict[str, Any]]:
        now = _now()
        with self._connect() as db:
            rows = db.execute(
                """SELECT users.*, COUNT(CASE WHEN sessions.revoked_at IS NULL AND sessions.refresh_expires_at > ? THEN 1 END) AS active_sessions, MAX(sessions.last_used_at) AS last_seen_at
                FROM users LEFT JOIN sessions ON sessions.user_id = users.id
                GROUP BY users.id ORDER BY users.created_at DESC, users.id DESC LIMIT ?""",
                (now, max(1, min(int(limit), 500))),
            ).fetchall()
        return [{**self._safe_user(row), "updated_at": int(row["updated_at"]), "active_sessions": int(row["active_sessions"] or 0), "last_seen_at": int(row["last_seen_at"]) if row["last_seen_at"] is not None else None, "verified": None} for row in rows]

    def admin_user(self, user_id: int) -> dict[str, Any]:
        now = _now()
        with self._connect() as db:
            row = db.execute(
                """SELECT users.*, COUNT(CASE WHEN sessions.revoked_at IS NULL AND sessions.refresh_expires_at > ? THEN 1 END) AS active_sessions, MAX(sessions.last_used_at) AS last_seen_at
                FROM users LEFT JOIN sessions ON sessions.user_id = users.id
                WHERE users.id = ? GROUP BY users.id""",
                (now, int(user_id)),
            ).fetchone()
            if row is None:
                raise AccountError(HTTPStatus.NOT_FOUND, "USER_NOT_FOUND", "User not found.")
            sessions = db.execute(
                "SELECT id, created_at, last_used_at, refresh_expires_at, revoked_at FROM sessions WHERE user_id = ? ORDER BY last_used_at DESC LIMIT 100",
                (int(user_id),),
            ).fetchall()
        return {**self._safe_user(row), "updated_at": int(row["updated_at"]), "active_sessions": int(row["active_sessions"] or 0), "last_seen_at": int(row["last_seen_at"]) if row["last_seen_at"] is not None else None, "verified": None, "sessions": [dict(item) for item in sessions]}

    def admin_sessions(self, limit: int = 300) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute(
                """SELECT sessions.id, sessions.user_id, users.email, users.role, sessions.created_at, sessions.last_used_at, sessions.refresh_expires_at, sessions.revoked_at
                FROM sessions JOIN users ON users.id = sessions.user_id ORDER BY sessions.last_used_at DESC LIMIT ?""",
                (max(1, min(int(limit), 1000)),),
            ).fetchall()
        return [dict(row) for row in rows]

    def admin_system(self) -> dict[str, Any]:
        with self._connect() as db:
            db.execute("SELECT 1").fetchone()
        smtp_configured = bool(
            (os.getenv("LOOM_SMTP_HOST") or os.getenv("SMTP_HOST"))
            and (os.getenv("LOOM_SMTP_FROM") or os.getenv("SMTP_FROM"))
        )
        return {
            "account_api": {"status": "healthy"},
            "database": {"status": "healthy", "engine": "SQLite"},
            "smtp": {"status": "configured" if smtp_configured else "not_configured"},
            "release": str(os.getenv("LOOM_RELEASE") or "unknown"),
            "generated_at": _now(),
        }

    def admin_audit(self, limit: int = 200) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute("""SELECT audit_logs.*, users.email AS actor_email FROM audit_logs LEFT JOIN users ON users.id = audit_logs.actor_user_id ORDER BY audit_logs.id DESC LIMIT ?""", (max(1, min(int(limit), 500)),)).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            try: item["metadata"] = json.loads(str(item.pop("metadata_json") or "{}"))
            except json.JSONDecodeError: item["metadata"] = {}
            result.append(item)
        return result

    def admin_feature_flags(self) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute("SELECT * FROM feature_flags ORDER BY key").fetchall()
        result = []
        for row in rows:
            try: value = json.loads(str(row["value_json"] or "{}"))
            except json.JSONDecodeError: value = {}
            result.append({"key": str(row["key"]), "enabled": bool(row["enabled"]), "value": value, "updated_at": int(row["updated_at"]), "updated_by": int(row["updated_by"]) if row["updated_by"] is not None else None})
        return result

    @staticmethod
    def _default_model_ids() -> list[str]:
        configured = str(os.getenv("LOOM_DEFAULT_BUILTIN_MODELS") or "").strip()
        values = [item.strip() for item in configured.split(",") if item.strip()] if configured else list(_DEFAULT_BUILTIN_MODEL_IDS)
        seen: set[str] = set()
        result: list[str] = []
        for value in values:
            key = value.casefold()
            if key in seen:
                continue
            seen.add(key)
            result.append(value)
        return result

    def model_access(self, user_id: int) -> dict[str, Any]:
        with self._connect() as db:
            row = db.execute("SELECT * FROM model_entitlements WHERE user_id = ?", (int(user_id),)).fetchone()
        if row is None:
            return {"enabled": True, "models": self._default_model_ids(), "source": "default", "updated_at": None}
        try:
            values = json.loads(str(row["models_json"] or "[]"))
        except json.JSONDecodeError:
            values = []
        models = [str(item).strip() for item in values if str(item).strip()] if isinstance(values, list) else []
        return {
            "enabled": bool(row["enabled"]),
            "models": models,
            "source": "override",
            "updated_at": int(row["updated_at"]),
            "updated_by": int(row["updated_by"]) if row["updated_by"] is not None else None,
        }

    def admin_set_model_access(self, actor: dict[str, Any], user_id: int, enabled: bool, models: Any) -> dict[str, Any]:
        if not isinstance(enabled, bool):
            raise AccountError(HTTPStatus.BAD_REQUEST, "INVALID_MODEL_ACCESS", "enabled must be a boolean.")
        if not isinstance(models, list):
            raise AccountError(HTTPStatus.BAD_REQUEST, "INVALID_MODEL_ACCESS", "models must be a list.")
        normalized: list[str] = []
        seen: set[str] = set()
        for item in models:
            value = str(item or "").strip()
            if not value or len(value) > 160:
                continue
            key = value.casefold()
            if key in seen:
                continue
            seen.add(key)
            normalized.append(value)
        if len(normalized) > 100:
            raise AccountError(HTTPStatus.BAD_REQUEST, "INVALID_MODEL_ACCESS", "Too many models in one entitlement.")
        now = _now()
        with self._guard, self._connect() as db:
            if db.execute("SELECT 1 FROM users WHERE id = ?", (int(user_id),)).fetchone() is None:
                raise AccountError(HTTPStatus.NOT_FOUND, "USER_NOT_FOUND", "User not found.")
            db.execute(
                """INSERT INTO model_entitlements(user_id, enabled, models_json, updated_at, updated_by)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET enabled=excluded.enabled, models_json=excluded.models_json,
                    updated_at=excluded.updated_at, updated_by=excluded.updated_by""",
                (int(user_id), 1 if enabled else 0, json.dumps(normalized, ensure_ascii=False, separators=(",", ":")), now, int(actor["id"])),
            )
            self._audit(db, int(actor["id"]), "user.model_access", target_type="user", target_id=str(user_id), metadata={"enabled": enabled, "models": normalized})
        return self.model_access(user_id)

    def admin_set_user_status(self, actor: dict[str, Any], user_id: int, status: str) -> dict[str, Any]:
        status = str(status or "").strip().casefold()
        if status not in {"active", "disabled"}: raise AccountError(HTTPStatus.BAD_REQUEST, "INVALID_STATUS", "Status must be active or disabled.")
        if int(actor["id"]) == int(user_id) and status != "active": raise AccountError(HTTPStatus.BAD_REQUEST, "SELF_DISABLE_FORBIDDEN", "You cannot disable your own administrator account.")
        with self._guard, self._connect() as db:
            row = db.execute("SELECT * FROM users WHERE id = ?", (int(user_id),)).fetchone()
            if row is None: raise AccountError(HTTPStatus.NOT_FOUND, "USER_NOT_FOUND", "User not found.")
            now = _now(); db.execute("UPDATE users SET status = ?, updated_at = ? WHERE id = ?", (status, now, int(user_id)))
            if status == "disabled": db.execute("UPDATE sessions SET revoked_at = COALESCE(revoked_at, ?) WHERE user_id = ?", (now, int(user_id)))
            self._audit(db, int(actor["id"]), "user.status", target_type="user", target_id=str(user_id), metadata={"status": status})
            updated = db.execute("SELECT * FROM users WHERE id = ?", (int(user_id),)).fetchone()
        return self._safe_user(updated)

    def admin_set_user_role(self, actor: dict[str, Any], user_id: int, role: str) -> dict[str, Any]:
        if str(actor.get("role")) != "owner": raise AccountError(HTTPStatus.FORBIDDEN, "OWNER_REQUIRED", "Owner access is required.")
        role = str(role or "").strip().casefold()
        if role not in {"user", "admin", "owner"}: raise AccountError(HTTPStatus.BAD_REQUEST, "INVALID_ROLE", "Role must be user, admin, or owner.")
        with self._guard, self._connect() as db:
            row = db.execute("SELECT * FROM users WHERE id = ?", (int(user_id),)).fetchone()
            if row is None: raise AccountError(HTTPStatus.NOT_FOUND, "USER_NOT_FOUND", "User not found.")
            if int(actor["id"]) == int(user_id) and str(row["role"]) == "owner" and role != "owner":
                if int(db.execute("SELECT COUNT(*) FROM users WHERE role = 'owner'").fetchone()[0]) <= 1: raise AccountError(HTTPStatus.BAD_REQUEST, "LAST_OWNER", "The last owner cannot be demoted.")
            db.execute("UPDATE users SET role = ?, updated_at = ? WHERE id = ?", (role, _now(), int(user_id)))
            self._audit(db, int(actor["id"]), "user.role", target_type="user", target_id=str(user_id), metadata={"role": role})
            updated = db.execute("SELECT * FROM users WHERE id = ?", (int(user_id),)).fetchone()
        return self._safe_user(updated)

    def admin_revoke_user_sessions(self, actor: dict[str, Any], user_id: int) -> int:
        now = _now()
        with self._guard, self._connect() as db:
            if db.execute("SELECT 1 FROM users WHERE id = ?", (int(user_id),)).fetchone() is None: raise AccountError(HTTPStatus.NOT_FOUND, "USER_NOT_FOUND", "User not found.")
            result = db.execute("UPDATE sessions SET revoked_at = COALESCE(revoked_at, ?) WHERE user_id = ? AND revoked_at IS NULL", (now, int(user_id)))
            self._audit(db, int(actor["id"]), "user.sessions.revoke", target_type="user", target_id=str(user_id), metadata={"count": int(result.rowcount)})
        return int(result.rowcount)

    def admin_revoke_session(self, actor: dict[str, Any], session_id: str) -> bool:
        session_id = str(session_id or "").strip()
        if not session_id or len(session_id) > 128:
            raise AccountError(HTTPStatus.BAD_REQUEST, "INVALID_SESSION_ID", "Session id is invalid.")
        now = _now()
        with self._guard, self._connect() as db:
            row = db.execute("SELECT id, user_id, revoked_at FROM sessions WHERE id = ?", (session_id,)).fetchone()
            if row is None:
                raise AccountError(HTTPStatus.NOT_FOUND, "SESSION_NOT_FOUND", "Session not found.")
            changed = row["revoked_at"] is None
            if changed:
                db.execute("UPDATE sessions SET revoked_at = ? WHERE id = ? AND revoked_at IS NULL", (now, session_id))
            self._audit(db, int(actor["id"]), "session.revoke", target_type="session", target_id=session_id, metadata={"user_id": int(row["user_id"]), "changed": changed})
        return changed

    def admin_set_feature_flag(self, actor: dict[str, Any], key: str, enabled: bool, value: Any) -> dict[str, Any]:
        key = str(key or "").strip()
        if not key or len(key) > 80 or not re.fullmatch(r"[a-z0-9_.-]+", key): raise AccountError(HTTPStatus.BAD_REQUEST, "INVALID_FLAG_KEY", "Feature flag key is invalid.")
        if not isinstance(enabled, bool): raise AccountError(HTTPStatus.BAD_REQUEST, "INVALID_FLAG_VALUE", "enabled must be a boolean.")
        value_json = json.dumps(value if value is not None else {}, ensure_ascii=False, separators=(",", ":"))
        if len(value_json.encode("utf-8")) > 8192: raise AccountError(HTTPStatus.BAD_REQUEST, "FLAG_VALUE_TOO_LARGE", "Feature flag value is too large.")
        now = _now()
        with self._guard, self._connect() as db:
            db.execute("""INSERT INTO feature_flags(key, enabled, value_json, updated_at, updated_by) VALUES (?, ?, ?, ?, ?) ON CONFLICT(key) DO UPDATE SET enabled=excluded.enabled,value_json=excluded.value_json,updated_at=excluded.updated_at,updated_by=excluded.updated_by""", (key, 1 if enabled else 0, value_json, now, int(actor["id"])))
            self._audit(db, int(actor["id"]), "feature_flag.set", target_type="feature_flag", target_id=key, metadata={"enabled": enabled})
        return {"key": key, "enabled": enabled, "value": json.loads(value_json), "updated_at": now, "updated_by": int(actor["id"])}


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
        self._pairing_guard = threading.Lock()
        self._pairing_tickets: dict[str, tuple[float, dict[str, Any]]] = {}

    def _email_configured(self) -> bool:
        cfg = self.store.config
        provider = str(cfg.email_provider or "").casefold()
        if provider == "test":
            return True
        if provider == "resend":
            return bool(cfg.resend_api_key and cfg.email_from)
        if provider == "smtp":
            return bool(cfg.smtp_host and cfg.email_from)
        return False

    def capabilities(self) -> dict[str, Any]:
        cfg = self.store.config
        return {
            "emailVerification": self._email_configured(),
            "passwordReset": self._email_configured(),
            "google": bool(cfg.google_client_id and cfg.google_client_secret),
            "github": bool(cfg.github_client_id and cfg.github_client_secret),
            "legacyRegistration": bool(cfg.legacy_registration_enabled),
        }

    def _send_email(self, to: str, subject: str, text: str) -> None:
        cfg = self.store.config
        provider = str(cfg.email_provider or "").casefold()
        if provider == "test":
            return
        if provider == "resend":
            payload = json.dumps({"from": cfg.email_from, "to": [to], "subject": subject, "text": text}).encode("utf-8")
            request = UrlRequest(
                "https://api.resend.com/emails",
                data=payload,
                method="POST",
                headers={"Authorization": f"Bearer {cfg.resend_api_key}", "Content-Type": "application/json"},
            )
            try:
                with urlopen(request, timeout=15) as response:
                    if int(response.status) >= 300:
                        raise RuntimeError(f"mail provider status {response.status}")
            except (HTTPError, URLError, OSError) as exc:
                raise AccountError(HTTPStatus.BAD_GATEWAY, "EMAIL_DELIVERY_FAILED", "We could not send the verification email. Please try again.") from exc
            return
        if provider == "smtp":
            message = EmailMessage()
            message["From"] = cfg.email_from
            message["To"] = to
            message["Subject"] = subject
            message.set_content(text)
            try:
                if int(cfg.smtp_port) == 465:
                    smtp = smtplib.SMTP_SSL(cfg.smtp_host, int(cfg.smtp_port), timeout=15)
                else:
                    smtp = smtplib.SMTP(cfg.smtp_host, int(cfg.smtp_port), timeout=15)
                with smtp:
                    smtp.ehlo()
                    if int(cfg.smtp_port) != 465 and cfg.smtp_tls:
                        smtp.starttls()
                        smtp.ehlo()
                    if cfg.smtp_username:
                        smtp.login(cfg.smtp_username, cfg.smtp_password)
                    smtp.send_message(message)
            except (OSError, smtplib.SMTPException) as exc:
                raise AccountError(HTTPStatus.BAD_GATEWAY, "EMAIL_DELIVERY_FAILED", "We could not send the verification email. Please try again.") from exc
            return
        raise AccountError(HTTPStatus.SERVICE_UNAVAILABLE, "EMAIL_NOT_CONFIGURED", "Email verification is not configured yet.")

    def _send_code(self, challenge: dict[str, Any], code: str) -> None:
        purpose = str(challenge.get("purpose") or "")
        if purpose == "register":
            subject = "Verify your Loom account"
            heading = "Use this code to finish creating your Loom account:"
        else:
            subject = "Reset your Loom password"
            heading = "Use this code to reset your Loom password:"
        self._send_email(
            str(challenge.get("email") or ""),
            subject,
            f"{heading}\n\n{code}\n\nThis code expires in 10 minutes. If you did not request this, you can ignore this email.",
        )

    def register(self, body: dict[str, Any], client_key: str) -> dict[str, Any]:
        self.limiter.check(f"register:{client_key}", 5, 60)
        if not self.store.config.legacy_registration_enabled:
            raise AccountError(HTTPStatus.UPGRADE_REQUIRED, "EMAIL_VERIFICATION_REQUIRED", "Update Loom to create a verified account.")
        user = self.store.register(str(body.get("email") or ""), str(body.get("password") or ""))
        return {**self.store.create_session(int(user["id"])), "user": user}

    def register_start(self, body: dict[str, Any], client_key: str) -> dict[str, Any]:
        self.limiter.check(f"register-start:{client_key}", 5, 60)
        if not self._email_configured():
            raise AccountError(HTTPStatus.SERVICE_UNAVAILABLE, "EMAIL_NOT_CONFIGURED", "Email verification is not configured yet.")
        email = _normalize_email(str(body.get("email") or ""))
        password = _validate_password(str(body.get("password") or ""))
        if self.store.user_by_email(email) is not None:
            raise AccountError(HTTPStatus.CONFLICT, "EMAIL_EXISTS", "An account with this email already exists.")
        challenge, code = self.store.create_email_challenge(email, "register", {"password_hash": _password_hash(password)})
        self._send_code(challenge, code)
        return {"challenge": challenge}

    def verify_email(self, body: dict[str, Any], client_key: str) -> dict[str, Any]:
        self.limiter.check(f"verify-email:{client_key}", 12, 60)
        email, payload = self.store.consume_email_challenge(
            str(body.get("challenge_id") or ""), str(body.get("code") or ""), "register"
        )
        password_hash = str(payload.get("password_hash") or "")
        if not password_hash.startswith("pbkdf2_sha256$"):
            raise AccountError(HTTPStatus.BAD_REQUEST, "INVALID_CODE", "The verification code is invalid or has expired.")
        user = self.store.register_verified(email, password_hash)
        return {**self.store.create_session(int(user["id"])), "user": user}

    def resend_email(self, body: dict[str, Any], client_key: str) -> dict[str, Any]:
        self.limiter.check(f"resend-email:{client_key}", 6, 60)
        challenge, code = self.store.resend_email_challenge(str(body.get("challenge_id") or ""))
        if challenge.get("purpose") == "register" or self.store.user_by_email(str(challenge.get("email") or "")) is not None:
            self._send_code(challenge, code)
        return {"challenge": challenge}

    def forgot_password(self, body: dict[str, Any], client_key: str) -> dict[str, Any]:
        self.limiter.check(f"forgot-password:{client_key}", 5, 60)
        if not self._email_configured():
            raise AccountError(HTTPStatus.SERVICE_UNAVAILABLE, "EMAIL_NOT_CONFIGURED", "Password reset email is not configured yet.")
        email = _normalize_email(str(body.get("email") or ""))
        user = self.store.user_by_email(email)
        challenge, code = self.store.create_email_challenge(email, "password_reset", {"user_id": int(user["id"]) if user else 0})
        # Do not reveal whether the address exists. Unknown addresses get a
        # syntactically valid challenge but no outbound message.
        if user is not None:
            self._send_code(challenge, code)
        return {"challenge": challenge}

    def reset_password(self, body: dict[str, Any], client_key: str) -> dict[str, Any]:
        self.limiter.check(f"reset-password:{client_key}", 10, 60)
        password = _validate_password(str(body.get("password") or ""))
        _, payload = self.store.consume_email_challenge(
            str(body.get("challenge_id") or ""), str(body.get("code") or ""), "password_reset"
        )
        user_id = int(payload.get("user_id") or 0)
        if user_id <= 0:
            raise AccountError(HTTPStatus.BAD_REQUEST, "INVALID_CODE", "The verification code is invalid or has expired.")
        user = self.store.set_password(user_id, password)
        return {**self.store.create_session(int(user["id"])), "user": user}

    def login(self, body: dict[str, Any], client_key: str) -> dict[str, Any]:
        self.limiter.check(f"login:{client_key}", 20, 60)
        user = self.store.authenticate(str(body.get("email") or ""), str(body.get("password") or ""))
        return {**self.store.create_session(int(user["id"])), "user": user}

    def _safe_return_to(self, value: str) -> str:
        origin = self.store.config.web_origin.rstrip("/")
        candidate = str(value or "").strip() or origin + "/"
        try:
            parsed = urlsplit(candidate)
            expected = urlsplit(origin)
            if parsed.scheme == expected.scheme and parsed.netloc == expected.netloc:
                return candidate
        except ValueError:
            pass
        return origin + "/"

    @staticmethod
    def _append_query(url: str, **params: str) -> str:
        parsed = urlsplit(url)
        current = parse_qs(parsed.query, keep_blank_values=True)
        for key, value in params.items():
            current[key] = [str(value)]
        query = urlencode([(key, item) for key, values in current.items() for item in values])
        return urlunsplit((parsed.scheme, parsed.netloc, parsed.path or "/", query, parsed.fragment))

    @staticmethod
    def _fetch_json(url: str, *, method: str = "GET", data: dict[str, Any] | None = None, headers: dict[str, str] | None = None) -> dict[str, Any]:
        body = urlencode(data).encode("utf-8") if data is not None else None
        request_headers = {"Accept": "application/json", "User-Agent": "Loom-Account/1"}
        if data is not None:
            request_headers["Content-Type"] = "application/x-www-form-urlencoded"
        request_headers.update(headers or {})
        request = UrlRequest(url, data=body, method=method, headers=request_headers)
        try:
            with urlopen(request, timeout=15) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (HTTPError, URLError, OSError, json.JSONDecodeError) as exc:
            raise AccountError(HTTPStatus.BAD_GATEWAY, "OAUTH_PROVIDER_FAILED", "The sign-in provider did not respond correctly. Please try again.") from exc
        if not isinstance(payload, dict):
            raise AccountError(HTTPStatus.BAD_GATEWAY, "OAUTH_PROVIDER_FAILED", "The sign-in provider did not respond correctly. Please try again.")
        return payload

    def oauth_start(self, provider: str, return_to: str) -> str:
        provider = str(provider).casefold()
        cfg = self.store.config
        redirect_uri = f"{cfg.public_base_url.rstrip('/')}/auth/oauth/{provider}/callback"
        target = self._safe_return_to(return_to)
        if provider == "google" and cfg.google_client_id and cfg.google_client_secret:
            state = self.store.create_oauth_state(provider, target)
            return "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode({
                "client_id": cfg.google_client_id,
                "redirect_uri": redirect_uri,
                "response_type": "code",
                "scope": "openid email profile",
                "state": state,
                "prompt": "select_account",
            })
        if provider == "github" and cfg.github_client_id and cfg.github_client_secret:
            state = self.store.create_oauth_state(provider, target)
            return "https://github.com/login/oauth/authorize?" + urlencode({
                "client_id": cfg.github_client_id,
                "redirect_uri": redirect_uri,
                "scope": "read:user user:email",
                "state": state,
            })
        raise AccountError(HTTPStatus.SERVICE_UNAVAILABLE, "OAUTH_NOT_CONFIGURED", "This sign-in provider is not configured yet.")

    def oauth_callback(self, provider: str, params: dict[str, str]) -> str:
        provider = str(provider).casefold()
        cfg = self.store.config
        default_return = cfg.web_origin.rstrip("/") + "/"
        state = str(params.get("state") or "")
        try:
            return_to = self.store.consume_oauth_state(state, provider)
        except AccountError as exc:
            return self._append_query(default_return, loom_oauth_error=exc.code)
        if params.get("error"):
            return self._append_query(return_to, loom_oauth_error="OAUTH_CANCELLED")
        code = str(params.get("code") or "").strip()
        if not code:
            return self._append_query(return_to, loom_oauth_error="OAUTH_CODE_MISSING")
        redirect_uri = f"{cfg.public_base_url.rstrip('/')}/auth/oauth/{provider}/callback"
        try:
            if provider == "google":
                token = self._fetch_json(
                    "https://oauth2.googleapis.com/token",
                    method="POST",
                    data={
                        "code": code,
                        "client_id": cfg.google_client_id,
                        "client_secret": cfg.google_client_secret,
                        "redirect_uri": redirect_uri,
                        "grant_type": "authorization_code",
                    },
                )
                access = str(token.get("access_token") or "")
                profile = self._fetch_json(
                    "https://openidconnect.googleapis.com/v1/userinfo",
                    headers={"Authorization": f"Bearer {access}"},
                )
                subject = str(profile.get("sub") or "")
                email = str(profile.get("email") or "")
                verified = bool(profile.get("email_verified"))
                name = str(profile.get("name") or "")
            elif provider == "github":
                token = self._fetch_json(
                    "https://github.com/login/oauth/access_token",
                    method="POST",
                    data={
                        "code": code,
                        "client_id": cfg.github_client_id,
                        "client_secret": cfg.github_client_secret,
                        "redirect_uri": redirect_uri,
                    },
                )
                access = str(token.get("access_token") or "")
                profile = self._fetch_json(
                    "https://api.github.com/user",
                    headers={"Authorization": f"Bearer {access}", "X-GitHub-Api-Version": "2022-11-28"},
                )
                subject = str(profile.get("id") or "")
                email = str(profile.get("email") or "")
                verified = False
                if email:
                    # Public profile email still has to be confirmed against the verified email list.
                    verified = False
                if not email or not verified:
                    request = UrlRequest(
                        "https://api.github.com/user/emails",
                        headers={"Accept": "application/vnd.github+json", "Authorization": f"Bearer {access}", "User-Agent": "Loom-Account/1", "X-GitHub-Api-Version": "2022-11-28"},
                    )
                    with urlopen(request, timeout=15) as response:
                        values = json.loads(response.read().decode("utf-8"))
                    if isinstance(values, list):
                        chosen = next((item for item in values if isinstance(item, dict) and item.get("primary") and item.get("verified")), None)
                        chosen = chosen or next((item for item in values if isinstance(item, dict) and item.get("verified")), None)
                        if isinstance(chosen, dict):
                            email = str(chosen.get("email") or "")
                            verified = bool(chosen.get("verified"))
                name = str(profile.get("name") or profile.get("login") or "")
            else:
                raise AccountError(HTTPStatus.BAD_REQUEST, "OAUTH_PROVIDER_INVALID", "Unsupported sign-in provider.")
            user = self.store.upsert_oauth_user(provider, subject, email, email_verified=verified, display_name=name)
            exchange = self.store.create_oauth_exchange(int(user["id"]))
            return self._append_query(return_to, loom_oauth_code=exchange, loom_oauth_provider=provider)
        except (AccountError, HTTPError, URLError, OSError, json.JSONDecodeError) as exc:
            code_name = exc.code if isinstance(exc, AccountError) else "OAUTH_PROVIDER_FAILED"
            return self._append_query(return_to, loom_oauth_error=code_name)

    def oauth_exchange(self, body: dict[str, Any], client_key: str) -> dict[str, Any]:
        self.limiter.check(f"oauth-exchange:{client_key}", 20, 60)
        user = self.store.consume_oauth_exchange(str(body.get("code") or ""))
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

    def issue_device_pair(self, authorization: str, client_key: str) -> dict[str, Any]:
        user = self.me(authorization)["user"]
        user_id = int(user["id"])
        self.limiter.check(f"pair-issue:{user_id}:{client_key}", 20, 60)
        ticket = _new_token("loom_pair")
        now = time.monotonic()
        with self._pairing_guard:
            self._pairing_tickets = {key: value for key, value in self._pairing_tickets.items() if value[0] > now}
            self._pairing_tickets[ticket] = (now + _PAIR_TTL_SECONDS, dict(user))
        return {"pairing_ticket": ticket, "expires_in": _PAIR_TTL_SECONDS}

    def exchange_device_pair(self, body: dict[str, Any], client_key: str) -> dict[str, Any]:
        self.limiter.check(f"pair-exchange:{client_key}", 30, 60)
        ticket = str(body.get("pairing_ticket") or "").strip()
        if not ticket.startswith("loom_pair_") or len(ticket) > 256:
            raise AccountError(HTTPStatus.UNAUTHORIZED, "INVALID_PAIRING_TICKET", "This Loom Host pairing request is no longer valid.")
        now = time.monotonic()
        with self._pairing_guard:
            entry = self._pairing_tickets.pop(ticket, None)
        if entry is None or entry[0] <= now:
            raise AccountError(HTTPStatus.UNAUTHORIZED, "INVALID_PAIRING_TICKET", "This Loom Host pairing request is no longer valid.")
        user = entry[1]
        return {**self.store.create_session(int(user["id"])), "user": user}

    @staticmethod
    def _bearer_token(authorization: str) -> str:
        scheme, _, token = str(authorization or "").partition(" ")
        if scheme.casefold() != "bearer" or not token.strip():
            raise AccountError(HTTPStatus.UNAUTHORIZED, "MISSING_TOKEN", "Sign in to continue.")
        return token.strip()

    def model_credential(self, authorization: str) -> dict[str, Any]:
        token = self._bearer_token(authorization)
        user = self.store.user_for_access_token(token)
        self.limiter.check(f"model-credential:{int(user['id'])}", 20, 60)
        return self.store.issue_model_credential(token)

    def _admin(self, authorization: str) -> dict[str, Any]:
        user = self.me(authorization)["user"]
        if str(user.get("role")) not in {"owner", "admin"}: raise AccountError(HTTPStatus.FORBIDDEN, "ADMIN_REQUIRED", "Administrator access is required.")
        return user

    def admin_me(self, authorization: str) -> dict[str, Any]: return {"user": self._admin(authorization)}
    def admin_overview(self, authorization: str) -> dict[str, Any]: self._admin(authorization); return self.store.admin_overview()
    def admin_users(self, authorization: str) -> dict[str, Any]: self._admin(authorization); return {"users": self.store.admin_users()}
    def admin_user(self, user_id: int, authorization: str) -> dict[str, Any]: self._admin(authorization); return {"user": self.store.admin_user(user_id)}
    def admin_sessions(self, authorization: str) -> dict[str, Any]: self._admin(authorization); return {"sessions": self.store.admin_sessions()}
    def admin_system(self, authorization: str) -> dict[str, Any]: self._admin(authorization); return self.store.admin_system()
    def admin_audit(self, authorization: str) -> dict[str, Any]: self._admin(authorization); return {"events": self.store.admin_audit()}
    def admin_feature_flags(self, authorization: str) -> dict[str, Any]: self._admin(authorization); return {"flags": self.store.admin_feature_flags()}
    def model_access(self, authorization: str) -> dict[str, Any]:
        token = self._bearer_token(authorization)
        user = self.store.user_for_model_token(token) if token.startswith("loom_model_") else self.store.user_for_access_token(token)
        return {"access": self.store.model_access(int(user["id"]))}

    def admin_user_model_access(self, user_id: int, authorization: str) -> dict[str, Any]:
        self._admin(authorization)
        return {"access": self.store.model_access(int(user_id))}

    def admin_set_user_model_access(self, body: dict[str, Any], authorization: str) -> dict[str, Any]:
        actor = self._admin(authorization)
        user_id = int(body.get("user_id") or 0)
        if user_id <= 0:
            raise AccountError(HTTPStatus.BAD_REQUEST, "INVALID_USER_ID", "user_id is required.")
        access = self.store.admin_set_model_access(actor, user_id, body.get("enabled"), body.get("models"))
        return {"access": access}

    @staticmethod
    def _user_id(body: dict[str, Any]) -> int:
        try: return int(body.get("user_id"))
        except (TypeError, ValueError): raise AccountError(HTTPStatus.BAD_REQUEST, "INVALID_USER_ID", "user_id must be an integer.") from None

    def admin_set_user_status(self, body: dict[str, Any], authorization: str) -> dict[str, Any]:
        actor=self._admin(authorization); return {"user": self.store.admin_set_user_status(actor, self._user_id(body), str(body.get("status") or ""))}
    def admin_set_user_status_by_id(self, user_id: int, status: str, authorization: str) -> dict[str, Any]:
        actor=self._admin(authorization); return {"user": self.store.admin_set_user_status(actor, int(user_id), status)}
    def admin_set_user_role(self, body: dict[str, Any], authorization: str) -> dict[str, Any]:
        actor=self._admin(authorization); return {"user": self.store.admin_set_user_role(actor, self._user_id(body), str(body.get("role") or ""))}
    def admin_revoke_user_sessions(self, body: dict[str, Any], authorization: str) -> dict[str, Any]:
        actor=self._admin(authorization); return {"ok": True, "revoked": self.store.admin_revoke_user_sessions(actor, self._user_id(body))}
    def admin_revoke_session(self, session_id: str, authorization: str) -> dict[str, Any]:
        actor=self._admin(authorization); return {"ok": True, "revoked": self.store.admin_revoke_session(actor, session_id)}
    def admin_set_feature_flag(self, body: dict[str, Any], authorization: str) -> dict[str, Any]:
        actor=self._admin(authorization); return {"flag": self.store.admin_set_feature_flag(actor, str(body.get("key") or ""), body.get("enabled"), body.get("value"))}

    def search(self, body: dict[str, Any], authorization: str) -> dict[str, Any]:
        user = self.me(authorization)["user"]
        query = " ".join(str(body.get("query") or "").split())
        count = body.get("count", 8)
        if not query or len(query) > 400 or len(query.split()) > 50:
            raise AccountError(400, "INVALID_SEARCH_QUERY", "Search query must contain 1–400 characters and at most 50 words.")
        if isinstance(count, bool) or not isinstance(count, int) or not 1 <= count <= 20:
            raise AccountError(400, "INVALID_SEARCH_COUNT", "Search result count must be within 1–20.")
        self.limiter.check(f"search:{user['id']}", 30, 60)
        try:
            return shared_search(query, count)
        except SearchServiceError as exc:
            raise AccountError(exc.status, exc.code, str(exc)) from None


class AccountRequestHandler(BaseHTTPRequestHandler):
    server_version = "LoomAccount/1"

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
            return peer
        forwarded = self.headers.get("X-Real-IP") or self.headers.get("X-Forwarded-For") or ""
        candidate = forwarded.split(",")[0].strip()
        return candidate or peer

    def _dispatch(self) -> dict[str, Any]:
        path = self.path.split("?", 1)[0].rstrip("/") or "/"
        if self.command == "GET" and path == "/healthz":
            return {"ok": True}
        authorization = self.headers.get("Authorization") or ""
        if self.command == "GET" and path == "/v1/auth/capabilities": return self.application.capabilities()
        if self.command == "GET" and path == "/v1/auth/me": return self.application.me(authorization)
        if self.command == "GET" and path == "/v1/admin/me": return self.application.admin_me(authorization)
        if self.command == "GET" and path == "/v1/admin/overview": return self.application.admin_overview(authorization)
        if self.command == "GET" and path == "/v1/admin/users": return self.application.admin_users(authorization)
        if self.command == "GET" and (match := re.fullmatch(r"/v1/admin/users/(\d+)", path)):
            return self.application.admin_user(int(match.group(1)), authorization)
        if self.command == "GET" and path == "/v1/admin/sessions": return self.application.admin_sessions(authorization)
        if self.command == "GET" and path == "/v1/admin/system": return self.application.admin_system(authorization)
        if self.command == "GET" and path == "/v1/admin/audit": return self.application.admin_audit(authorization)
        if self.command == "GET" and path == "/v1/admin/feature-flags": return self.application.admin_feature_flags(authorization)
        if self.command == "GET" and path == "/v1/models/access": return self.application.model_access(authorization)
        if self.command == "GET" and (match := re.fullmatch(r"/v1/admin/users/(\d+)/model-access", path)):
            return self.application.admin_user_model_access(int(match.group(1)), authorization)
        if self.command != "POST": raise AccountError(HTTPStatus.NOT_FOUND, "NOT_FOUND", "Endpoint not found.")

        body = self._json_body()
        if path == "/v1/models/credential":
            return self.application.model_credential(self.headers.get("Authorization") or "")
        if path == "/v1/search":
            return self.application.search(body, self.headers.get("Authorization") or "")
        if path == "/v1/auth/register":
            return self.application.register(body, self._client_key())
        if path == "/v1/auth/register/start":
            return self.application.register_start(body, self._client_key())
        if path == "/v1/auth/verify-email":
            return self.application.verify_email(body, self._client_key())
        if path == "/v1/auth/resend-email":
            return self.application.resend_email(body, self._client_key())
        if path == "/v1/auth/forgot-password":
            return self.application.forgot_password(body, self._client_key())
        if path == "/v1/auth/reset-password":
            return self.application.reset_password(body, self._client_key())
        if path == "/v1/auth/oauth/exchange":
            return self.application.oauth_exchange(body, self._client_key())
        if path == "/v1/auth/login":
            return self.application.login(body, self._client_key())
        if path == "/v1/auth/refresh":
            return self.application.refresh(body, self._client_key())
        if path == "/v1/auth/logout": return self.application.logout(body)
        if match := re.fullmatch(r"/v1/admin/users/(\d+)/(disable|enable)", path):
            return self.application.admin_set_user_status_by_id(int(match.group(1)), "disabled" if match.group(2) == "disable" else "active", authorization)
        if match := re.fullmatch(r"/v1/admin/sessions/([^/]+)/revoke", path):
            return self.application.admin_revoke_session(match.group(1), authorization)
        if path == "/v1/auth/device-pair/issue": return self.application.issue_device_pair(authorization, self._client_key())
        if path == "/v1/auth/device-pair/exchange": return self.application.exchange_device_pair(body, self._client_key())
        if path == "/v1/admin/users/status": return self.application.admin_set_user_status(body, authorization)
        if path == "/v1/admin/users/role": return self.application.admin_set_user_role(body, authorization)
        if path == "/v1/admin/users/revoke-sessions": return self.application.admin_revoke_user_sessions(body, authorization)
        if path == "/v1/admin/feature-flags": return self.application.admin_set_feature_flag(body, authorization)
        if path == "/v1/admin/users/model-access": return self.application.admin_set_user_model_access(body, authorization)
        raise AccountError(HTTPStatus.NOT_FOUND, "NOT_FOUND", "Endpoint not found.")

    def _redirect(self, location: str) -> None:
        self.send_response(HTTPStatus.FOUND)
        self.send_header("Location", str(location))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self) -> None:
        parsed = urlsplit(self.path)
        path = parsed.path.rstrip("/") or "/"
        match = re.fullmatch(r"/v1/auth/oauth/(google|github)/start", path)
        if match:
            try:
                query = parse_qs(parsed.query)
                return_to = str((query.get("return_to") or [""])[0])
                self._redirect(self.application.oauth_start(match.group(1), return_to))
            except AccountError as exc:
                self._write_json(exc.status, {"error": {"code": exc.code, "message": exc.message}})
            return
        match = re.fullmatch(r"/v1/auth/oauth/(google|github)/callback", path)
        if match:
            query = {key: str(values[0]) for key, values in parse_qs(parsed.query).items() if values}
            self._redirect(self.application.oauth_callback(match.group(1), query))
            return
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
        public_base_url=os.environ.get("LOOM_ACCOUNT_PUBLIC_BASE_URL", "https://account.smirel.com/v1").rstrip("/"),
        web_origin=os.environ.get("LOOM_WEB_ORIGIN", "https://loom.smirel.com").rstrip("/"),
        email_provider=os.environ.get("LOOM_EMAIL_PROVIDER", "").strip().casefold(),
        email_from=os.environ.get("LOOM_EMAIL_FROM", "").strip(),
        resend_api_key=os.environ.get("LOOM_RESEND_API_KEY", "").strip(),
        smtp_host=os.environ.get("LOOM_SMTP_HOST", "").strip(),
        smtp_port=int(os.environ.get("LOOM_SMTP_PORT", "587")),
        smtp_username=os.environ.get("LOOM_SMTP_USERNAME", "").strip(),
        smtp_password=os.environ.get("LOOM_SMTP_PASSWORD", ""),
        smtp_tls=os.environ.get("LOOM_SMTP_TLS", "1") != "0",
        google_client_id=os.environ.get("LOOM_GOOGLE_CLIENT_ID", "").strip(),
        google_client_secret=os.environ.get("LOOM_GOOGLE_CLIENT_SECRET", "").strip(),
        github_client_id=os.environ.get("LOOM_GITHUB_CLIENT_ID", "").strip(),
        github_client_secret=os.environ.get("LOOM_GITHUB_CLIENT_SECRET", "").strip(),
        legacy_registration_enabled=os.environ.get("LOOM_ALLOW_LEGACY_REGISTER", "1") != "0",
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
