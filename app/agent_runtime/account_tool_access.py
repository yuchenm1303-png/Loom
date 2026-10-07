from __future__ import annotations

"""Account-level entitlement gate for sensitive automation capabilities.

Computer Use and Browser Use are opt-in per Loom account. Desktop launches turn
this gate on explicitly; CLI/tests that do not opt into the account control plane
keep their existing behavior.
"""

import json
import os
import threading
import time
from typing import Mapping
from urllib.parse import urlsplit, urlunsplit
from urllib.request import Request, urlopen

_TARGET_CAPABILITIES = frozenset({"computerUse", "browserUse"})
_CACHE_TTL_SECONDS = 2.0
_GUARD = threading.RLock()
_CACHE_KEY = ""
_CACHE_EXPIRES_AT = 0.0
_CACHE_ACCESS: dict[str, bool] = {"computerUse": False, "browserUse": False}
_CREDENTIAL_OVERRIDE: str | None = None


def _truthy(value: object) -> bool:
    return str(value or "").strip().casefold() in {"1", "true", "yes", "on"}


def account_tool_access_enforced(environ: Mapping[str, str] | None = None) -> bool:
    values = os.environ if environ is None else environ
    return _truthy(values.get("LOOM_ACCOUNT_TOOL_ACCESS_ENFORCED"))


def set_account_tool_access_credential(value: str | None) -> None:
    """Replace the runtime credential after sign-in/sign-out without a restart."""

    global _CREDENTIAL_OVERRIDE, _CACHE_KEY, _CACHE_EXPIRES_AT, _CACHE_ACCESS
    credential = None if value is None else str(value).strip()
    with _GUARD:
        _CREDENTIAL_OVERRIDE = credential
        _CACHE_KEY = ""
        _CACHE_EXPIRES_AT = 0.0
        _CACHE_ACCESS = {"computerUse": False, "browserUse": False}


def _credential() -> str:
    with _GUARD:
        override = _CREDENTIAL_OVERRIDE
    if override is not None:
        return override
    return str(os.environ.get("LOOM_ACCOUNT_MODEL_CREDENTIAL") or "").strip()


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
    global _CACHE_KEY, _CACHE_EXPIRES_AT, _CACHE_ACCESS

    token = _credential()
    endpoint = _access_endpoint()
    key = endpoint + "\n" + token
    now = time.monotonic()

    with _GUARD:
        if not force_refresh and key == _CACHE_KEY and now < _CACHE_EXPIRES_AT:
            return dict(_CACHE_ACCESS)

    access = _denied()
    if endpoint and token:
        request = Request(
            endpoint,
            headers={
                "Accept": "application/json",
                "Authorization": "Bearer " + token,
            },
            method="GET",
        )
        try:
            with urlopen(request, timeout=2.5) as response:
                if int(getattr(response, "status", 200)) != 200:
                    raise RuntimeError("account tool access rejected")
                raw = response.read(64 * 1024 + 1)
                if len(raw) > 64 * 1024:
                    raise RuntimeError("account tool access response is too large")
            payload = json.loads(raw.decode("utf-8"))
            record = payload.get("access") if isinstance(payload, dict) else None
            if isinstance(record, dict):
                access = {
                    "computerUse": record.get("computerUse") is True,
                    "browserUse": record.get("browserUse") is True,
                }
        except Exception:
            # Sensitive automation is deliberately fail-closed. An account
            # service outage must never silently convert into an entitlement.
            access = _denied()

    with _GUARD:
        _CACHE_KEY = key
        _CACHE_ACCESS = dict(access)
        _CACHE_EXPIRES_AT = time.monotonic() + _CACHE_TTL_SECONDS
    return access


def account_capability_allowed(capability: str, *, force_refresh: bool = False) -> bool:
    name = str(capability or "").strip()
    if name not in _TARGET_CAPABILITIES:
        return True
    if not account_tool_access_enforced():
        return True
    return _fetch_access(force_refresh=force_refresh).get(name) is True


__all__ = [
    "account_capability_allowed",
    "account_tool_access_enforced",
    "set_account_tool_access_credential",
]
