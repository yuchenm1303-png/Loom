"""HTTP-level tests for the standalone Loom account service.

``test_loom_account_service.py`` drives :class:`AccountApplication` directly, so
it never touches the wire. These tests start a real ``ThreadingHTTPServer`` on
an ephemeral loopback port instead, which is what actually covers routing, HTTP
status codes, JSON encoding, the security response headers, the 64 KiB request
body ceiling, and the per-IP rate limiter.

Every test gets its own server and therefore its own limiter and database, so
rate-limit assertions cannot leak into unrelated cases.
"""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Iterator

import pytest

from services.loom_account.server import (
    AccountApplication,
    AccountConfig,
    AccountStore,
    LoomAccountServer,
)

_PASSWORD = "correct-horse-battery"


@pytest.fixture(autouse=True)
def _quiet_access_log(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOOM_ACCOUNT_QUIET", "1")


def _start(tmp_path: Path, trusted_proxies: tuple[str, ...] | None = None):
    application = AccountApplication(
        AccountStore(AccountConfig(db_path=tmp_path / "accounts.db"))
    )
    server = LoomAccountServer(("127.0.0.1", 0), application, trusted_proxies)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def _serve(server, thread) -> Iterator[str]:
    host, port = server.server_address[:2]
    try:
        yield f"http://{host}:{port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.fixture()
def base_url(tmp_path: Path) -> Iterator[str]:
    yield from _serve(*_start(tmp_path))


@pytest.fixture()
def untrusted_base_url(tmp_path: Path) -> Iterator[str]:
    """A server that trusts no proxy, so forwarding headers are ignored."""
    yield from _serve(*_start(tmp_path, trusted_proxies=()))


@pytest.fixture()
def cidr_base_url(tmp_path: Path) -> Iterator[str]:
    """A server that trusts a whole range, as a Docker bridge network requires."""
    yield from _serve(*_start(tmp_path, trusted_proxies=("127.0.0.0/8",)))


def _call(
    base_url: str,
    path: str,
    *,
    method: str = "POST",
    body: Any = None,
    token: str = "",
    raw: bytes | None = None,
    real_ip: str = "",
) -> tuple[int, dict[str, Any], Any]:
    """Perform one request and return ``(status, decoded_body, headers)``.

    Error responses are returned rather than raised so a test can assert on the
    status code and error envelope together. ``real_ip`` sets the forwarding
    header a reverse proxy would add.
    """
    data = raw if raw is not None else (json.dumps(body).encode("utf-8") if body is not None else None)
    request = urllib.request.Request(base_url + path, data=data, method=method)
    if data is not None:
        request.add_header("Content-Type", "application/json")
    if token:
        request.add_header("Authorization", "Bearer " + token)
    if real_ip:
        request.add_header("X-Real-IP", real_ip)
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, json.loads(response.read() or b"{}"), response.headers
    except urllib.error.HTTPError as error:
        payload = error.read()
        try:
            parsed = json.loads(payload or b"{}")
        except json.JSONDecodeError:
            parsed = {}
        return error.code, parsed, error.headers


def _register(base_url: str, email: str = "user@example.com") -> dict[str, Any]:
    status, payload, _ = _call(base_url, "/v1/auth/register", body={"email": email, "password": _PASSWORD})
    assert status == 200, payload
    return payload


def test_healthz_needs_no_credentials(base_url: str) -> None:
    status, payload, _ = _call(base_url, "/healthz", method="GET")
    assert status == 200
    assert payload == {"ok": True}


def test_full_sign_in_lifecycle_over_http(base_url: str) -> None:
    registered = _register(base_url, "Lifecycle@Example.com")
    assert registered["user"]["email"] == "lifecycle@example.com"
    assert registered["token_type"] == "Bearer"
    access, refresh = registered["access_token"], registered["refresh_token"]

    status, payload, _ = _call(base_url, "/v1/auth/me", method="GET", token=access)
    assert status == 200
    assert payload["user"]["id"] == registered["user"]["id"]

    status, payload, _ = _call(base_url, "/v1/auth/me", method="GET")
    assert status == 401
    assert payload["error"]["code"] == "MISSING_TOKEN"

    status, refreshed, _ = _call(base_url, "/v1/auth/refresh", body={"refresh_token": refresh})
    assert status == 200
    assert refreshed["access_token"] != access
    assert refreshed["refresh_token"] != refresh

    # Refresh tokens rotate, and rotation invalidates the access token that was
    # issued alongside the token just spent.
    status, payload, _ = _call(base_url, "/v1/auth/refresh", body={"refresh_token": refresh})
    assert status == 401
    assert payload["error"]["code"] == "INVALID_REFRESH_TOKEN"

    status, _, _ = _call(base_url, "/v1/auth/me", method="GET", token=access)
    assert status == 401

    status, _, _ = _call(base_url, "/v1/auth/me", method="GET", token=refreshed["access_token"])
    assert status == 200

    status, payload, _ = _call(base_url, "/v1/auth/logout", body={"refresh_token": refreshed["refresh_token"]})
    assert status == 200
    assert payload == {"ok": True}

    status, payload, _ = _call(base_url, "/v1/auth/refresh", body={"refresh_token": refreshed["refresh_token"]})
    assert status == 401
    assert payload["error"]["code"] == "INVALID_REFRESH_TOKEN"


def test_responses_carry_no_store_and_nosniff(base_url: str) -> None:
    _, _, headers = _call(base_url, "/healthz", method="GET")
    assert headers["Cache-Control"] == "no-store"
    assert headers["X-Content-Type-Options"] == "nosniff"


def test_error_contract_over_http(base_url: str) -> None:
    _register(base_url)

    status, payload, _ = _call(base_url, "/v1/auth/register", body={"email": "not-an-email", "password": _PASSWORD})
    assert (status, payload["error"]["code"]) == (400, "INVALID_EMAIL")

    status, payload, _ = _call(base_url, "/v1/auth/register", body={"email": "weak@example.com", "password": "short"})
    assert (status, payload["error"]["code"]) == (400, "WEAK_PASSWORD")

    status, payload, _ = _call(base_url, "/v1/auth/register", body={"email": "USER@example.com", "password": _PASSWORD})
    assert (status, payload["error"]["code"]) == (409, "EMAIL_EXISTS")

    status, payload, _ = _call(base_url, "/v1/auth/login", body={"email": "user@example.com", "password": "nope"})
    assert (status, payload["error"]["code"]) == (401, "INVALID_CREDENTIALS")

    status, payload, _ = _call(base_url, "/v1/auth/refresh", body={"refresh_token": "loom_refresh_bogus"})
    assert (status, payload["error"]["code"]) == (401, "INVALID_REFRESH_TOKEN")

    status, payload, _ = _call(base_url, "/v1/auth/does-not-exist", body={})
    assert (status, payload["error"]["code"]) == (404, "NOT_FOUND")

    status, payload, _ = _call(base_url, "/v1/auth/login", method="GET")
    assert (status, payload["error"]["code"]) == (404, "NOT_FOUND")


def test_malformed_and_non_object_json_are_rejected(base_url: str) -> None:
    status, payload, _ = _call(base_url, "/v1/auth/login", raw=b"{oops")
    assert (status, payload["error"]["code"]) == (400, "INVALID_JSON")

    status, payload, _ = _call(base_url, "/v1/auth/login", raw=b'["not", "an", "object"]')
    assert (status, payload["error"]["code"]) == (400, "INVALID_JSON")


def test_oversized_body_is_rejected_before_parsing(base_url: str) -> None:
    huge = json.dumps({"email": "a@b.com", "password": "x" * 70_000}).encode("utf-8")
    status, payload, _ = _call(base_url, "/v1/auth/login", raw=huge)
    assert (status, payload["error"]["code"]) == (413, "REQUEST_TOO_LARGE")


def test_login_rate_limit_returns_429(base_url: str) -> None:
    codes = [
        _call(base_url, "/v1/auth/login", body={"email": "nobody@example.com", "password": "wrong-password"})[0]
        for _ in range(24)
    ]
    assert codes[:20] == [401] * 20
    assert codes[20:] == [429] * 4


def test_register_rate_limit_returns_429(base_url: str) -> None:
    codes = [
        _call(base_url, "/v1/auth/register", body={"email": f"r{index}@example.com", "password": _PASSWORD})[0]
        for index in range(8)
    ]
    assert codes[:5] == [200] * 5
    assert codes[5:] == [429] * 3


def test_refresh_rate_limit_returns_429(base_url: str) -> None:
    refresh = _register(base_url)["refresh_token"]
    codes = [
        _call(base_url, "/v1/auth/refresh", body={"refresh_token": refresh})[0]
        for _ in range(33)
    ]
    # The first call rotates the token, so every later call is a rejected reuse;
    # the limiter still counts each attempt and trips on the 31st.
    assert codes[0] == 200
    assert codes[1:30] == [401] * 29
    assert codes[30:] == [429] * 3


def test_tokens_are_never_stored_in_plaintext(base_url: str, tmp_path: Path) -> None:
    registered = _register(base_url)
    refreshed = _call(
        base_url,
        "/v1/auth/refresh",
        body={"refresh_token": registered["refresh_token"]},
    )[1]

    raw = (tmp_path / "accounts.db").read_bytes()
    for token in (
        registered["access_token"],
        registered["refresh_token"],
        refreshed["access_token"],
        refreshed["refresh_token"],
    ):
        assert token.encode("utf-8") not in raw


def test_unknown_email_costs_the_same_as_a_known_one(base_url: str) -> None:
    """Failed logins must not be an email-enumeration oracle.

    A registered address and an unregistered one run the same PBKDF2 work, so
    their latencies should be comparable. Skipping the KDF for the unknown
    address (the short-circuit this guards against) would make it near-instant.
    """
    import time

    _register(base_url, "timing@example.com")

    def fastest(email: str) -> float:
        best = float("inf")
        for _ in range(2):
            started = time.perf_counter()
            status, _, _ = _call(
                base_url,
                "/v1/auth/login",
                body={"email": email, "password": "definitely-wrong"},
            )
            best = min(best, time.perf_counter() - started)
            assert status == 401
        return best

    known = fastest("timing@example.com")
    unknown = fastest("nobody-here@example.com")
    assert unknown >= known * 0.5, f"unknown={unknown:.4f}s known={known:.4f}s"


def test_throttled_caller_recovers_after_the_window(
    base_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from services.loom_account import server as account_server

    clock = {"now": 5_000.0}
    monkeypatch.setattr(account_server.time, "monotonic", lambda: clock["now"])

    for _ in range(20):
        assert _call(base_url, "/v1/auth/login", body={"email": "a@b.com", "password": "wrong"})[0] == 401
    assert _call(base_url, "/v1/auth/login", body={"email": "a@b.com", "password": "wrong"})[0] == 429

    clock["now"] += 61
    assert _call(base_url, "/v1/auth/login", body={"email": "a@b.com", "password": "wrong"})[0] == 401


def test_limiter_prunes_drained_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stale keys must not accumulate forever in the limiter's dict."""
    from services.loom_account import server as account_server

    clock = {"now": 1_000.0}
    monkeypatch.setattr(account_server.time, "monotonic", lambda: clock["now"])

    limiter = account_server.SlidingWindowLimiter()
    for index in range(limiter._SWEEP_EVERY + 5):
        limiter.check(f"key-{index}", 5, 60)
    assert len(limiter._events) == limiter._SWEEP_EVERY + 5

    # The sweep is amortised, so it only runs once another _SWEEP_EVERY calls
    # have gone through. Advance the clock past every old window first.
    clock["now"] += 61
    for _ in range(limiter._SWEEP_EVERY):
        limiter.check("fresh-key", 10_000, 60)
    assert list(limiter._events) == ["fresh-key"]


def test_forwarded_addresses_get_separate_buckets_behind_a_trusted_proxy(base_url: str) -> None:
    """Behind a reverse proxy every request arrives from 127.0.0.1.

    Keying the limiter on the peer address alone would collapse every client in
    the world into a single bucket, so a trusted proxy's X-Real-IP must be
    believed.
    """
    attempt = {"email": "nobody@example.com", "password": "wrong-password"}

    for _ in range(20):
        assert _call(base_url, "/v1/auth/login", body=attempt, real_ip="203.0.113.9")[0] == 401
    assert _call(base_url, "/v1/auth/login", body=attempt, real_ip="203.0.113.9")[0] == 429

    # A different client behind the same proxy keeps its own allowance.
    assert _call(base_url, "/v1/auth/login", body=attempt, real_ip="203.0.113.10")[0] == 401


def test_forwarded_addresses_are_ignored_from_an_untrusted_peer(untrusted_base_url: str) -> None:
    """A caller that can reach the service directly must not mint its own bucket."""
    attempt = {"email": "nobody@example.com", "password": "wrong-password"}

    for index in range(20):
        status, _, _ = _call(
            untrusted_base_url, "/v1/auth/login", body=attempt, real_ip=f"203.0.113.{index}"
        )
        assert status == 401
    # The same untrusted peer throughout, so one shared bucket despite 20
    # different claimed addresses.
    assert _call(untrusted_base_url, "/v1/auth/login", body=attempt, real_ip="203.0.113.200")[0] == 429


def test_a_cidr_range_may_be_trusted_as_a_proxy(cidr_base_url: str) -> None:
    """A proxy in Docker arrives from a bridge address, not from loopback.

    Docker assigns container addresses out of a range and they move on restart,
    so the trusted-proxy setting has to accept a network rather than one
    address. If it does not, the forwarded client address is ignored and every
    caller in the world shares a single rate-limit bucket.
    """
    attempt = {"email": "nobody@example.com", "password": "wrong-password"}

    for _ in range(20):
        assert _call(cidr_base_url, "/v1/auth/login", body=attempt, real_ip="203.0.113.9")[0] == 401
    assert _call(cidr_base_url, "/v1/auth/login", body=attempt, real_ip="203.0.113.9")[0] == 429
    assert _call(cidr_base_url, "/v1/auth/login", body=attempt, real_ip="203.0.113.10")[0] == 401


def test_unparseable_trusted_proxy_entries_are_skipped() -> None:
    from services.loom_account.server import _is_trusted_proxy, _trusted_proxy_networks

    networks = _trusted_proxy_networks(["127.0.0.1", "172.18.0.0/16", "not-an-address", ""])

    assert _is_trusted_proxy("127.0.0.1", networks)
    assert _is_trusted_proxy("172.18.0.2", networks)
    assert not _is_trusted_proxy("203.0.113.9", networks)
    assert not _is_trusted_proxy("garbage", networks)
