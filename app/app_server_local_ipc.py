from __future__ import annotations

import hmac
import json
import os
import secrets
import socket
import socketserver
import threading
from pathlib import Path
from typing import Any, Callable

from app.app_server_client import AppServerClientError, JsonRpcClientError


_MAX_MESSAGE_BYTES = 1024 * 1024
_DESCRIPTOR_VERSION = 1
_DESCRIPTOR_NAME = "app-server.json"
_LOOPBACK_HOST = "127.0.0.1"


def resolve_runtime_home(value: str | Path | None = None) -> Path:
    if value:
        return Path(value).expanduser().resolve()
    configured = str(os.environ.get("LOOM_HOME") or "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    return (Path.home() / ".loom").resolve()


def local_app_server_descriptor_path(runtime_home: str | Path | None = None) -> Path:
    return resolve_runtime_home(runtime_home) / "control" / _DESCRIPTOR_NAME


def runtime_home_from_store(store: Any) -> Path:
    root = Path(getattr(store, "root")).expanduser().resolve()
    try:
        return root.parents[1]
    except IndexError as exc:
        raise ValueError("could not derive Loom runtime home from session store") from exc


class _ThreadingTcpServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = False
    daemon_threads = True

    def __init__(self, owner: "LocalAppServerIpcServer") -> None:
        self.owner = owner
        super().__init__((_LOOPBACK_HOST, 0), _JsonRpcHandler)


class _JsonRpcHandler(socketserver.StreamRequestHandler):
    def _write(self, payload: dict[str, Any]) -> None:
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.wfile.write(encoded + b"\n")
        self.wfile.flush()

    def handle(self) -> None:
        owner = self.server.owner
        self.connection.settimeout(owner.client_timeout_seconds)
        raw_auth = self.rfile.readline(_MAX_MESSAGE_BYTES + 2)
        if not raw_auth or len(raw_auth) > _MAX_MESSAGE_BYTES + 1:
            return
        try:
            auth = json.loads(raw_auth.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return
        candidate = str(auth.get("token") or "") if isinstance(auth, dict) else ""
        if not hmac.compare_digest(candidate, owner.token):
            self._write({"ok": False, "error": "unauthorized"})
            return
        self._write({"ok": True, "protocolVersion": _DESCRIPTOR_VERSION})

        controller = owner.controller_factory(owner.service)
        while True:
            raw = self.rfile.readline(_MAX_MESSAGE_BYTES + 2)
            if not raw:
                return
            if len(raw) > _MAX_MESSAGE_BYTES + 1:
                self._write(
                    {
                        "jsonrpc": "2.0",
                        "id": None,
                        "error": {
                            "code": -32600,
                            "message": "Request exceeds the 1 MB app-server message limit",
                        },
                    }
                )
                return
            try:
                payload = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                self._write(
                    {
                        "jsonrpc": "2.0",
                        "id": None,
                        "error": {"code": -32700, "message": f"Parse error: {exc}"},
                    }
                )
                continue
            response = controller.handle(payload)
            if response is not None:
                self._write(response)


class LocalAppServerIpcServer:
    """Authenticated loopback JSON-RPC transport for one App Server service."""

    def __init__(
        self,
        service: Any,
        *,
        controller_factory: Callable[[Any], Any],
        runtime_home: str | Path,
        client_timeout_seconds: float = 300.0,
    ) -> None:
        self.service = service
        self.controller_factory = controller_factory
        self.runtime_home = resolve_runtime_home(runtime_home)
        self.client_timeout_seconds = max(5.0, float(client_timeout_seconds))
        self.token = secrets.token_urlsafe(48)
        self._server: _ThreadingTcpServer | None = None
        self._thread: threading.Thread | None = None
        self._descriptor_path = local_app_server_descriptor_path(self.runtime_home)

    @property
    def running(self) -> bool:
        return self._server is not None and self._thread is not None and self._thread.is_alive()

    def start(self) -> dict[str, Any]:
        if self.running:
            return self.descriptor()
        server = _ThreadingTcpServer(self)
        self._server = server
        descriptor = self.descriptor()
        self._publish_descriptor(descriptor)
        thread = threading.Thread(
            target=server.serve_forever,
            name="loom-app-server-local-ipc",
            daemon=True,
        )
        self._thread = thread
        thread.start()
        return descriptor

    def descriptor(self) -> dict[str, Any]:
        server = self._server
        if server is None:
            raise RuntimeError("local App Server IPC has not started")
        host, port = server.server_address
        return {
            "version": _DESCRIPTOR_VERSION,
            "transport": "jsonl-tcp-loopback",
            "host": str(host),
            "port": int(port),
            "token": self.token,
            "pid": os.getpid(),
        }

    def _publish_descriptor(self, descriptor: dict[str, Any]) -> None:
        target = self._descriptor_path
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f".{target.name}.{os.getpid()}.tmp")
        payload = json.dumps(descriptor, ensure_ascii=False, separators=(",", ":"))
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(payload)
            handle.write("\n")
        try:
            os.chmod(temporary, 0o600)
        except OSError:
            pass
        os.replace(temporary, target)
        try:
            os.chmod(target, 0o600)
        except OSError:
            pass

    def close(self) -> None:
        server, self._server = self._server, None
        thread, self._thread = self._thread, None
        if server is not None:
            server.shutdown()
            server.server_close()
        if thread is not None:
            thread.join(timeout=2.0)
        self._remove_own_descriptor()

    def _remove_own_descriptor(self) -> None:
        target = self._descriptor_path
        try:
            current = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        if not isinstance(current, dict):
            return
        if not hmac.compare_digest(str(current.get("token") or ""), self.token):
            return
        try:
            target.unlink()
        except OSError:
            pass


class LoomLocalAppServerClient:
    """Synchronous client for the authenticated local App Server IPC endpoint."""

    def __init__(
        self,
        runtime_home: str | Path | None = None,
        *,
        request_timeout_seconds: float = 30.0,
    ) -> None:
        self.runtime_home = resolve_runtime_home(runtime_home)
        self.request_timeout_seconds = max(1.0, float(request_timeout_seconds))
        self._socket: socket.socket | None = None
        self._reader = None
        self._writer = None
        self._guard = threading.RLock()
        self._next_id = 0

    @property
    def running(self) -> bool:
        return self._socket is not None

    def connect_and_initialize(
        self,
        *,
        client_name: str,
        client_version: str = "0.1",
    ) -> dict[str, Any]:
        self.connect()
        result = self.request(
            "initialize",
            {
                "protocolVersion": 1,
                "clientInfo": {"name": str(client_name), "version": str(client_version)},
            },
        )
        self.notify("initialized", {})
        if not isinstance(result, dict):
            raise AppServerClientError("local App Server initialize returned an invalid result")
        return result

    def connect(self) -> None:
        if self.running:
            return
        descriptor = self._load_descriptor()
        host = str(descriptor.get("host") or "")
        port = int(descriptor.get("port") or 0)
        token = str(descriptor.get("token") or "")
        if host != _LOOPBACK_HOST or not 1 <= port <= 65535 or not token:
            raise AppServerClientError("invalid local App Server descriptor")
        sock: socket.socket | None = None
        try:
            sock = socket.create_connection(
                (host, port),
                timeout=self.request_timeout_seconds,
            )
            sock.settimeout(self.request_timeout_seconds)
            reader = sock.makefile("r", encoding="utf-8", newline="\n")
            writer = sock.makefile("w", encoding="utf-8", newline="\n")
            writer.write(
                json.dumps({"token": token}, ensure_ascii=False, separators=(",", ":")) + "\n"
            )
            writer.flush()
            line = reader.readline()
            hello = json.loads(line) if line else {}
            if not isinstance(hello, dict) or hello.get("ok") is not True:
                raise AppServerClientError("local App Server authentication failed")
        except Exception as exc:
            if sock is not None:
                try:
                    sock.close()
                except Exception:
                    pass
            if isinstance(exc, AppServerClientError):
                raise
            raise AppServerClientError(f"could not connect to local App Server: {exc}") from exc
        self._socket = sock
        self._reader = reader
        self._writer = writer

    def _load_descriptor(self) -> dict[str, Any]:
        path = local_app_server_descriptor_path(self.runtime_home)
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise AppServerClientError("local App Server endpoint is not available") from exc
        except (OSError, json.JSONDecodeError) as exc:
            raise AppServerClientError(f"could not read local App Server descriptor: {exc}") from exc
        if not isinstance(raw, dict) or int(raw.get("version") or 0) != _DESCRIPTOR_VERSION:
            raise AppServerClientError("unsupported local App Server descriptor")
        return raw

    def request(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        timeout_seconds: float | None = None,
    ) -> Any:
        if not self.running or self._reader is None or self._writer is None:
            raise AppServerClientError("local App Server is not connected")
        resolved_method = str(method or "").strip()
        if not resolved_method:
            raise ValueError("JSON-RPC method must not be empty")
        with self._guard:
            self._next_id += 1
            request_id = self._next_id
            payload = {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": resolved_method,
                "params": params or {},
            }
            timeout = self.request_timeout_seconds if timeout_seconds is None else max(
                1.0, float(timeout_seconds)
            )
            self._socket.settimeout(timeout)
            try:
                self._writer.write(
                    json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n"
                )
                self._writer.flush()
                line = self._reader.readline()
            except (OSError, ValueError) as exc:
                raise AppServerClientError(f"local App Server request failed: {exc}") from exc
            if not line:
                raise AppServerClientError("local App Server connection closed")
            try:
                frame = json.loads(line)
            except json.JSONDecodeError as exc:
                raise AppServerClientError("local App Server returned invalid JSON") from exc
            if not isinstance(frame, dict) or frame.get("id") != request_id:
                raise AppServerClientError("local App Server returned an uncorrelated response")
            if "error" in frame:
                raw = frame.get("error") or {}
                if isinstance(raw, dict):
                    raise JsonRpcClientError(
                        code=int(raw.get("code") or -32603),
                        message=str(raw.get("message") or "JSON-RPC error"),
                        data=raw.get("data"),
                    )
                raise AppServerClientError(str(raw))
            return frame.get("result")

    def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        if not self.running or self._writer is None:
            raise AppServerClientError("local App Server is not connected")
        payload = {"jsonrpc": "2.0", "method": str(method), "params": params or {}}
        with self._guard:
            self._writer.write(
                json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n"
            )
            self._writer.flush()

    def runtime_status(self) -> dict[str, Any]:
        return dict(self.request("runtime/status", {}))

    def project_list(self) -> dict[str, Any]:
        return dict(self.request("project/list", {}))

    def thread_list(self, *, limit: int = 100) -> dict[str, Any]:
        return dict(self.request("thread/list", {"limit": int(limit)}))

    def thread_read(self, thread_id: str) -> dict[str, Any]:
        return dict(self.request("thread/read", {"threadId": str(thread_id)}))

    def thread_start(
        self,
        *,
        workspace: str | Path | None = None,
        project_id: str = "",
        permission_mode: str | None = None,
    ) -> dict[str, Any]:
        if bool(workspace) == bool(project_id):
            raise ValueError("pass exactly one of workspace or project_id")
        params: dict[str, Any] = (
            {"projectId": str(project_id)}
            if project_id
            else {"workspace": str(Path(workspace).expanduser().resolve())}
        )
        if permission_mode:
            params["permissionMode"] = str(permission_mode)
        return dict(self.request("thread/start", params))

    def turn_start(self, thread_id: str, text: str, attachments=()) -> dict[str, Any]:
        params: dict[str, Any] = {"threadId": str(thread_id), "input": str(text)}
        if attachments:
            params["attachments"] = list(attachments)
        return dict(self.request("turn/start", params))

    def turn_steer(
        self,
        thread_id: str,
        turn_id: str,
        text: str,
        *,
        client_input_id: str = "",
    ) -> dict[str, Any]:
        params = {
            "threadId": str(thread_id),
            "turnId": str(turn_id),
            "input": str(text),
        }
        if client_input_id:
            params["clientInputId"] = str(client_input_id)
        return dict(self.request("turn/steer", params))

    def turn_interrupt(self, thread_id: str, turn_id: str) -> dict[str, Any]:
        return dict(
            self.request(
                "turn/interrupt",
                {"threadId": str(thread_id), "turnId": str(turn_id)},
            )
        )

    def approval_respond(
        self,
        thread_id: str,
        *,
        turn_id: str,
        request_id: str,
        call_id: str,
        decision: str,
    ) -> dict[str, Any]:
        return dict(
            self.request(
                "approval/respond",
                {
                    "threadId": str(thread_id),
                    "turnId": str(turn_id),
                    "requestId": str(request_id),
                    "callId": str(call_id),
                    "decision": str(decision),
                },
            )
        )

    def close(self) -> None:
        writer, self._writer = self._writer, None
        reader, self._reader = self._reader, None
        sock, self._socket = self._socket, None
        for stream in (writer, reader):
            if stream is not None:
                try:
                    stream.close()
                except OSError:
                    pass
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass


__all__ = [
    "LocalAppServerIpcServer",
    "LoomLocalAppServerClient",
    "local_app_server_descriptor_path",
    "resolve_runtime_home",
    "runtime_home_from_store",
]
