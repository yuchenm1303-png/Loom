from __future__ import annotations

"""Account-level entitlement gate for sensitive automation capabilities.

Computer Use and Browser Use are opt-in per Loom account. Desktop launches turn
this gate on explicitly; CLI/tests that do not opt into the account control plane
keep their existing behavior.
"""

import json
import errno
import os
import re
import socket
import ssl
import threading
import time
from typing import Mapping
from urllib.parse import urlsplit, urlunsplit
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

_TARGET_CAPABILITIES = frozenset({"computerUse", "browserUse"})
_CACHE_TTL_SECONDS = 120.0
_ERROR_TTL_SECONDS = 5.0
# A brief outage of the account service (this machine reaches it through a
# slow proxy) keeps the answer the service last confirmed for this identity,
# instead of failing every browser step. Never longer, never for a new identity,
# and a confirmed revocation is kept just the same.
_OUTAGE_GRACE_SECONDS = 900.0
_OUTAGE_RECHECK_SECONDS = 60.0
_LAST_CONFIRMED: tuple[str, dict[str, bool], float] | None = None
_GUARD = threading.RLock()
_CACHE_KEY = ""
_CACHE_EXPIRES_AT = 0.0
_CACHE_ACCESS: dict[str, bool] = {"computerUse": False, "browserUse": False}
_CREDENTIAL_OVERRIDE: str | None = None
_DIAGNOSTIC: dict[str, object] = {"status": "not_checked"}
_FETCH_LOCK = threading.Lock()
_GENERATION = 0
_REQUEST_TIMEOUT_SECONDS = 5.0


def _network_diagnostic(exc: Exception) -> dict[str, object]:
    """Classify transport failures without copying messages, URLs or credentials."""
    reason = exc.reason if isinstance(exc, URLError) else exc
    category = "unknown"
    if isinstance(reason, ssl.SSLCertVerificationError):
        category = "tls_certificate"
    elif isinstance(reason, ssl.SSLError):
        category = "tls"
    elif isinstance(reason, socket.gaierror):
        category = "dns"
    elif isinstance(reason, TimeoutError):
        category = "timeout"
    elif isinstance(reason, ConnectionRefusedError):
        category = "connection_refused"
    elif isinstance(reason, ConnectionResetError):
        category = "connection_reset"
    elif isinstance(reason, ConnectionAbortedError):
        category = "connection_aborted"
    elif isinstance(reason, OSError):
        category = {
            errno.ENETUNREACH: "network_unreachable", errno.EHOSTUNREACH: "host_unreachable",
            errno.ETIMEDOUT: "timeout", 10060: "timeout", 10061: "connection_refused",
            10054: "connection_reset", 10051: "network_unreachable", 10065: "host_unreachable",
        }.get(getattr(reason, "winerror", None) or reason.errno, "transport")
    # urllib sometimes supplies only a textual reason. Recognize categories,
    # never persist that string (it may contain proxy userinfo or a bearer token).
    elif isinstance(reason, str):
        lowered = reason.casefold()
        if "timed out" in lowered or "timeout" in lowered:
            category = "timeout"
        elif "certificate verify failed" in lowered:
            category = "tls_certificate"
        elif "name resolution" in lowered or "getaddrinfo failed" in lowered:
            category = "dns"
    result: dict[str, object] = {"error_type": type(exc).__name__,
                                "network_error": category, "reason_type": type(reason).__name__}
    for attr, field in (("errno", "os_error_code"), ("winerror", "win32_error"),
                        ("verify_code", "tls_verify_code")):
        value = getattr(reason, attr, None)
        if isinstance(value, int) and not isinstance(value, bool):
            result[field] = value
    return result


def _truthy(value: object) -> bool:
    return str(value or "").strip().casefold() in {"1", "true", "yes", "on"}


def account_tool_access_enforced(environ: Mapping[str, str] | None = None) -> bool:
    values = os.environ if environ is None else environ
    return _truthy(values.get("LOOM_ACCOUNT_TOOL_ACCESS_ENFORCED"))


def set_account_tool_access_credential(value: str | None) -> None:
    """Replace the runtime credential after sign-in/sign-out without a restart."""

    global _CREDENTIAL_OVERRIDE, _CACHE_KEY, _CACHE_EXPIRES_AT, _CACHE_ACCESS, _GENERATION, _DIAGNOSTIC, _FETCH_LOCK
    global _LAST_CONFIRMED
    credential = None if value is None else str(value).strip()
    with _GUARD:
        if credential == _CREDENTIAL_OVERRIDE:
            return
        _GENERATION += 1
        # An old account's slow DNS/network request must not block the new
        # identity. Its result remains fenced by generation below.
        _FETCH_LOCK = threading.Lock()
        _CREDENTIAL_OVERRIDE = credential
        _CACHE_KEY = ""
        _CACHE_EXPIRES_AT = 0.0
        _CACHE_ACCESS = {"computerUse": False, "browserUse": False}
        _DIAGNOSTIC = {"status": "not_checked"}
        _LAST_CONFIRMED = None


def _credential() -> str:
    with _GUARD:
        override = _CREDENTIAL_OVERRIDE
    if override is not None:
        return override
    return str(os.environ.get("LOOM_ACCOUNT_AUTOMATION_CREDENTIAL", os.environ.get("LOOM_ACCOUNT_MODEL_CREDENTIAL") or "")).strip()


def _access_endpoint() -> str:
    raw = str(os.environ.get("LOOM_ACCOUNT_API_BASE_URL") or "").strip().rstrip("/")
    if not raw:
        return ""
    try:
        parsed = urlsplit(raw)
    except ValueError:
        return ""
    hostname = str(parsed.hostname or "").casefold()
    loopback = hostname in {"127.0.0.1", "localhost", "::1"}
    if parsed.username or parsed.password:
        return ""
    if parsed.scheme != "https" and not (parsed.scheme == "http" and loopback):
        return ""
    path = parsed.path.rstrip("/")
    if not path:
        path = "/v1"
    return urlunsplit((parsed.scheme, parsed.netloc, path + "/tools/access", "", ""))


def _denied() -> dict[str, bool]:
    return {"computerUse": False, "browserUse": False}


def _fetch_access(*, force_refresh: bool = False) -> dict[str, bool]:
    # Single flight; callers recheck the cache after waiting for another query.
    with _GUARD:
        flight, generation = _FETCH_LOCK, _GENERATION
    with flight:
        return _fetch_access_locked(force_refresh=force_refresh, expected_generation=generation)


def _fetch_access_locked(*, force_refresh: bool = False, expected_generation: int | None = None) -> dict[str, bool]:
    global _CACHE_KEY, _CACHE_EXPIRES_AT, _CACHE_ACCESS, _DIAGNOSTIC, _LAST_CONFIRMED

    with _GUARD:
        if expected_generation is not None and expected_generation != _GENERATION:
            return _denied()
        # Capture identity and credential atomically, including A -> B -> A.
        token = _credential()
        endpoint = _access_endpoint()
        key = endpoint + "\n" + token
        now = time.monotonic()
        generation = _GENERATION
        if not force_refresh and key == _CACHE_KEY and now < _CACHE_EXPIRES_AT:
            return dict(_CACHE_ACCESS)

    access = _denied()
    diagnostic: dict[str, object] = {"status": "credential_missing" if not token else "configuration_invalid"}
    if endpoint and token:
        started = time.monotonic()
        request = Request(
            endpoint,
            headers={
                "Accept": "application/json",
                "Authorization": "Bearer " + token,
            },
            method="GET",
        )
        try:
            with urlopen(request, timeout=_REQUEST_TIMEOUT_SECONDS) as response:
                if int(getattr(response, "status", 200)) != 200:
                    raise RuntimeError("account tool access rejected")
                raw = response.read(64 * 1024 + 1)
                if len(raw) > 64 * 1024:
                    raise RuntimeError("account tool access response is too large")
            payload = json.loads(raw.decode("utf-8"))
            record = payload.get("access") if isinstance(payload, dict) else None
            if isinstance(record, dict):
                if not all(isinstance(record.get(name), bool) for name in _TARGET_CAPABILITIES):
                    raise ValueError("invalid authorization response")
                access = {
                    "computerUse": record.get("computerUse") is True,
                    "browserUse": record.get("browserUse") is True,
                }
                diagnostic = {"status": "confirmed", "checked_at": time.time()}
                if record.get("source") in {"default", "override"}:
                    diagnostic["source"] = record["source"]
                if isinstance(record.get("updated_at"), int):
                    diagnostic["updated_at"] = record["updated_at"]
                subject = payload.get("subject_ref")
                if isinstance(subject, str) and re.fullmatch(r"[a-f0-9]{24}", subject):
                    diagnostic["subject_ref"] = subject
            else:
                raise ValueError("missing authorization response")
        except HTTPError as exc:
            diagnostic = {"status": "authentication_failed" if exc.code == 401 else "service_unavailable", "http_status": exc.code}
            # Keep only a bounded machine code, never the body/message/token.
            try:
                body = json.loads(exc.read(8193))
                code = body.get("error", {}).get("code")
                if isinstance(code, str) and re.fullmatch(r"[A-Z][A-Z0-9_]{0,63}", code):
                    diagnostic["server_error_code"] = code
            except Exception:
                pass
            access = _denied()
        except ValueError:
            diagnostic = {"status": "invalid_response"}
            access = _denied()
        except Exception as exc:
            # Sensitive automation is deliberately fail-closed. An account
            # service outage must never silently convert into an entitlement.
            access = _denied()
            diagnostic = {"status": "service_unavailable", **_network_diagnostic(exc)}
        finally:
            diagnostic["request_elapsed_ms"] = round((time.monotonic() - started) * 1000)
            # Socket timeout is not an end-to-end DNS/request deadline.
            diagnostic["request_timeout_seconds"] = _REQUEST_TIMEOUT_SECONDS

    with _GUARD:
        if generation != _GENERATION:
            # Never publish results for a signed-out or different account.
            return _denied()
        ttl = _CACHE_TTL_SECONDS if diagnostic["status"] == "confirmed" else _ERROR_TTL_SECONDS
        if diagnostic["status"] == "confirmed":
            _LAST_CONFIRMED = (key, dict(access), time.monotonic())
        elif (_outage(diagnostic) and _LAST_CONFIRMED is not None and _LAST_CONFIRMED[0] == key
              and time.monotonic() - _LAST_CONFIRMED[2] < _OUTAGE_GRACE_SECONDS):
            access = dict(_LAST_CONFIRMED[1])
            diagnostic = {**diagnostic, "access_from": "recent_confirmation",
                          "confirmed_seconds_ago": round(time.monotonic() - _LAST_CONFIRMED[2])}
            # Each retry may wait the full request timeout; do not pay it every step.
            ttl = _OUTAGE_RECHECK_SECONDS
        _CACHE_KEY = key
        _CACHE_ACCESS = dict(access)
        _DIAGNOSTIC = {**diagnostic, "endpoint": endpoint, "credential_generation": generation}
        _CACHE_EXPIRES_AT = time.monotonic() + ttl
    return access


def _outage(diagnostic: Mapping[str, object]) -> bool:
    """The service could not answer, as opposed to answering no (401, revoked, bad config)."""
    if diagnostic.get("status") != "service_unavailable":
        return False
    status = diagnostic.get("http_status")
    return status is None or (isinstance(status, int) and status >= 500)


def account_tool_access_status() -> dict[str, object]:
    if not account_tool_access_enforced():
        return {"status": "not_enforced", "access": {name: True for name in _TARGET_CAPABILITIES}}
    with _GUARD:
        generation = _GENERATION
    access = _fetch_access()
    with _GUARD:
        if generation != _GENERATION:
            return {"status": "identity_changed", "access": _denied(),
                    "credential_generation": _GENERATION, "refresh_in_seconds": 0.0}
        return {**_DIAGNOSTIC, "access": access, "refresh_in_seconds": max(0.0, _CACHE_EXPIRES_AT - time.monotonic())}


def account_capability_allowed(capability: str, *, force_refresh: bool = False) -> bool:
    name = str(capability or "").strip()
    if name not in _TARGET_CAPABILITIES:
        return True
    if not account_tool_access_enforced():
        return True
    return _fetch_access(force_refresh=force_refresh).get(name) is True


__all__ = [
    "account_capability_allowed",
    "account_tool_access_status",
    "account_tool_access_enforced",
    "set_account_tool_access_credential",
]
