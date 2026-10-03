from __future__ import annotations

import asyncio
import logging
import os
import uuid
from urllib.parse import urlencode
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse

ACCOUNT_BASE_URL = os.environ.get("LOOM_ACCOUNT_API_BASE_URL", "https://account.smirel.com/v1").rstrip("/")
WEB_ORIGIN = os.environ.get("LOOM_WEB_ORIGIN", "https://loom.smirel.com").rstrip("/")
STATIC_DIR = Path(os.environ.get("LOOM_WEB_STATIC_DIR", "/app/static")).resolve()
BUILD_SHA = str(os.environ.get("LOOM_BUILD_SHA", "unknown") or "unknown").strip()
TELEMETRY_SECRET = str(os.environ.get("LOOM_TELEMETRY_SECRET", "") or "").strip()
ACCESS_COOKIE = "loom_web_access"
REFRESH_COOKIE = "loom_web_refresh"
ACCESS_MAX_AGE = 15 * 60
REFRESH_MAX_AGE = 30 * 24 * 60 * 60

# Loom Web is a thin browser client for the same local Loom Host used by Desktop.
# The gateway authenticates the account and relays WebSocket frames only; it never
# starts a second Agent Runtime on the server.

app = FastAPI(title="Loom Web", docs_url=None, redoc_url=None, openapi_url=None)
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


def _device_payload(peer: "DevicePeer", event: str) -> dict[str, Any]:
    device = peer.device if isinstance(peer.device, dict) else {}
    return {
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


async def _record_device(peer: "DevicePeer", event: str) -> None:
    body = _device_payload(peer, event)
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


async def _browser_identity(request: Request) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    access = request.cookies.get(ACCESS_COOKIE, "")
    _, user = await _authenticated_user(access)
    if user:
        return user, None
    refresh = request.cookies.get(REFRESH_COOKIE, "")
    if not refresh:
        return None, None
    refresh_status, session = await _refresh(refresh)
    if refresh_status != 200:
        return None, None
    _, user = await _authenticated_user(str(session.get("access_token") or ""))
    return user, session if user else None


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
    user, rotated = await _browser_identity(request)
    response = JSONResponse({"ok": True, "snapshot": _snapshot(user)})
    if rotated:
        _set_session_cookies(response, rotated)
    return response


async def _auth_form(endpoint: str, request: Request) -> Response:
    try:
        body = await request.json()
    except Exception:
        body = {}
    return await _auth_action(endpoint, request, body if isinstance(body, dict) else {})


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


@app.post("/api/auth/logout")
async def auth_logout(request: Request) -> Response:
    refresh = request.cookies.get(REFRESH_COOKIE, "")
    if refresh:
        await _account_request("POST", "/auth/logout", json_body={"refresh_token": refresh})
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
    access = request.cookies.get(ACCESS_COOKIE, "")
    _, user = await _authenticated_user(access)
    rotated: dict[str, Any] | None = None
    if not user:
        refresh = request.cookies.get(REFRESH_COOKIE, "")
        if refresh:
            refresh_status, candidate = await _refresh(refresh)
            if refresh_status == 200:
                candidate_access = str(candidate.get("access_token") or "")
                _, user = await _authenticated_user(candidate_access)
                if user:
                    access = candidate_access
                    rotated = candidate
    if not user or not access:
        return JSONResponse({"ok": False, "error": _error("AUTH_REQUIRED", "Sign in to Loom Web first.", 401)}, status_code=401)

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


@dataclass
class BrowserPeer:
    id: str
    user_id: int
    websocket: WebSocket
    selected_device_id: str = ""
    pending_invokes: dict[str, tuple[str, list[Any]]] = field(default_factory=dict)
    send_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    closed: bool = False

    async def send(self, payload: dict[str, Any]) -> bool:
        if self.closed:
            return False
        async with self.send_lock:
            if self.closed:
                return False
            try:
                await self.websocket.send_json(payload)
                return True
            except (RuntimeError, WebSocketDisconnect):
                self.closed = True
                return False


@dataclass
class DevicePeer:
    user_id: int
    websocket: WebSocket
    device_id: str = ""
    device: dict[str, Any] = field(default_factory=dict)
    send_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    closed: bool = False

    async def send(self, payload: dict[str, Any]) -> bool:
        if self.closed:
            return False
        async with self.send_lock:
            if self.closed:
                return False
            try:
                await self.websocket.send_json(payload)
                return True
            except (RuntimeError, WebSocketDisconnect):
                self.closed = True
                return False


class RelayHub:
    def __init__(self) -> None:
        self.devices: dict[int, dict[str, DevicePeer]] = {}
        self.browsers: dict[str, BrowserPeer] = {}
        self.lock = asyncio.Lock()

    def _device_for_browser_locked(self, browser: BrowserPeer) -> DevicePeer | None:
        devices = self.devices.get(browser.user_id, {})
        if browser.selected_device_id:
            selected = devices.get(browser.selected_device_id)
            return selected if selected is not None and not selected.closed else None
        for device in reversed(tuple(devices.values())):
            if not device.closed:
                return device
        return None

    async def device_for_browser(self, browser: BrowserPeer) -> DevicePeer | None:
        async with self.lock:
            return self._device_for_browser_locked(browser)

    async def browser_status(self, browser: BrowserPeer) -> None:
        async with self.lock:
            devices = [device for device in self.devices.get(browser.user_id, {}).values() if not device.closed]
            selected = self._device_for_browser_locked(browser)
        await browser.send({
            "type": "device_status",
            "online": selected is not None,
            "device": dict(selected.device) if selected else None,
            "selectedDeviceId": browser.selected_device_id or (selected.device_id if selected else None),
            "devices": [dict(device.device) for device in devices],
        })

    async def broadcast_device_status(self, user_id: int) -> None:
        async with self.lock:
            peers = [peer for peer in self.browsers.values() if peer.user_id == user_id]
        await asyncio.gather(*(self.browser_status(peer) for peer in peers), return_exceptions=True)

    async def broadcast_notification(self, user_id: int, device_id: str, payload: dict[str, Any]) -> None:
        async with self.lock:
            peers = [
                peer for peer in self.browsers.values()
                if peer.user_id == user_id
                and (selected := self._device_for_browser_locked(peer)) is not None
                and selected.device_id == device_id
            ]
        await asyncio.gather(*(peer.send({"type": "notification", "payload": payload}) for peer in peers), return_exceptions=True)


hub = RelayHub()


async def _ws_user_from_access(access: str) -> dict[str, Any] | None:
    _, user = await _authenticated_user(access)
    return user


def _window_legacy_thread_read_result(result: Any, operation: str, args: list[Any]) -> Any:
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
        cursor = next((index for index, turn in enumerate(turns) if isinstance(turn, dict) and str(turn.get("id") or "") == before_turn_id), -1)
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
    bounded["oldestTurnId"] = str(window_turns[0].get("id") or "") if window_turns and isinstance(window_turns[0], dict) else None
    return bounded


async def _run_device_invoke(peer: BrowserPeer, request_id: Any, operation: str, args: list[Any]) -> None:
    logger.info("relay invoke operation=%s", operation)
    device = await hub.device_for_browser(peer)
    if device is None or device.closed:
        await peer.send({"type":"invoke_result","id":request_id,"error":{"code":"HOST_OFFLINE","message":"No Loom Host is online for this account. Start Loom on the computer you want to control; its Host can remain running in the background."}})
        return
    request_key = str(request_id)
    track_result_window = bool(operation == "call" and len(args) >= 2 and args[0] == "thread/read")
    if track_result_window:
        peer.pending_invokes[request_key] = (operation, args)
        while len(peer.pending_invokes) > 256:
            peer.pending_invokes.pop(next(iter(peer.pending_invokes)), None)
    sent = await device.send({"type":"invoke","browserId":peer.id,"id":request_id,"operation":operation,"args":args})
    if not sent:
        peer.pending_invokes.pop(request_key, None)
        await peer.send({"type":"invoke_result","id":request_id,"error":{"code":"HOST_OFFLINE","message":"Loom Host disconnected before the request was sent."}})


@app.websocket("/api/ws/browser")
async def browser_socket(websocket: WebSocket) -> None:
    expected_origin = os.environ.get("LOOM_WEB_ORIGIN", "https://loom.smirel.com").rstrip("/")
    origin = str(websocket.headers.get("origin") or "").rstrip("/")
    if origin != expected_origin:
        await websocket.close(code=4403, reason="origin not allowed")
        return
    access = websocket.cookies.get(ACCESS_COOKIE, "")
    user = await _ws_user_from_access(access)
    if not user:
        await websocket.close(code=4401, reason="authentication required")
        return
    user_id = int(user.get("id") or 0)
    if user_id <= 0:
        await websocket.close(code=4401, reason="invalid account")
        return
    requested_device = str(websocket.query_params.get("device") or "").strip()
    selected_device_id = _device_id(requested_device)
    if requested_device and not selected_device_id:
        await websocket.close(code=4400, reason="invalid device id required")
        return
    await websocket.accept()
    peer = BrowserPeer(id=uuid.uuid4().hex,user_id=user_id,websocket=websocket,selected_device_id=selected_device_id)
    async with hub.lock:
        hub.browsers[peer.id] = peer
    await hub.browser_status(peer)
    try:
        while True:
            frame = await websocket.receive_json()
            if not isinstance(frame, dict):
                continue
            kind = frame.get("type")
            if kind == "ping":
                await peer.send({"type":"pong"})
                continue
            if kind != "invoke":
                continue
            request_id = frame.get("id")
            operation = str(frame.get("operation") or "")
            args = frame.get("args") if isinstance(frame.get("args"), list) else []
            asyncio.create_task(_run_device_invoke(peer,request_id,operation,args))
    except (WebSocketDisconnect, RuntimeError, ValueError):
        pass
    finally:
        peer.closed = True
        async with hub.lock:
            hub.browsers.pop(peer.id, None)


@app.websocket("/api/ws/device")
async def device_socket(websocket: WebSocket) -> None:
    authorization = str(websocket.headers.get("authorization") or "")
    access = authorization[7:].strip() if authorization.lower().startswith("bearer ") else ""
    user = await _ws_user_from_access(access)
    if not user:
        await websocket.close(code=4401, reason="authentication required")
        return
    user_id = int(user.get("id") or 0)
    if user_id <= 0:
        await websocket.close(code=4401, reason="invalid account")
        return
    await websocket.accept()
    logger.info("relay device connected")
    peer = DevicePeer(user_id=user_id, websocket=websocket)
    command_task: asyncio.Task[None] | None = None
    try:
        while True:
            frame = await websocket.receive_json()
            if not isinstance(frame, dict):
                continue
            kind = frame.get("type")
            if kind == "ping":
                await peer.send({"type":"pong"})
                if peer.device_id:
                    asyncio.create_task(_record_device(peer,"heartbeat"))
            elif kind == "device_hello":
                device = frame.get("device")
                payload = dict(device) if isinstance(device, dict) else {}
                device_id = _device_id(payload.get("id"))
                if not device_id:
                    await websocket.close(code=4400, reason="valid device id required")
                    return
                payload["id"] = device_id
                peer.device_id = device_id
                peer.device = payload
                asyncio.create_task(_record_device(peer,"connected"))
                if command_task is None:
                    command_task = asyncio.create_task(_command_loop(peer))
                async with hub.lock:
                    devices = hub.devices.setdefault(user_id,{})
                    old = devices.get(device_id)
                    devices.pop(device_id,None)
                    devices[device_id] = peer
                if old is not None and old is not peer and old.websocket is not websocket:
                    try:
                        await old.websocket.close(code=4001, reason="newer Loom Host instance connected for this device")
                    except RuntimeError:
                        pass
                await hub.broadcast_device_status(user_id)
            elif kind == "invoke_result":
                if not peer.device_id:
                    continue
                browser_id = str(frame.get("browserId") or "")
                async with hub.lock:
                    current = hub.devices.get(user_id,{}).get(peer.device_id)
                    browser = hub.browsers.get(browser_id)
                if current is peer and browser_id.startswith("ops:"):
                    try:
                        command_id = int(browser_id.split(":",1)[1])
                    except (TypeError, ValueError):
                        command_id = 0
                    if command_id > 0:
                        error = frame.get("error") if isinstance(frame.get("error"),dict) else None
                        await _telemetry_post("/telemetry/commands/complete", {"command_id":command_id,"ok":error is None,"error":str((error or {}).get("message") or "")[:240]})
                    continue
                if current is peer and browser and browser.user_id == user_id:
                    request_id = frame.get("id")
                    invoke_meta = browser.pending_invokes.pop(str(request_id),None)
                    forwarded = {"type":"invoke_result","id":request_id}
                    if "error" in frame:
                        forwarded["error"] = frame.get("error")
                    else:
                        result = frame.get("result")
                        if invoke_meta is not None:
                            result = _window_legacy_thread_read_result(result,invoke_meta[0],invoke_meta[1])
                        forwarded["result"] = result
                    await browser.send(forwarded)
            elif kind == "notification":
                payload = frame.get("payload")
                async with hub.lock:
                    current = hub.devices.get(user_id,{}).get(peer.device_id)
                if isinstance(payload,dict) and current is peer:
                    asyncio.create_task(_record_notification(peer,payload))
                    await hub.broadcast_notification(user_id,peer.device_id,payload)
    except (WebSocketDisconnect, RuntimeError, ValueError):
        pass
    finally:
        if command_task is not None:
            command_task.cancel()
        if peer.device_id:
            await _record_device(peer,"disconnected")
        peer.closed = True
        async with hub.lock:
            devices = hub.devices.get(user_id)
            if devices is not None and peer.device_id and devices.get(peer.device_id) is peer:
                devices.pop(peer.device_id,None)
                if not devices:
                    hub.devices.pop(user_id,None)
        await hub.broadcast_device_status(user_id)


def _static_headers(path: Path) -> dict[str, str]:
    headers={"X-Loom-Build":BUILD_SHA}
    if path.name == "index.html":
        headers["Cache-Control"]="no-store, max-age=0"
        return headers
    try:
        relative=path.relative_to(STATIC_DIR)
    except ValueError:
        relative=path
    if relative.parts and relative.parts[0] == "assets":
        headers["Cache-Control"]="public, max-age=31536000, immutable"
    else:
        headers["Cache-Control"]="no-cache, must-revalidate"
    return headers


@app.get("/{full_path:path}")
async def spa(full_path: str) -> Response:
    relative=full_path.strip("/")
    candidate=(STATIC_DIR/relative).resolve() if relative else STATIC_DIR/"index.html"
    if STATIC_DIR in candidate.parents and candidate.is_file():
        return FileResponse(candidate,headers=_static_headers(candidate))
    index=STATIC_DIR/"index.html"
    if index.is_file():
        return FileResponse(index,headers=_static_headers(index))
    return JSONResponse({"error":"Loom Web frontend is not built."},status_code=503)
