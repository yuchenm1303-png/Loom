from __future__ import annotations

import base64
import json
import os
import threading
import time
import uuid
from dataclasses import dataclass, field
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlsplit

from .browser_session import BrowserError, BrowserLaunchOptions, BrowserPageState


DEFAULT_EXTENSION_HOST = "127.0.0.1"
DEFAULT_EXTENSION_PORT = 39222
DEFAULT_EXTENSION_TOKEN = "loom-dev-browser-extension"


@dataclass(slots=True)
class _BridgeCommand:
    command_id: str
    action: str
    args: dict[str, Any]
    created_at: float = field(default_factory=time.monotonic)
    event: threading.Event = field(default_factory=threading.Event)
    result: dict[str, Any] | None = None
    error: str = ""


class BrowserExtensionBridge:
    """Small localhost job bridge for the Chrome/Edge current-tab extension.

    Browser extensions cannot listen on a local TCP port, so Loom exposes a
    loopback-only HTTP endpoint and the extension long-polls for commands. The
    Python BrowserBackend enqueues one command, waits for a result, and converts
    the payload back into BrowserPageState.
    """

    def __init__(
        self,
        *,
        host: str = DEFAULT_EXTENSION_HOST,
        port: int = DEFAULT_EXTENSION_PORT,
        token: str = DEFAULT_EXTENSION_TOKEN,
        command_timeout: float = 45.0,
        poll_timeout: float = 25.0,
    ) -> None:
        host = str(host or "").strip()
        if host not in {"127.0.0.1", "::1"}:
            raise ValueError("browser extension bridge host must be 127.0.0.1 or ::1")
        port = int(port)
        if not 0 <= port <= 65535:
            raise ValueError("browser extension bridge port must be within 0..65535")
        token = str(token or "").strip()
        if len(token) < 8:
            raise ValueError("browser extension bridge token must be at least 8 characters")
        self.host = host
        self.port = port
        self.token = token
        self.command_timeout = max(1.0, float(command_timeout))
        self.poll_timeout = max(1.0, min(float(poll_timeout), 30.0))

        self._condition = threading.Condition(threading.RLock())
        self._commands: list[_BridgeCommand] = []
        self._pending: dict[str, _BridgeCommand] = {}
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._closed = False
        self._last_client_id = ""
        self._last_client_version = ""
        self._last_poll_at = 0.0
        self._last_result_at = 0.0

    @classmethod
    def from_environment(cls) -> "BrowserExtensionBridge":
        def env(name: str, default: str) -> str:
            return str(os.environ.get(name) or default).strip()

        return cls(
            host=env("LOOM_BROWSER_EXTENSION_HOST", DEFAULT_EXTENSION_HOST),
            port=int(env("LOOM_BROWSER_EXTENSION_PORT", str(DEFAULT_EXTENSION_PORT))),
            token=env("LOOM_BROWSER_EXTENSION_TOKEN", DEFAULT_EXTENSION_TOKEN),
            command_timeout=float(env("LOOM_BROWSER_EXTENSION_TIMEOUT", "45")),
        )

    @property
    def url(self) -> str:
        host = f"[{self.host}]" if ":" in self.host else self.host
        return f"http://{host}:{self.port}"

    @property
    def connected(self) -> bool:
        with self._condition:
            return bool(self._last_poll_at and time.monotonic() - self._last_poll_at < 40.0)

    def status(self) -> dict[str, object]:
        with self._condition:
            return {
                "url": self.url,
                "connected": self.connected,
                "last_client_id": self._last_client_id[-12:] if self._last_client_id else "",
                "last_client_version": self._last_client_version,
                "pending_commands": len(self._pending),
                "queued_commands": len(self._commands),
            }

    def start(self) -> None:
        with self._condition:
            if self._server is not None:
                return
            handler_cls = self._make_handler()
            self._server = ThreadingHTTPServer((self.host, self.port), handler_cls)
            self._server.daemon_threads = True
            self.port = int(self._server.server_address[1])
            self._thread = threading.Thread(
                target=self._server.serve_forever,
                name="loom-browser-extension-bridge",
                daemon=True,
            )
            self._thread.start()

    def stop(self) -> None:
        with self._condition:
            self._closed = True
            server = self._server
            self._server = None
            for command in list(self._pending.values()):
                command.error = "browser extension bridge stopped"
                command.event.set()
            self._commands.clear()
            self._pending.clear()
            self._condition.notify_all()
        if server is not None:
            server.shutdown()
            server.server_close()

    def call(self, action: str, args: dict[str, Any] | None = None) -> dict[str, Any]:
        self.start()
        command = _BridgeCommand(
            command_id=uuid.uuid4().hex,
            action=str(action),
            args=dict(args or {}),
        )
        with self._condition:
            if self._closed:
                raise BrowserError("browser extension bridge is closed")
            self._commands.append(command)
            self._pending[command.command_id] = command
            self._condition.notify_all()
        if not command.event.wait(self.command_timeout):
            with self._condition:
                self._pending.pop(command.command_id, None)
            raise BrowserError(
                "browser extension did not respond. Install/enable extensions/browser-current-tab "
                "and make sure its bridge URL/token match Loom."
            )
        if command.error:
            raise BrowserError(command.error)
        result = command.result or {}
        if not isinstance(result, dict):
            raise BrowserError("browser extension returned a non-object result")
        return result

    def _make_handler(self):
        bridge = self

        class Handler(BaseHTTPRequestHandler):
            server_version = "LoomBrowserExtensionBridge/0.1"

            def log_message(self, format: str, *args: Any) -> None:  # pragma: no cover - keep stdio clean
                return

            def do_OPTIONS(self) -> None:
                self._send_json({"ok": True})

            def do_GET(self) -> None:
                parsed = urlsplit(self.path)
                if parsed.path == "/browser-extension/v1/health":
                    self._send_json({"ok": True, "protocol_version": 1})
                    return
                if parsed.path == "/browser-extension/v1/poll":
                    if not self._authorized(parsed):
                        self._send_json({"ok": False, "error": "unauthorized"}, HTTPStatus.UNAUTHORIZED)
                        return
                    params = parse_qs(parsed.query)
                    client_id = str((params.get("client_id") or [""])[0])[:128]
                    version = str((params.get("version") or [""])[0])[:64]
                    deadline = time.monotonic() + bridge.poll_timeout
                    with bridge._condition:
                        bridge._last_client_id = client_id or bridge._last_client_id
                        bridge._last_client_version = version or bridge._last_client_version
                        bridge._last_poll_at = time.monotonic()
                        while not bridge._commands and not bridge._closed:
                            remaining = deadline - time.monotonic()
                            if remaining <= 0:
                                self._send_json({"ok": True, "command": None})
                                return
                            bridge._condition.wait(timeout=remaining)
                        if bridge._closed:
                            self._send_json({"ok": False, "error": "bridge closed"}, HTTPStatus.GONE)
                            return
                        command = bridge._commands.pop(0)
                    self._send_json(
                        {
                            "ok": True,
                            "command": {
                                "id": command.command_id,
                                "action": command.action,
                                "args": command.args,
                            },
                        }
                    )
                    return
                self._send_json({"ok": False, "error": "not found"}, HTTPStatus.NOT_FOUND)

            def do_POST(self) -> None:
                parsed = urlsplit(self.path)
                if parsed.path == "/browser-extension/v1/register":
                    if not self._authorized(parsed):
                        self._send_json({"ok": False, "error": "unauthorized"}, HTTPStatus.UNAUTHORIZED)
                        return
                    body = self._read_json()
                    with bridge._condition:
                        bridge._last_client_id = str(body.get("client_id") or "")[:128]
                        bridge._last_client_version = str(body.get("version") or "")[:64]
                        bridge._last_poll_at = time.monotonic()
                    self._send_json({"ok": True, "protocol_version": 1})
                    return
                if parsed.path == "/browser-extension/v1/result":
                    if not self._authorized(parsed):
                        self._send_json({"ok": False, "error": "unauthorized"}, HTTPStatus.UNAUTHORIZED)
                        return
                    body = self._read_json()
                    command_id = str(body.get("id") or "")
                    ok = body.get("ok") is True
                    with bridge._condition:
                        command = bridge._pending.pop(command_id, None)
                        bridge._last_result_at = time.monotonic()
                    if command is not None:
                        if ok:
                            result = body.get("result") or {}
                            command.result = result if isinstance(result, dict) else {"value": result}
                        else:
                            command.error = str(body.get("error") or "browser extension command failed")
                        command.event.set()
                    self._send_json({"ok": True})
                    return
                self._send_json({"ok": False, "error": "not found"}, HTTPStatus.NOT_FOUND)

            def _authorized(self, parsed) -> bool:
                supplied = self.headers.get("X-Loom-Token", "")
                if not supplied:
                    supplied = str((parse_qs(parsed.query).get("token") or [""])[0])
                return supplied == bridge.token

            def _read_json(self) -> dict[str, Any]:
                length = int(self.headers.get("Content-Length") or "0")
                raw = self.rfile.read(min(length, 10_000_000)) if length > 0 else b"{}"
                try:
                    payload = json.loads(raw.decode("utf-8"))
                except Exception as exc:
                    raise ValueError("invalid JSON body") from exc
                if not isinstance(payload, dict):
                    raise ValueError("JSON body must be an object")
                return payload

            def _send_json(self, payload: dict[str, Any], status: int | HTTPStatus = HTTPStatus.OK) -> None:
                data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                self.send_response(int(status))
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Loom-Token")
                self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
                self.end_headers()
                self.wfile.write(data)

        return Handler


class BrowserExtensionSessionBackend:
    """BrowserBackend that controls the user's currently active extension tab."""

    backend_name = "browser-extension"

    def __init__(self, *, options: BrowserLaunchOptions, bridge: BrowserExtensionBridge) -> None:
        self.options = options
        self.bridge = bridge
        self.state_revision = 0
        self._started = False
        self._tab_id = ""

    def start(self) -> BrowserPageState:
        self.bridge.start()
        self._started = True
        return self.state()

    def _target_args(self, extra: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = dict(extra or {})
        if self._tab_id:
            payload["tab_id"] = self._tab_id
        return payload

    def state(self) -> BrowserPageState:
        return self._state_from_result(self.bridge.call("state", self._target_args()))

    def navigate(self, url: str, *, new_tab: bool = False) -> BrowserPageState:
        return self._state_from_result(self.bridge.call("navigate", self._target_args({"url": url, "new_tab": bool(new_tab)})))

    def click(self, index: int) -> BrowserPageState:
        return self._state_from_result(self.bridge.call("click", self._target_args({"index": int(index)})))

    def type_text(self, index: int, text: str, *, clear: bool = True) -> BrowserPageState:
        return self._state_from_result(
            self.bridge.call("type_text", self._target_args({"index": int(index), "text": str(text), "clear": bool(clear)}))
        )

    def scroll(self, direction: str, amount: int) -> BrowserPageState:
        return self._state_from_result(
            self.bridge.call("scroll", self._target_args({"direction": str(direction), "amount": int(amount)}))
        )

    def go_back(self) -> BrowserPageState:
        return self._state_from_result(self.bridge.call("go_back", self._target_args()))

    def refresh(self) -> BrowserPageState:
        return self._state_from_result(self.bridge.call("refresh", self._target_args()))

    def tabs(self) -> BrowserPageState:
        return self._state_from_result(self.bridge.call("tabs", self._target_args()))

    def switch_tab(self, tab_id: str) -> BrowserPageState:
        return self._state_from_result(self.bridge.call("switch_tab", {"tab_id": str(tab_id)}))

    def close_tab(self, tab_id: str) -> BrowserPageState:
        return self._state_from_result(self.bridge.call("close_tab", {"tab_id": str(tab_id)}))

    def hover(self, index: int) -> BrowserPageState:
        return self._state_from_result(self.bridge.call("hover", self._target_args({"index": int(index)})))

    def press_key(self, key: str) -> BrowserPageState:
        return self._state_from_result(self.bridge.call("press_key", self._target_args({"key": str(key)})))

    def select_option(self, index: int, value: str) -> BrowserPageState:
        return self._state_from_result(
            self.bridge.call("select_option", self._target_args({"index": int(index), "value": str(value)}))
        )

    def drag(self, source_index: int, target_index: int) -> BrowserPageState:
        return self._state_from_result(
            self.bridge.call("drag", self._target_args({"source_index": int(source_index), "target_index": int(target_index)}))
        )

    def screenshot(self, *, full_page: bool = False) -> bytes:
        result = self.bridge.call("screenshot", self._target_args({"full_page": bool(full_page)}))
        encoded = str(result.get("png_base64") or "")
        if not encoded:
            raise BrowserError("browser extension returned an empty screenshot")
        try:
            return base64.b64decode(encoded, validate=True)
        except Exception as exc:
            raise BrowserError("browser extension returned invalid screenshot data") from exc

    def close(self) -> None:
        self._started = False

    def _state_from_result(self, result: dict[str, Any]) -> BrowserPageState:
        self.state_revision += 1
        tabs_raw = result.get("tabs") or ()
        tabs: list[dict[str, str]] = []
        if isinstance(tabs_raw, list):
            for item in tabs_raw[:100]:
                if not isinstance(item, dict):
                    continue
                tabs.append(
                    {
                        "tab_id": str(item.get("tab_id") or item.get("id") or "")[:128],
                        "url": str(item.get("url") or "")[:4000],
                        "title": str(item.get("title") or "")[:1000],
                    }
                )
        errors_raw = result.get("errors") or ()
        errors = tuple(str(item)[:2000] for item in errors_raw if item) if isinstance(errors_raw, list) else ()
        page_info = result.get("page_info") if isinstance(result.get("page_info"), dict) else {}
        tab_id = str(page_info.get("tab_id") or result.get("tab_id") or "").strip()
        if tab_id:
            self._tab_id = tab_id[:128]
        page_info = {
            "backend": self.backend_name,
            "capture_mode": "current_active_tab",
            **page_info,
        }
        return BrowserPageState(
            url=str(result.get("url") or "about:blank"),
            title=str(result.get("title") or ""),
            dom=str(result.get("dom") or ""),
            tabs=tuple(tabs),
            page_info=page_info,
            errors=errors,
        )


__all__ = [
    "BrowserExtensionBridge",
    "BrowserExtensionSessionBackend",
]
