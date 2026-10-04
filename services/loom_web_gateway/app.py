from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import httpx
from fastapi import FastAPI, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from starlette.middleware.gzip import GZipMiddleware

ACCOUNT_BASE_URL = os.environ.get("LOOM_ACCOUNT_API_BASE_URL", "https://account.smirel.com/v1").rstrip("/")
STATIC_DIR = Path(os.environ.get("LOOM_WEB_STATIC_DIR", "/app/static")).resolve()
WEB_ORIGIN = os.environ.get("LOOM_WEB_ORIGIN", "https://loom.smirel.com").rstrip("/")
BUILD_SHA = os.environ.get("LOOM_BUILD_SHA", "unknown")
TELEMETRY_SECRET = str(os.environ.get("LOOM_TELEMETRY_SECRET", "") or "").strip()
ACCESS_COOKIE = "loom_web_access"
REFRESH_COOKIE = "loom_web_refresh"
ACCESS_MAX_AGE = 15 * 60
REFRESH_MAX_AGE = 30 * 24 * 60 * 60

# Loom Web is a thin browser client for the same local Loom Host used by Desktop.
# The gateway authenticates the account and relays WebSocket frames only; it never
# starts a second Agent Runtime on the server.

app = FastAPI(title="Loom Web", docs_url=None, redoc_url=None, openapi_url=None)


class StaticAssetCompression(GZipMiddleware):
    async def __call__(self, scope, receive, send):
        # Compress public bundles only; account responses and relay sockets pass
        # through unchanged. Large CSS otherwise delays module execution.
        if scope["type"] == "http" and scope.get("path", "").startswith("/assets/"):
            await super().__call__(scope, receive, send)
        else:
            await self.app(scope, receive, send)


app.add_middleware(StaticAssetCompression, minimum_size=1024, compresslevel=5)
logger = logging.getLogger("uvicorn.error")


def _snapshot(user: dict[str, Any] | None, *, reachable: bool = True) -> dict[str, Any]:
    return {
        "configured": True,
        "reachable": reachable,
        "authenticated": bool(user),
        "user": user,
        "serviceUrl": ACCOUNT_BASE_URL,
    }


def _error(code: str, message: str, status: int = 0) -> dict[str, Any]:
    payload: dict[str, Any] = {"code": code, "message": message}
    if status:
        payload["status"] = status
    return payload


def _device_id(value: Any) -> str:
    candidate = str(value or "").strip()
    if not candidate or len(candidate) > 128:
        return ""
    if any(character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_.:" for character in candidate):
        return ""
    return candidate


def _set_session_cookies(response: Response, payload: dict[str, Any]) -> None:
    access = str(payload.get("access_token") or "")
    refresh = str(payload.get("refresh_token") or "")
    if not access or not refresh:
        return
    access_age = max(60, int(payload.get("expires_in") or ACCESS_MAX_AGE))
    response.set_cookie(ACCESS_COOKIE, access, max_age=access_age, httponly=True, secure=True, samesite="strict", path="/")
    response.set_cookie(REFRESH_COOKIE, refresh, max_age=REFRESH_MAX_AGE, httponly=True, secure=True, samesite="strict", path="/")


def _clear_session_cookies(response: Response) -> None:
    response.delete_cookie(ACCESS_COOKIE, path="/", secure=True, httponly=True, samesite="strict")
    response.delete_cookie(REFRESH_COOKIE, path="/", secure=True, httponly=True, samesite="strict")


async def _account_request(method: str, path: str, *, token: str = "", json_body: Any = None) -> tuple[int, dict[str, Any]]:
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.request(method, f"{ACCOUNT_BASE_URL}{path}", headers=headers, json=json_body)
    except httpx.HTTPError:
        return 0, {"error": _error("ACCOUNT_SERVICE_UNREACHABLE", "Could not reach the Loom Account Service.")}
    try:
        payload = response.json()
    except ValueError:
        payload = {}
    return response.status_code, payload if isinstance(payload, dict) else {}


async def _telemetry_post(path: str, body: dict[str, Any]) -> dict[str, Any]:
    if not TELEMETRY_SECRET:
        return {}
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "X-Loom-Telemetry-Secret": TELEMETRY_SECRET,
    }
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.post(f"{ACCOUNT_BASE_URL}{path}", headers=headers, json=body)
        if response.status_code != 200:
            return {}
        payload = response.json()
        return payload if isinstance(payload, dict) else {}
    except (httpx.HTTPError, ValueError):
        return {}


def _health_number(value: Any, *, minimum: float = 0.0, maximum: float = 1_000_000_000_000_000.0) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number < minimum:
        return None
    return min(maximum, number)


def _health_payload(value: Any) -> dict[str, Any] | None:
    health = value if isinstance(value, dict) else {}
    if not health:
        return None
    capabilities = health.get("capabilities") if isinstance(health.get("capabilities"), dict) else {}
    result: dict[str, Any] = {
        "schema": int(_health_number(health.get("schema"), maximum=100) or 0),
        "os_release": str(health.get("osRelease") or "")[:120],
        "os_version": str(health.get("osVersion") or "")[:160],
        "arch": str(health.get("arch") or "")[:32],
        "system_uptime_seconds": int(_health_number(health.get("systemUptimeSeconds"), maximum=10_000_000_000) or 0),
        "cpu_percent": _health_number(health.get("cpuPercent"), maximum=100),
        "memory_total_bytes": int(_health_number(health.get("memoryTotalBytes")) or 0),
        "memory_used_bytes": int(_health_number(health.get("memoryUsedBytes")) or 0),
        "memory_percent": _health_number(health.get("memoryPercent"), maximum=100),
        "disk_total_bytes": int(_health_number(health.get("diskTotalBytes")) or 0),
        "disk_free_bytes": int(_health_number(health.get("diskFreeBytes")) or 0),
        "disk_percent": _health_number(health.get("diskPercent"), maximum=100),
        "host_rss_bytes": int(_health_number(health.get("hostRssBytes")) or 0),
        "host_heap_used_bytes": int(_health_number(health.get("hostHeapUsedBytes")) or 0),
        "host_cpu_percent": _health_number(health.get("hostCpuPercent"), maximum=100),
        "relay_rtt_ms": _health_number(health.get("relayRttMs"), maximum=60_000),
        "capabilities": {
            "browser": bool(capabilities.get("browser")),
            "computer_use": bool(capabilities.get("computerUse")),
            "terminal": bool(capabilities.get("terminal")),
            "files": bool(capabilities.get("files")),
        },
    }
    return result


def _device_payload(peer: "DevicePeer", event: str, health: Any = None) -> dict[str, Any]:
    device = peer.device if isinstance(peer.device, dict) else {}
    payload = {
        "event": event,
        "user_id": peer.user_id,
        "device_id": _device_id(device.get("id")),
        "name": str(device.get("name") or "")[:160],
        "platform": str(device.get("platform") or "")[:64],
        "app_version": str(device.get("version") or "")[:64],
        "host_version": str(device.get("hostVersion") or "")[:64],
        "host_mode": str(device.get("hostMode") or "")[:64],
        "host_protocol": int(device.get("hostProtocol") or 0),
        "bootstrap_protocol": int(device.get("bootstrapProtocol") or 0),
    }
    mapped_health = _health_payload(health)
    if mapped_health is not None:
        payload["health"] = mapped_health
    return payload


async def _record_device(peer: "DevicePeer", event: str, health: Any = None) -> None:
    body = _device_payload(peer, event, health)
    if body["device_id"]:
        await _telemetry_post("/telemetry/device", body)


def _notification_telemetry(peer: "DevicePeer", payload: dict[str, Any]) -> dict[str, Any] | None:
    method = str(payload.get("method") or "")
    params = payload.get("params") if isinstance(payload.get("params"), dict) else {}
    device_id = _device_id((peer.device or {}).get("id"))
    base: dict[str, Any] = {"user_id": peer.user_id, "device_id": device_id}
    if method == "thread/started":
        thread = params.get("thread") if isinstance(params.get("thread"), dict) else {}
        thread_id = str(thread.get("id") or "").strip()
        if not thread_id:
            return None
        return {
            **base,
            "event": "thread.started",
            "thread_id": thread_id,
            "model": str(thread.get("model") or "")[:160],
            "provider": str(thread.get("modelProvider") or "")[:120],
            "at": thread.get("updatedAt") or thread.get("createdAt"),
        }
    if method == "turn/started":
        turn = params.get("turn") if isinstance(params.get("turn"), dict) else {}
        thread_id = str(params.get("threadId") or turn.get("threadId") or "").strip()
        turn_id = str(turn.get("id") or "").strip()
        if not thread_id or not turn_id:
            return None
        return {
            **base,
            "event": "turn.started",
            "thread_id": thread_id,
            "turn_id": turn_id,
            "model": str(turn.get("model") or "")[:160],
            "provider": str(turn.get("modelProvider") or "")[:120],
            "at": turn.get("startedAt"),
        }
    if method == "approval/requested":
        approval = params.get("approval") if isinstance(params.get("approval"), dict) else {}
        thread_id = str(params.get("threadId") or "").strip()
        turn_id = str(params.get("turnId") or "").strip()
        if not thread_id or not turn_id:
            return None
        call_id = str(approval.get("callId") or approval.get("call_id") or approval.get("id") or "")[:160]
        return {
            **base,
            "event": "approval.requested",
            "thread_id": thread_id,
            "turn_id": turn_id,
            "call_id": call_id,
            "event_key": f"approval:{turn_id}:{call_id}" if call_id else f"approval:{turn_id}",
            "tool_name": str(approval.get("toolName") or approval.get("tool") or "")[:160],
        }
    if method == "item/completed":
        item = params.get("item") if isinstance(params.get("item"), dict) else {}
        if str(item.get("type") or "") != "tool_call":
            return None
        thread_id = str(item.get("threadId") or "").strip()
        turn_id = str(item.get("turnId") or "").strip()
        if not thread_id or not turn_id:
            return None
        call_id = str(item.get("callId") or "")[:160]
        item_id = str(item.get("id") or call_id or "")[:180]
        return {
            **base,
            "event": "tool.completed",
            "thread_id": thread_id,
            "turn_id": turn_id,
            "call_id": call_id,
            "event_key": f"tool:{item_id}" if item_id else f"tool:{turn_id}:{call_id}",
            "tool_name": str(item.get("toolName") or "")[:160],
            "at": item.get("completedAt") or item.get("createdAt"),
        }
    if method == "turn/completed":
        turn = params.get("turn") if isinstance(params.get("turn"), dict) else {}
        thread_id = str(params.get("threadId") or turn.get("threadId") or "").strip()
        turn_id = str(turn.get("id") or "").strip()
        if not thread_id or not turn_id:
            return None
        return {
            **base,
            "event": "turn.completed",
            "thread_id": thread_id,
            "turn_id": turn_id,
            "status": str(turn.get("status") or "completed")[:32],
            "usage": turn.get("usage") if isinstance(turn.get("usage"), dict) else {},
            "error_present": bool(str(turn.get("error") or "").strip()),
            "at": turn.get("completedAt"),
        }
    return None


async def _record_notification(peer: "DevicePeer", payload: dict[str, Any]) -> None:
    body = _notification_telemetry(peer, payload)
    if body is not None:
        await _telemetry_post("/telemetry/agent-event", body)


async def _command_loop(peer: "DevicePeer") -> None:
    if not TELEMETRY_SECRET:
        return
    try:
        while True:
            await asyncio.sleep(3.0)
            device_id = _device_id((peer.device or {}).get("id"))
            if not device_id:
                continue
            payload = await _telemetry_post("/telemetry/commands/poll", {"user_id": peer.user_id, "device_id": device_id})
            commands = payload.get("commands") if isinstance(payload.get("commands"), list) else []
            for command in commands:
                if not isinstance(command, dict):
                    continue
                command_id = int(command.get("id") or 0)
                kind = str(command.get("kind") or "")
                values = command.get("payload") if isinstance(command.get("payload"), dict) else {}
                if command_id <= 0:
                    continue
                if kind != "turn.interrupt":
                    await _telemetry_post("/telemetry/commands/complete", {"command_id": command_id, "ok": False, "error": "unsupported_command"})
                    continue
                thread_id = str(values.get("threadId") or "")
                turn_id = str(values.get("turnId") or "")
                if not thread_id or not turn_id:
                    await _telemetry_post("/telemetry/commands/complete", {"command_id": command_id, "ok": False, "error": "invalid_interrupt_target"})
                    continue
                await peer.send({
                    "type": "invoke",
                    "browserId": f"ops:{command_id}",
                    "id": command_id,
                    "operation": "call",
                    "args": ["turn/interrupt", {"threadId": thread_id, "turnId": turn_id}],
                })
    except asyncio.CancelledError:
        return


async def _refresh(refresh_token: str) -> tuple[int, dict[str, Any]]:
    return await _account_request("POST", "/auth/refresh", json_body={"refresh_token": refresh_token})


async def _authenticated_user(access_token: str) -> tuple[int, dict[str, Any] | None]:
    if not access_token:
        return 401, None
    status, payload = await _account_request("GET", "/auth/me", token=access_token)
    user = payload.get("user") if status == 200 else None
    return status, user if isinstance(user, dict) else None


# Short overlap window for concurrent requests carrying the same rotating cookie.
# Keys are hashes; credentials remain in bounded process memory only.
_refresh_tasks: dict[str, tuple[float, asyncio.Task]] = {}


async def _shared_refresh(token: str) -> tuple[int, dict[str, Any]]:
    now = time.monotonic()
    key = hashlib.sha256(token.encode()).hexdigest()
    for old_key, (expires, task) in list(_refresh_tasks.items()):
        if expires <= now and task.done():
            _refresh_tasks.pop(old_key, None)
    entry = _refresh_tasks.get(key)
    if entry is None:
        if len(_refresh_tasks) >= 256:
            return 503, {}
        task = asyncio.create_task(_refresh(token))
        _refresh_tasks[key] = (now + 30, task)
    else:
        task = entry[1]
    status, payload = await asyncio.shield(task)
    if status != 200:
        _refresh_tasks.pop(key, None)
    return status, payload


async def _browser_identity(request: Request) -> tuple[dict[str, Any] | None, dict[str, Any] | None, int]:
    access = request.cookies.get(ACCESS_COOKIE, "")
    status, user = await _authenticated_user(access)
    if user:
        return user, None, status
    # Outages and disabled accounts must not rotate otherwise valid cookies.
    if status != 401:
        return None, None, status
    refresh = request.cookies.get(REFRESH_COOKIE, "")
    if not refresh:
        return None, None, 401
    status, session = await _shared_refresh(refresh)
    if status != 200:
        return None, None, status
    user = session.get("user")
    if not isinstance(user, dict) or not session.get("access_token") or not session.get("refresh_token"):
        return None, None, 502
    # Refresh itself authenticates and returns the user. Avoid a second /me
    # request losing already-rotated credentials when the service goes offline.
    return user, session, 200


@app.get("/api/healthz")
async def healthz() -> dict[str, Any]:
    return {"ok": True, "buildSha": BUILD_SHA}


@app.get("/setup")
async def retired_cloud_setup() -> Response:
    return HTMLResponse(
        "<!doctype html><title>Loom Web</title><p>No cloud model setup is required. "
        "Loom Web uses the same local Loom Host, models, sessions and tools as Loom Desktop.</p>",
        status_code=410,
    )


@app.get("/api/auth/status")
async def auth_status(request: Request) -> Response:
    user, rotated, status = await _browser_identity(request)
    response = JSONResponse({"ok": True, "snapshot": _snapshot(user, reachable=status not in {0, 502, 503, 504} and status < 500)})
    if rotated:
        _set_session_cookies(response, rotated)
    return response


async def _auth_form(endpoint: str, request: Request) -> Response:
    try:
        body = await request.json()
    except (ValueError, UnicodeDecodeError):
        return JSONResponse({"ok": False, "error": _error("INVALID_JSON", "Request body must be a JSON object.", 400)}, status_code=400)
    if not isinstance(body, dict):
        return JSONResponse({"ok": False, "error": _error("INVALID_JSON", "Request body must be a JSON object.", 400)}, status_code=400)
    return await _auth_action(endpoint, request, body)


async def _auth_action(endpoint: str, request: Request, body: dict[str, Any] | None = None) -> Response:
    status, payload = await _account_request("POST", endpoint, json_body=body or {})
    if status != 200:
        error = payload.get("error") if isinstance(payload.get("error"), dict) else {}
        return JSONResponse({"ok": False, "error": _error(
            str(error.get("code") or "ACCOUNT_REQUEST_FAILED"),
            str(error.get("message") or "Account request failed."),
            status,
        )})
    user = payload.get("user") if isinstance(payload.get("user"), dict) else None
    if user and payload.get("access_token") and payload.get("refresh_token"):
        response = JSONResponse({"ok": True, "snapshot": _snapshot(user)})
        _set_session_cookies(response, payload)
        return response
    if endpoint in {"/auth/login", "/auth/oauth/exchange", "/auth/verify-email", "/auth/reset-password"}:
        return JSONResponse({"ok": False, "error": _error("ACCOUNT_RESPONSE_INVALID", "Account service returned an incomplete session.", 502)}, status_code=502)
    return JSONResponse({"ok": True, **payload})


@app.get("/api/auth/capabilities")
async def auth_capabilities() -> Response:
    status, payload = await _account_request("GET", "/auth/capabilities")
    if status != 200:
        error = payload.get("error") if isinstance(payload.get("error"), dict) else {}
        return JSONResponse({"ok": False, "error": _error(
            str(error.get("code") or "ACCOUNT_REQUEST_FAILED"),
            str(error.get("message") or "Could not load sign-in methods."),
            status,
        )})
    return JSONResponse({"ok": True, "capabilities": payload})


@app.post("/api/auth/login")
async def auth_login(request: Request) -> Response:
    return await _auth_form("/auth/login", request)


@app.post("/api/auth/register")
async def auth_register(request: Request) -> Response:
    return await _auth_form("/auth/register", request)


@app.post("/api/auth/register/start")
async def auth_register_start(request: Request) -> Response:
    try:
        body = await request.json()
    except Exception:
        body = {}
    return await _auth_action("/auth/register/start", request, body if isinstance(body, dict) else {})


@app.post("/api/auth/verify-email")
async def auth_verify_email(request: Request) -> Response:
    try: body = await request.json()
    except Exception: body = {}
    return await _auth_action("/auth/verify-email", request, body if isinstance(body, dict) else {})


@app.post("/api/auth/resend-email")
async def auth_resend_email(request: Request) -> Response:
    try: body = await request.json()
    except Exception: body = {}
    return await _auth_action("/auth/resend-email", request, body if isinstance(body, dict) else {})


@app.post("/api/auth/forgot-password")
async def auth_forgot_password(request: Request) -> Response:
    try: body = await request.json()
    except Exception: body = {}
    return await _auth_action("/auth/forgot-password", request, body if isinstance(body, dict) else {})


@app.post("/api/auth/reset-password")
async def auth_reset_password(request: Request) -> Response:
    try: body = await request.json()
    except Exception: body = {}
    return await _auth_action("/auth/reset-password", request, body if isinstance(body, dict) else {})


@app.get("/api/auth/oauth/start/{provider}")
async def auth_oauth_start(provider: str) -> Response:
    provider = str(provider).casefold()
    if provider not in {"google", "github"}:
        return JSONResponse({"ok": False, "error": _error("OAUTH_PROVIDER_INVALID", "Unsupported sign-in provider.", 400)}, status_code=400)
    return RedirectResponse(
        f"{ACCOUNT_BASE_URL}/auth/oauth/{provider}/start?{urlencode({'return_to': WEB_ORIGIN + '/'})}",
        status_code=302,
    )


@app.post("/api/auth/oauth/exchange")
async def auth_oauth_exchange(request: Request) -> Response:
    try: body = await request.json()
    except Exception: body = {}
    return await _auth_action("/auth/oauth/exchange", request, body if isinstance(body, dict) else {})


@app.post("/api/auth/profile")
async def auth_profile(request: Request) -> Response:
    user, rotated, identity_status = await _browser_identity(request)
    access = str(rotated.get("access_token") or "") if rotated else request.cookies.get(ACCESS_COOKIE, "")
    if not user or not access:
        status = identity_status or 502
        response = JSONResponse({"ok": False, "error": _error(
            "AUTH_REQUIRED" if status in {401, 403} else "ACCOUNT_SERVICE_UNREACHABLE",
            "Could not authenticate Loom Web.",
            status,
        )}, status_code=status)
        if rotated:
            _set_session_cookies(response, rotated)
        return response
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}
    status, payload = await _account_request("POST", "/auth/profile", token=access, json_body=body)
    updated_user = payload.get("user") if status == 200 and isinstance(payload.get("user"), dict) else None
    if not updated_user:
        error = payload.get("error") if isinstance(payload.get("error"), dict) else {}
        response = JSONResponse({"ok": False, "error": _error(
            str(error.get("code") or "PROFILE_UPDATE_FAILED"),
            str(error.get("message") or "Could not update your Loom profile."),
            status,
        )}, status_code=status or 502)
    else:
        response = JSONResponse({"ok": True, "snapshot": _snapshot(updated_user)})
    if rotated:
        _set_session_cookies(response, rotated)
    return response


@app.post("/api/auth/logout")
async def auth_logout(request: Request) -> Response:
    refresh = request.cookies.get(REFRESH_COOKIE, "")
    if refresh:
        key = hashlib.sha256(refresh.encode()).hexdigest()
        entry = _refresh_tasks.pop(key, None)
        if entry:
            status, rotated = await asyncio.shield(entry[1])
            if status == 200:
                refresh = str(rotated.get("refresh_token") or refresh)
        await _account_request("POST", "/auth/logout", json_body={"refresh_token": refresh})
        for key, (_, task) in list(_refresh_tasks.items()):
            if task.done() and not task.cancelled() and task.exception() is None:
                _, payload = task.result()
                if payload.get("refresh_token") == refresh:
                    _refresh_tasks.pop(key, None)
    response = JSONResponse({"ok": True, "snapshot": _snapshot(None)})
    _clear_session_cookies(response)
    return response


@app.post("/api/auth/device-pair")
async def auth_device_pair(request: Request) -> Response:
    """Mint a short-lived ticket for the Host on this browser's computer.

    The browser never receives its HttpOnly access/refresh credentials. The
    account service exchanges this one-time ticket into a completely independent
    Host session, so later token rotation cannot sign either side out.
    """
    user, rotated, identity_status = await _browser_identity(request)
    access = str(rotated.get("access_token") or "") if rotated else request.cookies.get(ACCESS_COOKIE, "")
    if not user or not access:
        status = identity_status or 502
        return JSONResponse({"ok": False, "error": _error("AUTH_REQUIRED" if status in {401, 403} else "ACCOUNT_SERVICE_UNREACHABLE", "Could not authenticate Loom Web.", status)}, status_code=status)

    status, payload = await _account_request(
        "POST",
        "/auth/device-pair/issue",
        token=access,
        json_body={},
    )
    if status != 200:
        error = payload.get("error") if isinstance(payload.get("error"), dict) else {}
        response = JSONResponse({"ok": False, "error": _error(
            str(error.get("code") or "PAIRING_FAILED"),
            str(error.get("message") or "Could not pair Loom Host."),
            status,
        )}, status_code=status or 502)
    else:
        response = JSONResponse({
            "ok": True,
            "pairing_ticket": str(payload.get("pairing_ticket") or ""),
            "expires_in": int(payload.get("expires_in") or 0),
        })
    if rotated:
        _set_session_cookies(response, rotated)
    return response


AUTH_RECHECK_SECONDS = 30.0
SEND_TIMEOUT_SECONDS = 10.0
MAX_INFLIGHT_INVOKES = 32


@dataclass
class RelayIdentity:
    token: str = field(repr=False)
    user_id: int
    valid_until: float
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def valid(self) -> bool:
        async with self.lock:
            if time.monotonic() < self.valid_until:
                return True
            status, payload = await _account_request("GET", "/auth/relay-me", token=self.token)
            user = payload.get("user")
            if status != 200 or not isinstance(user, dict) or user.get("id") != self.user_id:
                return False
            self.valid_until = time.monotonic() + AUTH_RECHECK_SECONDS
            return True


async def _relay_identity(access: str, user_id: int) -> RelayIdentity | None:
    status, payload = await _account_request("POST", "/auth/relay-credential", token=access, json_body={})
    token = str(payload.get("relay_token") or "")
    if status != 200 or not token:
        return None
    return RelayIdentity(token, user_id, time.monotonic() + AUTH_RECHECK_SECONDS)


async def _watch_identity(peer) -> None:
    while not peer.closed:
        await asyncio.sleep(AUTH_RECHECK_SECONDS)
        if not await peer.identity.valid():
            peer.closed = True
            await peer.websocket.close(code=4401, reason="session no longer authorized")
            return
        if isinstance(peer, BrowserPeer):
            for request_id, deadline in list(peer.deadlines.items()):
                if deadline <= time.monotonic():
                    peer.deadlines.pop(request_id, None)
                    peer.pending.pop(request_id, None)
                    peer.pending_invokes.pop(request_id, None)
                    await peer.send({"type": "invoke_result", "id": request_id,
                                     "error": {"code": "HOST_TIMEOUT", "message": "The selected Host did not respond."}})


async def _send(peer, payload: dict[str, Any]) -> bool:
    if peer.closed:
        return False
    if not await peer.identity.valid():
        peer.closed = True
        await peer.websocket.close(code=4401, reason="session no longer authorized")
        return False
    try:
        # Timeout includes waiting for the send lock, bounding slow clients.
        async with asyncio.timeout(SEND_TIMEOUT_SECONDS):
            async with peer.send_lock:
                if peer.closed:
                    return False
                await peer.websocket.send_json(payload)
        return True
    except (TimeoutError, OSError, RuntimeError, WebSocketDisconnect):
        peer.closed = True
        try:
            await asyncio.wait_for(peer.websocket.close(code=1013), timeout=1)
        except (TimeoutError, OSError, RuntimeError, WebSocketDisconnect):
            pass
        return False


@dataclass
class BrowserPeer:
    id: str
    user_id: int
    websocket: WebSocket
    device_id: str
    identity: RelayIdentity
    send_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    closed: bool = False
    pending: dict[int, Any] = field(default_factory=dict)
    deadlines: dict[int, float] = field(default_factory=dict)
    pending_invokes: dict[int, tuple[str, list[Any]]] = field(default_factory=dict)

    async def send(self, payload: dict[str, Any]) -> bool:
        return await _send(self, payload)


@dataclass
class DevicePeer:
    user_id: int
    websocket: WebSocket
    identity: RelayIdentity
    device_id: str = ""
    device: dict[str, Any] = field(default_factory=dict)
    send_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    closed: bool = False

    async def send(self, payload: dict[str, Any]) -> bool:
        return await _send(self, payload)


class RelayHub:
    def __init__(self) -> None:
        self.devices: dict[tuple[int, str], DevicePeer] = {}
        self.browsers: dict[str, BrowserPeer] = {}
        self.lock = asyncio.Lock()

    async def browser_status(self, browser: BrowserPeer) -> None:
        async with self.lock:
            device = self.devices.get((browser.user_id, browser.device_id))
            devices = [dict(peer.device) for (user_id, _), peer in self.devices.items()
                       if user_id == browser.user_id and not peer.closed]
        online = device is not None and not device.closed
        await browser.send({"type": "device_status", "online": online,
                            "selectedDeviceId": browser.device_id,
                            "devices": devices,
                            "device": dict(device.device) if online and device else None})

    async def broadcast_device_status(self, user_id: int) -> None:
        async with self.lock:
            peers = [peer for peer in self.browsers.values() if peer.user_id == user_id]
        await asyncio.gather(*(self.browser_status(peer) for peer in peers), return_exceptions=True)

    async def broadcast_notification(self, device: DevicePeer, payload: dict[str, Any]) -> None:
        async with self.lock:
            peers = [peer for peer in self.browsers.values()
                     if peer.user_id == device.user_id and peer.device_id == device.device_id]
        await asyncio.gather(*(peer.send({"type": "notification", "payload": payload}) for peer in peers), return_exceptions=True)


hub = RelayHub()


def _window_legacy_thread_read_result(
    result: Any,
    operation: str,
    args: list[Any],
) -> Any:
    """Bound old-Host thread/read payloads before they reach the browser.

    Loom Host 0.1.8 predates turn-window support and ignores ``turnLimit``. The
    gateway already has to decode its response, so slicing here prevents the
    browser from receiving/parsing/rendering a giant transcript while remaining
    fully compatible with newer Hosts that return ``hasMoreTurns`` themselves.
    """
    if operation != "call" or len(args) < 2 or args[0] != "thread/read":
        return result
    params = args[1] if isinstance(args[1], dict) else {}
    limit_raw = params.get("turnLimit")
    if limit_raw is None or limit_raw == "" or not isinstance(result, dict):
        return result

    try:
        limit = int(limit_raw)
    except (TypeError, ValueError):
        return result
    if not 1 <= limit <= 100:
        return result

    # Newer Hosts have already done the expensive durable-state windowing.
    # Still strip legacy diagnostic duplicates when the browser explicitly asked
    # for the presentation shape.
    bounded = dict(result)
    if bool(params.get("presentationOnly", False)):
        bounded.pop("messages", None)
        bounded.pop("events", None)
    if isinstance(result.get("hasMoreTurns"), bool):
        return bounded

    turns = result.get("turns")
    if not isinstance(turns, list):
        return bounded

    before_turn_id = str(params.get("beforeTurnId") or "").strip()
    end = len(turns)
    if before_turn_id:
        cursor = next(
            (index for index, turn in enumerate(turns) if isinstance(turn, dict) and str(turn.get("id") or "") == before_turn_id),
            -1,
        )
        if cursor < 0:
            bounded["turns"] = []
            bounded["hasMoreTurns"] = False
            bounded["oldestTurnId"] = None
            return bounded
        end = cursor

    start = max(0, end - limit)
    window_turns = turns[start:end]
    bounded["turns"] = window_turns
    bounded["hasMoreTurns"] = start > 0
    logger.info(
        "relay legacy thread/read window total_turns=%s sent_turns=%s before=%s",
        len(turns),
        len(window_turns),
        bool(before_turn_id),
    )
    bounded["oldestTurnId"] = (
        str(window_turns[0].get("id") or "")
        if window_turns and isinstance(window_turns[0], dict)
        else None
    )
    return bounded


async def _run_device_invoke(peer: BrowserPeer, request_id: int, operation: str, args: list[Any]) -> None:
    async with hub.lock:
        device = hub.devices.get((peer.user_id, peer.device_id))
    if peer.closed or not await peer.identity.valid():
        await peer.websocket.close(code=4401, reason="session no longer authorized")
        return
    if device is None or device.closed or not await device.identity.valid():
        peer.pending.pop(request_id, None)
        peer.deadlines.pop(request_id, None)
        await peer.send({"type": "invoke_result", "id": request_id,
                         "error": {"code": "HOST_OFFLINE", "message": "The selected Loom Host is offline. Start Loom on that computer; another Host will not be used."}})
        return
    if operation == "call" and len(args) >= 2 and args[0] == "thread/read":
        peer.pending_invokes[request_id] = (operation, args)
    peer.pending[request_id] = device
    sent = await device.send({"type": "invoke", "browserId": peer.id, "id": request_id,
                              "operation": operation, "args": args})
    if not sent:
        peer.pending.pop(request_id, None)
        peer.deadlines.pop(request_id, None)
        peer.pending_invokes.pop(request_id, None)
        await peer.send({"type": "invoke_result", "id": request_id,
                         "error": {"code": "HOST_OFFLINE", "message": "The selected Loom Host disconnected."}})


async def _cancel_tasks(tasks) -> None:
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


@app.websocket("/api/ws/browser")
async def browser_socket(websocket: WebSocket) -> None:
    expected_origin = os.environ.get("LOOM_WEB_ORIGIN", "https://loom.smirel.com").rstrip("/")
    origin = str(websocket.headers.get("origin") or "").rstrip("/")
    if origin != expected_origin:
        await websocket.close(code=4403, reason="origin not allowed")
        return
    access = websocket.cookies.get(ACCESS_COOKIE, "")
    _, user = await _authenticated_user(access)
    user_id = int(user.get("id") or 0) if user else 0
    identity = await _relay_identity(access, user_id) if user_id > 0 else None
    if identity is None:
        await websocket.close(code=4401, reason="authentication required")
        return
    selected = _device_id(websocket.query_params.get("device"))
    await websocket.accept()
    peer = BrowserPeer(uuid.uuid4().hex, user_id, websocket, selected, identity)
    async with hub.lock:
        hub.browsers[peer.id] = peer
    tasks: set[asyncio.Task] = set()
    watcher = asyncio.create_task(_watch_identity(peer))
    try:
        await hub.browser_status(peer)
        while not peer.closed:
            frame = await websocket.receive_json()
            if not isinstance(frame, dict):
                continue
            if frame.get("type") == "ping":
                await peer.send({"type": "pong"})
                continue
            if frame.get("type") == "get_status":
                await hub.browser_status(peer)
                continue
            if frame.get("type") != "invoke":
                continue
            request_id = frame.get("id")
            if type(request_id) is not int:
                continue
            if request_id in peer.pending or len(peer.pending) >= MAX_INFLIGHT_INVOKES:
                await peer.send({"type": "invoke_result", "id": request_id,
                                 "error": {"code": "HOST_BUSY", "message": "Too many pending Host requests."}})
                continue
            operation = str(frame.get("operation") or "")
            args = frame.get("args") if isinstance(frame.get("args"), list) else []
            peer.pending[request_id] = None
            peer.deadlines[request_id] = time.monotonic() + 120
            task = asyncio.create_task(_run_device_invoke(peer, request_id, operation, args))
            tasks.add(task)
            task.add_done_callback(tasks.discard)
    except (WebSocketDisconnect, OSError, RuntimeError, ValueError):
        pass
    finally:
        peer.closed = True
        await _cancel_tasks([watcher, *tasks])
        peer.pending.clear()
        peer.deadlines.clear()
        async with hub.lock:
            hub.browsers.pop(peer.id, None)


@app.websocket("/api/ws/device")
async def device_socket(websocket: WebSocket) -> None:
    authorization = str(websocket.headers.get("authorization") or "")
    access = authorization[7:].strip() if authorization.lower().startswith("bearer ") else ""
    _, user = await _authenticated_user(access)
    user_id = int(user.get("id") or 0) if user else 0
    identity = await _relay_identity(access, user_id) if user_id > 0 else None
    if identity is None:
        await websocket.close(code=4401, reason="authentication required")
        return
    await websocket.accept()
    peer = DevicePeer(user_id, websocket, identity)
    watcher = asyncio.create_task(_watch_identity(peer))
    command_task: asyncio.Task | None = None
    try:
        while not peer.closed:
            frame = await websocket.receive_json()
            if not isinstance(frame, dict):
                continue
            if not await peer.identity.valid():
                await websocket.close(code=4401, reason="session no longer authorized")
                return
            kind = frame.get("type")
            if kind == "ping":
                await peer.send({"type": "pong"})
                if peer.device_id:
                    asyncio.create_task(_record_device(peer, "heartbeat", frame.get("health")))
            elif kind == "device_hello":
                payload = frame.get("device")
                payload = dict(payload) if isinstance(payload, dict) else {}
                device_id = _device_id(payload.get("id"))
                if not device_id or (peer.device_id and peer.device_id != device_id):
                    await websocket.close(code=4400, reason="valid immutable device id required")
                    return
                payload["id"] = device_id
                peer.device_id, peer.device = device_id, payload
                asyncio.create_task(_record_device(peer, "connected", frame.get("health")))
                if command_task is None:
                    command_task = asyncio.create_task(_command_loop(peer))
                async with hub.lock:
                    old = hub.devices.get((user_id, device_id))
                    hub.devices[(user_id, device_id)] = peer
                if old is not None and old is not peer:
                    old.closed = True
                    await old.websocket.close(code=4001, reason="newer connection for this device")
                await hub.broadcast_device_status(user_id)
            elif kind == "invoke_result":
                browser_id = str(frame.get("browserId") or "")
                request_id = frame.get("id")
                if type(request_id) is not int:
                    continue
                async with hub.lock:
                    current = hub.devices.get((user_id, peer.device_id))
                    browser = hub.browsers.get(browser_id)
                if current is peer and browser_id.startswith("ops:"):
                    try:
                        command_id = int(browser_id.split(":", 1)[1])
                    except (TypeError, ValueError):
                        command_id = 0
                    if command_id > 0:
                        error = frame.get("error") if isinstance(frame.get("error"), dict) else None
                        await _telemetry_post("/telemetry/commands/complete", {
                            "command_id": command_id,
                            "ok": error is None,
                            "error": str((error or {}).get("message") or "")[:240],
                        })
                    continue
                if (current is peer and browser and browser.user_id == user_id
                        and browser.device_id == peer.device_id and browser.pending.get(request_id) is peer):
                    browser.pending.pop(request_id, None)
                    browser.deadlines.pop(request_id, None)
                    forwarded = {"type": "invoke_result", "id": request_id}
                    invoke_meta = browser.pending_invokes.pop(request_id, None)
                    if "error" in frame:
                        forwarded["error"] = frame.get("error")
                    else:
                        result = frame.get("result")
                        if invoke_meta is not None:
                            result = _window_legacy_thread_read_result(result, invoke_meta[0], invoke_meta[1])
                        forwarded["result"] = result
                    await browser.send(forwarded)
            elif kind == "notification":
                payload = frame.get("payload")
                async with hub.lock:
                    current = hub.devices.get((user_id, peer.device_id))
                if isinstance(payload, dict) and current is peer:
                    asyncio.create_task(_record_notification(peer, payload))
                    await hub.broadcast_notification(peer, payload)
    except (WebSocketDisconnect, OSError, RuntimeError, ValueError):
        pass
    finally:
        if peer.device_id:
            await _record_device(peer, "disconnected")
        peer.closed = True
        tasks_to_cancel = [watcher]
        if command_task is not None:
            tasks_to_cancel.append(command_task)
        await _cancel_tasks(tasks_to_cancel)
        async with hub.lock:
            if hub.devices.get((user_id, peer.device_id)) is peer:
                hub.devices.pop((user_id, peer.device_id), None)
            browsers = [browser for browser in hub.browsers.values() if browser.user_id == user_id]
        for browser in browsers:
            for request_id, device in list(browser.pending.items()):
                if device is peer:
                    browser.pending.pop(request_id, None)
                    browser.deadlines.pop(request_id, None)
                    browser.pending_invokes.pop(request_id, None)
                    await browser.send({"type": "invoke_result", "id": request_id,
                                        "error": {"code": "HOST_OFFLINE", "message": "The selected Loom Host disconnected."}})
        await hub.broadcast_device_status(user_id)

def _static_headers(path: Path) -> dict[str, str]:
    headers = {"X-Loom-Build": BUILD_SHA}
    if path.name == "index.html":
        headers["Cache-Control"] = "no-store, max-age=0"
        return headers
    try:
        relative = path.relative_to(STATIC_DIR)
    except ValueError:
        relative = path
    if relative.parts and relative.parts[0] == "assets":
        headers["Cache-Control"] = "public, max-age=31536000, immutable"
    else:
        headers["Cache-Control"] = "no-cache, must-revalidate"
    return headers


@app.get("/{full_path:path}")
async def spa(full_path: str) -> Response:
    relative = full_path.strip("/")
    candidate = (STATIC_DIR / relative).resolve() if relative else STATIC_DIR / "index.html"
    if STATIC_DIR in candidate.parents and candidate.is_file():
        return FileResponse(candidate, headers=_static_headers(candidate))
    index = STATIC_DIR / "index.html"
    if index.is_file():
        return FileResponse(index, headers=_static_headers(index))
    return JSONResponse({"error": "Loom Web frontend is not built."}, status_code=503)
