from __future__ import annotations

import asyncio
import logging
import os
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

ACCOUNT_BASE_URL = os.environ.get("LOOM_ACCOUNT_API_BASE_URL", "https://account.smirel.com/v1").rstrip("/")
STATIC_DIR = Path(os.environ.get("LOOM_WEB_STATIC_DIR", "/app/static")).resolve()
ACCESS_COOKIE = "loom_web_access"
REFRESH_COOKIE = "loom_web_refresh"
ACCESS_MAX_AGE = 15 * 60
REFRESH_MAX_AGE = 30 * 24 * 60 * 60

# Loom Web is a thin browser client for the same local Loom Host used by Desktop.
# The gateway authenticates the account and relays WebSocket frames only; it never
# starts a second Agent Runtime on the server.

app = FastAPI(title="Loom Web", docs_url=None, redoc_url=None, openapi_url=None)
logger = logging.getLogger("loom.web.relay")


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
async def healthz() -> dict[str, bool]:
    return {"ok": True}


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
    status, payload = await _account_request("POST", endpoint, json_body={
        "email": str(body.get("email") or ""),
        "password": str(body.get("password") or ""),
    })
    if status != 200:
        error = payload.get("error") if isinstance(payload.get("error"), dict) else {}
        return JSONResponse({"ok": False, "error": _error(
            str(error.get("code") or "ACCOUNT_REQUEST_FAILED"),
            str(error.get("message") or "Account request failed."),
            status,
        )})
    user = payload.get("user") if isinstance(payload.get("user"), dict) else None
    response = JSONResponse({"ok": True, "snapshot": _snapshot(user)})
    _set_session_cookies(response, payload)
    return response


@app.post("/api/auth/login")
async def auth_login(request: Request) -> Response:
    return await _auth_form("/auth/login", request)


@app.post("/api/auth/register")
async def auth_register(request: Request) -> Response:
    return await _auth_form("/auth/register", request)


@app.post("/api/auth/logout")
async def auth_logout(request: Request) -> Response:
    refresh = request.cookies.get(REFRESH_COOKIE, "")
    if refresh:
        await _account_request("POST", "/auth/logout", json_body={"refresh_token": refresh})
    response = JSONResponse({"ok": True, "snapshot": _snapshot(None)})
    _clear_session_cookies(response)
    return response


@dataclass
class BrowserPeer:
    id: str
    user_id: int
    websocket: WebSocket
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
        self.devices: dict[int, DevicePeer] = {}
        self.browsers: dict[str, BrowserPeer] = {}
        self.lock = asyncio.Lock()

    async def browser_status(self, browser: BrowserPeer) -> None:
        async with self.lock:
            device = self.devices.get(browser.user_id)
        await browser.send({"type": "device_status", "online": device is not None, "device": device.device if device else None})

    async def broadcast_device_status(self, user_id: int) -> None:
        async with self.lock:
            device = self.devices.get(user_id)
            peers = [peer for peer in self.browsers.values() if peer.user_id == user_id]
        payload = {"type": "device_status", "online": device is not None, "device": device.device if device else None}
        await asyncio.gather(*(peer.send(payload) for peer in peers), return_exceptions=True)

    async def broadcast_notification(self, user_id: int, payload: dict[str, Any]) -> None:
        async with self.lock:
            peers = [peer for peer in self.browsers.values() if peer.user_id == user_id]
        await asyncio.gather(*(peer.send({"type": "notification", "payload": payload}) for peer in peers), return_exceptions=True)


hub = RelayHub()


async def _ws_user_from_access(access: str) -> dict[str, Any] | None:
    _, user = await _authenticated_user(access)
    return user


async def _run_device_invoke(peer: BrowserPeer, request_id: Any, operation: str, args: list[Any]) -> None:
    logger.info("relay invoke operation=%s", operation)
    async with hub.lock:
        device = hub.devices.get(peer.user_id)
    if device is None:
        await peer.send({
            "type": "invoke_result",
            "id": request_id,
            "error": {"code": "HOST_OFFLINE", "message": "Your Loom Host is offline. Start Loom on your computer; the Host can remain running in the background."},
        })
        return
    sent = await device.send({
        "type": "invoke",
        "browserId": peer.id,
        "id": request_id,
        "operation": operation,
        "args": args,
    })
    if not sent:
        await peer.send({
            "type": "invoke_result",
            "id": request_id,
            "error": {"code": "HOST_OFFLINE", "message": "Your Loom Host disconnected before the request was sent."},
        })


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
    await websocket.accept()
    peer = BrowserPeer(id=uuid.uuid4().hex, user_id=user_id, websocket=websocket)
    async with hub.lock:
        hub.browsers[peer.id] = peer
    await hub.browser_status(peer)
    try:
        while True:
            frame = await websocket.receive_json()
            if not isinstance(frame, dict):
                continue
            if frame.get("type") == "ping":
                await peer.send({"type": "pong"})
                continue
            if frame.get("type") != "invoke":
                continue
            request_id = frame.get("id")
            operation = str(frame.get("operation") or "")
            args = frame.get("args") if isinstance(frame.get("args"), list) else []
            # Every operation goes to the user's local Loom Host. Desktop and Web
            # therefore share one App Server, Agent Runtime, model registry,
            # conversation store, approval chain and tool execution environment.
            asyncio.create_task(_run_device_invoke(peer, request_id, operation, args))
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
    async with hub.lock:
        old = hub.devices.get(user_id)
        hub.devices[user_id] = peer
    if old is not None and old.websocket is not websocket:
        try:
            await old.websocket.close(code=4001, reason="newer Loom Desktop connected")
        except RuntimeError:
            pass
    await hub.broadcast_device_status(user_id)
    try:
        while True:
            frame = await websocket.receive_json()
            if not isinstance(frame, dict):
                continue
            kind = frame.get("type")
            if kind == "ping":
                await peer.send({"type": "pong"})
            elif kind == "device_hello":
                device = frame.get("device")
                peer.device = device if isinstance(device, dict) else {}
                logger.info(
                    "relay device hello platform=%s version=%s",
                    str(peer.device.get("platform") or "unknown"),
                    str(peer.device.get("version") or "unknown"),
                )
                await hub.broadcast_device_status(user_id)
            elif kind == "invoke_result":
                browser_id = str(frame.get("browserId") or "")
                async with hub.lock:
                    browser = hub.browsers.get(browser_id)
                if browser and browser.user_id == user_id:
                    forwarded = {"type": "invoke_result", "id": frame.get("id")}
                    if "error" in frame:
                        forwarded["error"] = frame.get("error")
                    else:
                        forwarded["result"] = frame.get("result")
                    await browser.send(forwarded)
            elif kind == "notification":
                payload = frame.get("payload")
                if isinstance(payload, dict):
                    notification_type = str(payload.get("method") or payload.get("type") or "unknown")
                    logger.info("relay notification type=%s", notification_type)
                    # Streaming deltas, turn completion, approvals and every other
                    # App Server notification are the same stream Desktop receives.
                    await hub.broadcast_notification(user_id, payload)
    except (WebSocketDisconnect, RuntimeError, ValueError):
        pass
    finally:
        peer.closed = True
        async with hub.lock:
            if hub.devices.get(user_id) is peer:
                hub.devices.pop(user_id, None)
        logger.info("relay device disconnected")
        await hub.broadcast_device_status(user_id)


@app.get("/{full_path:path}")
async def spa(full_path: str) -> Response:
    relative = full_path.strip("/")
    candidate = (STATIC_DIR / relative).resolve() if relative else STATIC_DIR / "index.html"
    if STATIC_DIR in candidate.parents and candidate.is_file():
        headers = {"Cache-Control": "no-cache, must-revalidate"} if candidate.name == "index.html" else None
        return FileResponse(candidate, headers=headers)
    index = STATIC_DIR / "index.html"
    if index.is_file():
        return FileResponse(index, headers={"Cache-Control": "no-cache, must-revalidate"})
    return JSONResponse({"error": "Loom Web frontend is not built."}, status_code=503)