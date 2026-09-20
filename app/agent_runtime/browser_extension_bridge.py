from __future__ import annotations

import base64
import hmac
import json
import os
import secrets
import threading
import time
import uuid
from dataclasses import dataclass, field
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

from .browser_diagnostics import BrowserDiagnosticLog, summarize_bridge_args, summarize_browser_state_payload
from .browser_session import (
    BrowserError,
    BrowserLaunchOptions,
    BrowserPageState,
    BrowserTextNotFoundError,
)


DEFAULT_EXTENSION_HOST = "127.0.0.1"
DEFAULT_EXTENSION_PORT = 39222
# How often a command still waiting re-checks whether anything is there to
# collect it. Short enough that giving up early is actually early, long enough
# not to spin.
_TIMEOUT_POLL_INTERVAL_SECONDS = 1.0
# Slack on top of poll_timeout before silence counts as "no reader". The
# extension re-polls the moment each poll returns, so one missed window is
# already generous.
_POLL_SILENCE_GRACE_SECONDS = 5.0


def _runtime_home() -> Path:
    configured = str(os.environ.get("LOOM_HOME") or "").strip()
    return Path(configured).expanduser().resolve() if configured else (Path.home() / ".loom").resolve()


def _load_or_create_install_token() -> str:
    """Return a stable per-install credential without exposing it in status."""

    target = _runtime_home() / "browser" / "current-tab-bridge.token"
    try:
        value = target.read_text(encoding="utf-8").strip()
        if len(value) >= 32:
            return value
    except OSError:
        pass
    target.parent.mkdir(parents=True, exist_ok=True)
    value = secrets.token_urlsafe(48)
    temporary = target.with_suffix(f".tmp-{os.getpid()}-{threading.get_ident()}")
    temporary.write_text(value, encoding="utf-8")
    try:
        os.chmod(temporary, 0o600)
    except OSError:
        pass
    try:
        os.replace(temporary, target)
    except OSError:
        try:
            temporary.unlink()
        except OSError:
            pass
        existing = target.read_text(encoding="utf-8").strip()
        if len(existing) < 32:
            raise ValueError("stored browser extension bridge token is invalid")
        return existing
    return value


class _BridgeServer(ThreadingHTTPServer):
    """Loopback job server that refuses to share its port on Windows.

    Windows SO_REUSEADDR is not the POSIX one: it lets a second socket bind a port
    another socket is already listening on. Two Loom instances - a checkout and the
    installed build - therefore both "started" the bridge, the extension long-polled
    into whichever one Windows routed it to, and the other sat there queueing
    commands nobody would ever collect. Neither could tell it had lost.

    On POSIX the flag only permits rebinding during TIME_WAIT, which is still
    wanted, so this only changes Windows.
    """

    allow_reuse_address = os.name != "nt"


@dataclass(slots=True)
class _BridgeCommand:
    command_id: str
    action: str
    args: dict[str, Any]
    created_at: float = field(default_factory=time.monotonic)
    event: threading.Event = field(default_factory=threading.Event)
    result: dict[str, Any] | None = None
    error: str = ""
    dispatched_at: float = 0.0
    cancelled: bool = False


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
        token: str | None = None,
        command_timeout: float = 45.0,
        poll_timeout: float = 25.0,
        diagnostics: BrowserDiagnosticLog | None = None,
    ) -> None:
        host = str(host or "").strip()
        if host not in {"127.0.0.1", "::1"}:
            raise ValueError("browser extension bridge host must be 127.0.0.1 or ::1")
        port = int(port)
        if not 0 <= port <= 65535:
            raise ValueError("browser extension bridge port must be within 0..65535")
        token = str(token or _load_or_create_install_token()).strip()
        if len(token) < 8:
            raise ValueError("browser extension bridge token must be at least 8 characters")
        self.host = host
        self.port = port
        self.token = token
        self.command_timeout = max(1.0, float(command_timeout))
        self.poll_timeout = max(1.0, min(float(poll_timeout), 30.0))
        self.diagnostics = diagnostics or BrowserDiagnosticLog.from_environment()

        self._condition = threading.Condition(threading.RLock())
        self._commands: list[_BridgeCommand] = []
        self._pending: dict[str, _BridgeCommand] = {}
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._closed = False
        self._last_client_id = ""
        self._last_client_version = ""
        self._last_browser_name = ""
        self._last_tab_title = ""
        self._last_tab_url = ""
        self._last_tab_id = ""
        self._last_window_id = ""
        self._last_poll_at = 0.0
        self._last_result_at = 0.0
        self._bind_error = ""
        self._log("bridge.created", host=self.host, port=self.port, command_timeout=self.command_timeout)

    @classmethod
    def from_environment(cls) -> "BrowserExtensionBridge":
        def env(name: str, default: str) -> str:
            return str(os.environ.get(name) or default).strip()

        return cls(
            host=env("LOOM_BROWSER_EXTENSION_HOST", DEFAULT_EXTENSION_HOST),
            port=int(env("LOOM_BROWSER_EXTENSION_PORT", str(DEFAULT_EXTENSION_PORT))),
            token=env("LOOM_BROWSER_EXTENSION_TOKEN", _load_or_create_install_token()),
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

    @property
    def closed(self) -> bool:
        """True once stop() ran. A stopped bridge cannot be restarted.

        start() would happily bind a fresh server, but _closed stays set and every
        call() raises, so a caller looking to reuse a bridge has to check this
        rather than assume start() is enough.
        """

        with self._condition:
            return self._closed

    def status(self) -> dict[str, object]:
        with self._condition:
            return {
                "url": self.url,
                "connected": self.connected,
                "last_client_id": self._last_client_id[-12:] if self._last_client_id else "",
                "last_client_version": self._last_client_version,
                "browser": self._last_browser_name,
                "current_tab": {
                    "title": self._last_tab_title,
                    "url": self._last_tab_url,
                    "tab_id": self._last_tab_id,
                    "window_id": self._last_window_id,
                } if self._last_tab_title or self._last_tab_url else None,
                "pending_commands": len(self._pending),
                "queued_commands": len(self._commands),
                "port_conflict": self._bind_error,
                "diagnostics": self.diagnostics.status(expose_path=False),
            }

    @property
    def port_conflict(self) -> str:
        """Why this bridge has no server, when another process owns its port."""

        with self._condition:
            return self._bind_error

    def start(self) -> None:
        with self._condition:
            if self._server is not None:
                return
            handler_cls = self._make_handler()
            try:
                self._server = _BridgeServer((self.host, self.port), handler_cls)
            except OSError as exc:
                # Losing the port must not stop Loom from starting: the browser is
                # one capability, and the honest outcome is a bridge that reports
                # why it is unavailable rather than one that silently competes.
                self._bind_error = (
                    f"another process already owns the browser bridge port {self.port}. "
                    "This is usually a second Loom instance - close it, or set "
                    "LOOM_BROWSER_EXTENSION_PORT to give this one its own port."
                )
                self._log("bridge.bind.conflict", port=self.port, error=str(exc))
                return
            self._bind_error = ""
            self._server.daemon_threads = True
            self.port = int(self._server.server_address[1])
            self._thread = threading.Thread(
                target=self._server.serve_forever,
                name="loom-browser-extension-bridge",
                daemon=True,
            )
            self._thread.start()
        self._log("bridge.started", host=self.host, port=self.port, url_exposed=False, token_exposed=False)

    def stop(self) -> None:
        with self._condition:
            self._closed = True
            server = self._server
            self._server = None
            for command in list(self._pending.values()):
                command.cancelled = True
                command.error = "browser extension bridge stopped"
                command.event.set()
            self._commands.clear()
            self._pending.clear()
            self._condition.notify_all()
        if server is not None:
            server.shutdown()
            server.server_close()
        self._log("bridge.stopped")

    def _await_result(self, command: "_BridgeCommand", wait_seconds: float) -> bool:
        """Wait for a result, giving up early on a command nothing will collect.

        Waiting out the full window was right for a command the extension is
        working on and wrong for one nobody is there to take: with the extension
        disabled, every call sat for 45 seconds before failing, which is how a
        browser_open came to cost three quarters of a minute to report a broken
        install. A live extension re-polls as soon as each poll returns, so it
        stamps _last_poll_at at least once per poll_timeout; silence past that
        window means the queue has no reader.
        """

        deadline = time.monotonic() + wait_seconds
        silence_limit = self.poll_timeout + _POLL_SILENCE_GRACE_SECONDS
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return command.event.is_set()
            if command.event.wait(min(remaining, _TIMEOUT_POLL_INTERVAL_SECONDS)):
                return True
            with self._condition:
                # Dispatched means the extension has it; how long the page then
                # takes is the page's business and gets the whole window.
                # dispatched_at is 0.0 until collected, never None.
                if command.dispatched_at:
                    continue
                last_poll = self._last_poll_at
            silent_for = time.monotonic() - last_poll if last_poll else None
            if silent_for is None or silent_for > silence_limit:
                return False

    def call(
        self,
        action: str,
        args: dict[str, Any] | None = None,
        *,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """Run one command in the extension.

        timeout overrides the bridge default for commands that legitimately take
        longer than an interaction, such as waiting for a page condition: the
        default would fire first and report the extension as unresponsive.
        """

        self.start()
        if self.port_conflict:
            # Without this the caller waits out the full command timeout and is
            # told the extension is unresponsive, which sends them to reinstall an
            # extension that was never the problem.
            raise BrowserError(self.port_conflict)
        action_name = str(action)
        wait_seconds = self.command_timeout if timeout is None else max(1.0, float(timeout))
        command = _BridgeCommand(
            command_id=uuid.uuid4().hex,
            action=action_name,
            args=dict(args or {}),
        )
        self._log(
            "bridge.command.queued",
            command_id=command.command_id,
            action=action_name,
            args=summarize_bridge_args(action_name, command.args),
        )
        with self._condition:
            if self._closed:
                self._log("bridge.command.rejected", command_id=command.command_id, action=action_name, reason="closed")
                raise BrowserError("browser extension bridge is closed")
            self._commands.append(command)
            self._pending[command.command_id] = command
            self._condition.notify_all()
        if not self._await_result(command, wait_seconds):
            with self._condition:
                # A command that timed out before the extension collected it must
                # disappear from both indexes. Leaving it in _commands lets an
                # extension that reconnects later execute an operation Loom has
                # already reported as failed (a "ghost" click/type/navigation).
                pending = self._pending.pop(command.command_id, None)
                if pending is command:
                    command.cancelled = True
                    self._commands[:] = [queued for queued in self._commands if queued is not command]
                phase = "dispatched" if command.dispatched_at else "queued"
                silent_ms = int((time.monotonic() - self._last_poll_at) * 1000) if self._last_poll_at else -1
            elapsed_ms = int((time.monotonic() - command.created_at) * 1000)
            self._log(
                "bridge.command.timeout",
                command_id=command.command_id,
                action=action_name,
                phase=phase,
                elapsed_ms=elapsed_ms,
                poll_silent_ms=silent_ms,
            )
            # These are different failures and the advice differs. Sending someone
            # to reinstall a working extension because a page was slow is how a
            # real 45-second browser_open got reported as a broken install.
            if phase == "dispatched":
                raise BrowserError(
                    f"the browser extension collected this {action_name} command but did not finish it "
                    f"within {elapsed_ms // 1000}s. The extension is connected; the page is most likely "
                    "still loading, showing a modal dialog, or blocked on a permission prompt. Retry, or "
                    "use browser_state to see where the tab actually is."
                )
            raise BrowserError(
                "the Loom browser extension is not collecting commands "
                f"({'never polled this bridge' if silent_ms < 0 else f'last poll {silent_ms // 1000}s ago'}). "
                "Install/enable extensions/browser-current-tab and make sure its bridge URL/token match Loom."
            )
        elapsed_ms = int((time.monotonic() - command.created_at) * 1000)
        if command.error:
            self._log("bridge.command.failed", command_id=command.command_id, action=action_name, elapsed_ms=elapsed_ms, error=command.error)
            raise BrowserError(command.error)
        result = command.result or {}
        if not isinstance(result, dict):
            self._log("bridge.command.invalid_result", command_id=command.command_id, action=action_name, elapsed_ms=elapsed_ms)
            raise BrowserError("browser extension returned a non-object result")
        self._log(
            "bridge.command.completed",
            command_id=command.command_id,
            action=action_name,
            elapsed_ms=elapsed_ms,
            result=summarize_browser_state_payload(result, include_dom_excerpt=action_name != "type_text")
            if action_name != "screenshot"
            else {"png_base64": "[bytes omitted]"},
        )
        return result

    def _log(self, event: str, **fields: Any) -> None:
        try:
            self.diagnostics.event(event, **fields)
        except Exception:
            return

    def _make_handler(self):
        bridge = self

        class Handler(BaseHTTPRequestHandler):
            server_version = "LoomBrowserExtensionBridge/0.1"

            def log_message(self, format: str, *args: Any) -> None:  # pragma: no cover - keep stdio clean
                return

            def do_OPTIONS(self) -> None:
                # Chrome extensions with host permissions do not need page-style
                # CORS opt-in. Deliberately omit Access-Control-Allow-Origin so an
                # arbitrary web page cannot use this localhost service as an API.
                self._send_json({"ok": True})

            def do_GET(self) -> None:
                parsed = urlsplit(self.path)
                if parsed.path == "/browser-extension/v1/health":
                    self._send_json({"ok": True, "protocol_version": 1})
                    return
                if parsed.path == "/browser-extension/v1/poll":
                    if not self._authorized(parsed):
                        bridge._log("bridge.auth.rejected", endpoint="poll")
                        self._send_json({"ok": False, "error": "unauthorized"}, HTTPStatus.UNAUTHORIZED)
                        return
                    params = parse_qs(parsed.query)
                    client_id = str((params.get("client_id") or [""])[0])[:128]
                    version = str((params.get("version") or [""])[0])[:64]
                    browser_name = str((params.get("browser") or [""])[0])[:64]
                    deadline = time.monotonic() + bridge.poll_timeout
                    with bridge._condition:
                        bridge._last_client_id = client_id or bridge._last_client_id
                        bridge._last_client_version = version or bridge._last_client_version
                        bridge._last_browser_name = browser_name or bridge._last_browser_name
                        bridge._last_poll_at = time.monotonic()
                        command: _BridgeCommand | None = None
                        while command is None and not bridge._closed:
                            while bridge._commands:
                                candidate = bridge._commands.pop(0)
                                if candidate.cancelled:
                                    continue
                                if bridge._pending.get(candidate.command_id) is not candidate:
                                    continue
                                candidate.dispatched_at = time.monotonic()
                                command = candidate
                                break
                            if command is not None:
                                break
                            remaining = deadline - time.monotonic()
                            if remaining <= 0:
                                self._send_json({"ok": True, "command": None})
                                return
                            bridge._condition.wait(timeout=remaining)
                        if bridge._closed:
                            self._send_json({"ok": False, "error": "bridge closed"}, HTTPStatus.GONE)
                            return
                        if command is None:
                            self._send_json({"ok": True, "command": None})
                            return
                    bridge._log(
                        "bridge.command.dispatched",
                        command_id=command.command_id,
                        action=command.action,
                        client_id=client_id[-12:],
                        client_version=version,
                        queued_ms=int((command.dispatched_at - command.created_at) * 1000),
                    )
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
                        bridge._log("bridge.auth.rejected", endpoint="register")
                        self._send_json({"ok": False, "error": "unauthorized"}, HTTPStatus.UNAUTHORIZED)
                        return
                    body = self._read_json()
                    with bridge._condition:
                        bridge._last_client_id = str(body.get("client_id") or "")[:128]
                        bridge._last_client_version = str(body.get("version") or "")[:64]
                        bridge._last_browser_name = str(body.get("browser") or "")[:64]
                        active_tab = body.get("active_tab")
                        if isinstance(active_tab, dict):
                            bridge._last_tab_title = str(active_tab.get("title") or "")[:500]
                            bridge._last_tab_url = str(active_tab.get("url") or "")[:4000]
                            bridge._last_tab_id = str(active_tab.get("tab_id") or "")[:64]
                            bridge._last_window_id = str(active_tab.get("window_id") or "")[:64]
                        bridge._last_poll_at = time.monotonic()
                    bridge._log(
                        "bridge.client.registered",
                        client_id=bridge._last_client_id[-12:],
                        client_version=bridge._last_client_version,
                        protocol_version=body.get("protocol_version"),
                    )
                    self._send_json({"ok": True, "protocol_version": 1})
                    return
                if parsed.path == "/browser-extension/v1/result":
                    if not self._authorized(parsed):
                        bridge._log("bridge.auth.rejected", endpoint="result")
                        self._send_json({"ok": False, "error": "unauthorized"}, HTTPStatus.UNAUTHORIZED)
                        return
                    body = self._read_json()
                    command_id = str(body.get("id") or "")
                    ok = body.get("ok") is True
                    with bridge._condition:
                        command = bridge._pending.pop(command_id, None)
                        bridge._last_result_at = time.monotonic()
                    if command is not None and not command.cancelled:
                        if ok:
                            result = body.get("result") or {}
                            command.result = result if isinstance(result, dict) else {"value": result}
                        else:
                            command.error = str(body.get("error") or "browser extension command failed")
                        command.event.set()
                        bridge._log(
                            "bridge.result.received",
                            command_id=command.command_id,
                            action=command.action,
                            ok=ok,
                            elapsed_ms=int((time.monotonic() - command.created_at) * 1000),
                            error=command.error,
                        )
                    else:
                        bridge._log("bridge.result.orphaned", command_id=command_id, ok=ok)
                    self._send_json({"ok": True})
                    return
                self._send_json({"ok": False, "error": "not found"}, HTTPStatus.NOT_FOUND)

            def _authorized(self, parsed) -> bool:
                origin = str(self.headers.get("Origin") or "").strip()
                if origin and not origin.startswith("chrome-extension://"):
                    return False
                supplied = self.headers.get("X-Loom-Token", "")
                return hmac.compare_digest(str(supplied), bridge.token)

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
        self._started_at_ms = 0
        self._tab_id = ""

    def start(self) -> BrowserPageState:
        self.bridge.start()
        self._started = True
        # Wall clock, not monotonic: it is compared against chrome.downloads
        # startTime inside the browser.
        self._started_at_ms = int(time.time() * 1000)
        self._log("backend.session.started", allowed_domains=list(self.options.allowed_domains), headless=self.options.headless)
        return self.state()

    def _target_args(self, extra: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = dict(extra or {})
        if self._tab_id:
            payload["tab_id"] = self._tab_id
        return payload

    def _call_state(self, action: str, args: dict[str, Any] | None = None) -> BrowserPageState:
        payload = dict(args or {})
        self._log(
            "backend.action.started",
            action=action,
            state_revision=self.state_revision,
            tab_id=self._tab_id,
            args=summarize_bridge_args(action, payload),
        )
        result = self.bridge.call(action, payload)
        state = self._state_from_result(result)
        self._log(
            "backend.action.completed",
            action=action,
            state_revision=self.state_revision,
            tab_id=self._tab_id,
            state=summarize_browser_state_payload(result, include_dom_excerpt=action not in {"type_text", "send_text"}),
        )
        return state

    def state(self) -> BrowserPageState:
        return self._call_state("state", self._target_args())

    def navigate(self, url: str, *, new_tab: bool = False) -> BrowserPageState:
        return self._call_state("navigate", self._target_args({"url": url, "new_tab": bool(new_tab)}))

    def click(self, index: int) -> BrowserPageState:
        return self._call_state("click", self._target_args({"index": int(index)}))

    def type_text(self, index: int, text: str, *, clear: bool = True) -> BrowserPageState:
        return self._call_state(
            "type_text",
            self._target_args({"index": int(index), "text": str(text), "clear": bool(clear)}),
        )

    def click_at(self, x: int, y: int, button: str = "left") -> BrowserPageState:
        return self._call_state(
            "click_at",
            self._target_args({"x": int(x), "y": int(y), "button": str(button)}),
        )

    def send_text(self, text: str) -> BrowserPageState:
        value = str(text)
        if not value:
            raise ValueError("browser send_text must not be empty")
        if len(value) > 8000:
            raise ValueError("browser send_text exceeds 8000 characters")
        return self._call_state("send_text", self._target_args({"text": value}))

    def scroll(self, direction: str, amount: int) -> BrowserPageState:
        return self._call_state("scroll", self._target_args({"direction": str(direction), "amount": int(amount)}))

    def go_back(self) -> BrowserPageState:
        return self._call_state("go_back", self._target_args())

    def refresh(self) -> BrowserPageState:
        return self._call_state("refresh", self._target_args())

    def tabs(self) -> BrowserPageState:
        return self._call_state("tabs", self._target_args())

    def switch_tab(self, tab_id: str) -> BrowserPageState:
        return self._call_state("switch_tab", {"tab_id": str(tab_id)})

    def close_tab(self, tab_id: str) -> BrowserPageState:
        return self._call_state("close_tab", {"tab_id": str(tab_id)})

    def go_forward(self) -> BrowserPageState:
        return self._call_state("go_forward", self._target_args())

    def find_text(self, text: str, direction: str = "down") -> BrowserPageState:
        value = str(text or "").strip()
        if not value:
            raise ValueError("browser find text must not be empty")
        if len(value) > 500:
            raise ValueError("browser find text exceeds 500 characters")
        # The extension walks text nodes, so direction does not apply; it is
        # accepted to keep one call shape across both backends.
        result = self.bridge.call("find_text", self._target_args({"text": value}))
        if not bool(result.get("found")):
            raise BrowserTextNotFoundError("browser find matched no text on the page")
        return self._state_from_result(result)

    def dropdown_options(self, index: int) -> list[dict[str, Any]]:
        result = self.bridge.call(
            "dropdown_options", self._target_args({"index": int(index)})
        )
        raw = result.get("options")
        return [dict(item) for item in raw][:300] if isinstance(raw, list) else []

    def evaluate(self, expression: str, *, await_promise: bool = True) -> dict[str, Any]:
        source = str(expression or "")
        if not source.strip():
            raise ValueError("browser evaluate expression must not be empty")
        if len(source) > 20_000:
            raise ValueError("browser evaluate expression exceeds 20,000 characters")
        # The page action resolves synchronously; a promise has to be awaited by
        # the expression itself, which is why the flag is accepted and ignored.
        result = self.bridge.call("evaluate", self._target_args({"expression": source}))
        return dict(result) if isinstance(result, dict) else {"ok": False, "error": "no result"}

    def wait_for(
        self,
        *,
        seconds: float = 0.0,
        for_text: str = "",
        until: str = "",
        timeout_seconds: float = 15.0,
    ) -> dict[str, Any]:
        text = str(for_text or "").strip()
        expression = str(until or "").strip()
        if text and expression:
            raise ValueError("browser wait takes either for_text or until, not both")
        if text:
            if len(text) > 500:
                raise ValueError("browser wait text exceeds 500 characters")
            literal = json.dumps(text)
            expression = (
                "(() => { const t = document.body && document.body.innerText;"
                f" return !!t && t.indexOf({literal}) !== -1; }})()"
            )
        elif expression and len(expression) > 20_000:
            raise ValueError("browser wait condition exceeds 20,000 characters")
        delay = max(0.0, min(float(seconds or 0.0), 60.0))
        if not expression and delay <= 0:
            raise ValueError("browser wait needs seconds, for_text, or until")
        result = self.bridge.call(
            "wait_for",
            self._target_args(
                {"until": expression, "seconds": delay, "timeout_seconds": float(timeout_seconds)}
            ),
            timeout=max(5.0, float(timeout_seconds) + 10.0),
        )
        return dict(result) if isinstance(result, dict) else {"satisfied": False, "waited_ms": 0}

    def cookies(self) -> list[dict[str, Any]]:
        result = self.bridge.call("cookies", self._target_args())
        raw = result.get("cookies")
        return [dict(item) for item in raw] if isinstance(raw, list) else []

    def clear_cookies(self) -> None:
        self.bridge.call("clear_cookies", self._target_args())

    def storage_origins(self) -> list[dict[str, Any]]:
        # Only the current origin: a content script cannot read another origin's
        # storage, unlike the CDP path which enumerates every origin.
        result = self.bridge.call("origin_storage", self._target_args())
        raw = result.get("origins")
        return [dict(item) for item in raw] if isinstance(raw, list) else []

    def downloaded_files(self) -> list[str]:
        # The extension can see every download in the user's browser, so it is
        # told when this session began and reports nothing older.
        result = self.bridge.call("downloads", self._target_args({"since_ms": self._started_at_ms}))
        raw = result.get("files")
        if not isinstance(raw, list):
            return []
        return [str(item.get("path") or "") for item in raw if isinstance(item, dict)]

    def hover(self, index: int) -> BrowserPageState:
        return self._call_state("hover", self._target_args({"index": int(index)}))

    def press_key(self, key: str) -> BrowserPageState:
        return self._call_state("press_key", self._target_args({"key": str(key)}))

    def select_option(self, index: int, value: str) -> BrowserPageState:
        return self._call_state("select_option", self._target_args({"index": int(index), "value": str(value)}))

    def drag(self, source_index: int, target_index: int) -> BrowserPageState:
        return self._call_state(
            "drag",
            self._target_args({"source_index": int(source_index), "target_index": int(target_index)}),
        )

    def screenshot(self, *, full_page: bool = False) -> bytes:
        self._log("backend.action.started", action="screenshot", state_revision=self.state_revision, tab_id=self._tab_id, full_page=bool(full_page))
        result = self.bridge.call("screenshot", self._target_args({"full_page": bool(full_page)}))
        encoded = str(result.get("png_base64") or "")
        if not encoded:
            self._log("backend.action.failed", action="screenshot", state_revision=self.state_revision, tab_id=self._tab_id, error="empty screenshot")
            raise BrowserError("browser extension returned an empty screenshot")
        try:
            data = base64.b64decode(encoded, validate=True)
        except Exception as exc:
            self._log("backend.action.failed", action="screenshot", state_revision=self.state_revision, tab_id=self._tab_id, error="invalid screenshot data")
            raise BrowserError("browser extension returned invalid screenshot data") from exc
        self._log("backend.action.completed", action="screenshot", state_revision=self.state_revision, tab_id=self._tab_id, bytes=len(data))
        return data

    def close(self) -> None:
        was_started = self._started
        self._started = False
        released = -1
        if was_started:
            try:
                # Tab ownership lives in the browser and outlives this session, so
                # it has to be handed back explicitly or the next task inherits
                # tabs the user has since gone back to using. A short timeout: a
                # close must not block on an extension that is already gone, and
                # a browser that never answers has no ownership left to release.
                result = self.bridge.call("release_tabs", {}, timeout=5.0)
                released = int(result.get("released") or 0)
            except Exception:
                released = -1
        self._log(
            "backend.session.closed",
            state_revision=self.state_revision,
            tab_id=self._tab_id,
            released_tabs=released,
        )

    def _log(self, event: str, **fields: Any) -> None:
        diagnostics = getattr(self.bridge, "diagnostics", None)
        if diagnostics is None:
            return
        try:
            diagnostics.event(event, **fields)
        except Exception:
            return

    def _state_from_result(self, result: dict[str, Any]) -> BrowserPageState:
        self.state_revision += 1
        tabs_raw = result.get("tabs") or ()
        tabs: list[dict[str, object]] = []
        if isinstance(tabs_raw, list):
            for item in tabs_raw[:100]:
                if not isinstance(item, dict):
                    continue
                tabs.append(
                    {
                        "tab_id": str(item.get("tab_id") or item.get("id") or "")[:128],
                        "window_id": str(item.get("window_id") or "")[:128],
                        "url": str(item.get("url") or "")[:4000],
                        "title": str(item.get("title") or "")[:1000],
                        "active": bool(item.get("active", False)),
                        "current_window": bool(item.get("current_window", False)),
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
