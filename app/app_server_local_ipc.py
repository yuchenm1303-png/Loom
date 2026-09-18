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
_OWNER_LOCK_NAME = "app-server.lock"
_LOOPBACK_HOST = "127.0.0.1"
_READ_ONLY_METHODS = frozenset(
    {
        "runtime/status",
        "project/list",
        "thread/list",
        "thread/read",
    }
)
_DURABLE_RETRY_METHODS = frozenset({"thread/start", "turn/start"})


class LocalAppServerUnavailable(AppServerClientError):
    """No live desktop App Server endpoint is available to attach to."""


class LocalAppServerSecurityError(AppServerClientError):
    """The published local endpoint failed integrity or authentication checks."""


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
        self.connection.settimeout(owner.auth_timeout_seconds)
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
        # This is a long-lived local control connection. Restrict the timeout to
        # authentication; idle remote channels must not lose their shared App
        # Server attachment merely because no tool call arrived for a while.
        self.connection.settimeout(None)

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
        auth_timeout_seconds: float = 10.0,
    ) -> None:
        self.service = service
        self.controller_factory = controller_factory
        self.runtime_home = resolve_runtime_home(runtime_home)
        self.auth_timeout_seconds = max(1.0, float(auth_timeout_seconds))
        self.token = secrets.token_urlsafe(48)
        self.instance_id = secrets.token_urlsafe(24)
        self._server: _ThreadingTcpServer | None = None
        self._thread: threading.Thread | None = None
        self._descriptor_path = local_app_server_descriptor_path(self.runtime_home)
        self._owner_lock_path = self._descriptor_path.with_name(_OWNER_LOCK_NAME)
        self._owner_lock_file = None

    @property
    def running(self) -> bool:
        return self._server is not None and self._thread is not None and self._thread.is_alive()

    def start(self) -> dict[str, Any]:
        if self.running:
            return self.descriptor()

        self._acquire_ownership()
        server: _ThreadingTcpServer | None = None
        try:
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
        except BaseException:
            self._thread = None
            self._server = None
            if server is not None:
                try:
                    server.server_close()
                except OSError:
                    pass
            self._remove_own_descriptor()
            self._release_ownership()
            raise

    def _acquire_ownership(self) -> None:
        if self._owner_lock_file is not None:
            return

        target = self._owner_lock_path
        target.parent.mkdir(parents=True, exist_ok=True)
        handle = target.open("a+b")
        try:
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"\0")
                handle.flush()
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (OSError, BlockingIOError) as exc:
            handle.close()
            raise RuntimeError(
                "another Loom App Server already owns the local control endpoint"
            ) from exc

        try:
            os.chmod(target, 0o600)
        except OSError:
            pass
        self._owner_lock_file = handle

    def _release_ownership(self) -> None:
        handle, self._owner_lock_file = self._owner_lock_file, None
        if handle is None:
            return
        try:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        finally:
            handle.close()

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
            "instanceId": self.instance_id,
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
        self._release_ownership()

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
        self._attached_identity: tuple[Any, ...] | None = None
        self._client_name = ""
        self._client_version = ""
        self._auto_initialize = False

    @property
    def running(self) -> bool:
        return self._socket is not None

    def connect_and_initialize(
        self,
        *,
        client_name: str,
        client_version: str = "0.1",
    ) -> dict[str, Any]:
        with self._guard:
            self._client_name = str(client_name)
            self._client_version = str(client_version)
            descriptor = self._load_descriptor()
            self._close_transport_locked()
            self._connect_descriptor_locked(descriptor)
            result = self._initialize_attached_locked()
            self._auto_initialize = True
            return result

    def connect(self) -> None:
        with self._guard:
            if self.running:
                return
            descriptor = self._load_descriptor()
            self._connect_descriptor_locked(descriptor)

    def _connect_descriptor_locked(self, descriptor: dict[str, Any]) -> None:
        host = str(descriptor.get("host") or "")
        port = int(descriptor.get("port") or 0)
        token = str(descriptor.get("token") or "")
        transport = str(descriptor.get("transport") or "")
        if (
            transport != "jsonl-tcp-loopback"
            or host != _LOOPBACK_HOST
            or not 1 <= port <= 65535
            or not token
        ):
            raise LocalAppServerSecurityError("invalid local App Server descriptor")
        sock: socket.socket | None = None
        reader = None
        writer = None
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
                raise LocalAppServerSecurityError("local App Server authentication failed")
        except Exception as exc:
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
            if isinstance(exc, LocalAppServerSecurityError):
                raise
            if isinstance(exc, AppServerClientError):
                raise
            raise LocalAppServerSecurityError(
                "a local App Server descriptor is published but its endpoint "
                f"cannot be reached; refusing to start a second Runtime: {exc}"
            ) from exc
        self._socket = sock
        self._reader = reader
        self._writer = writer
        self._attached_identity = self._descriptor_identity(descriptor)

    @staticmethod
    def _descriptor_identity(descriptor: dict[str, Any]) -> tuple[Any, ...]:
        return (
            int(descriptor.get("version") or 0),
            str(descriptor.get("transport") or ""),
            str(descriptor.get("host") or ""),
            int(descriptor.get("port") or 0),
            str(descriptor.get("token") or ""),
            int(descriptor.get("pid") or 0),
            str(descriptor.get("instanceId") or ""),
        )

    def _ensure_current_endpoint_locked(self) -> None:
        try:
            descriptor = self._load_descriptor()
        except (LocalAppServerUnavailable, LocalAppServerSecurityError):
            self._close_transport_locked()
            raise
        identity = self._descriptor_identity(descriptor)
        if self.running and identity == self._attached_identity:
            return

        self._close_transport_locked()
        self._connect_descriptor_locked(descriptor)
        if self._auto_initialize and self._client_name:
            self._initialize_attached_locked()

    def _initialize_attached_locked(self) -> dict[str, Any]:
        if not self.running:
            raise AppServerClientError("local App Server is not connected")
        result = self._request_once_locked(
            "initialize",
            {
                "protocolVersion": 1,
                "clientInfo": {
                    "name": self._client_name or "loom-local-client",
                    "version": self._client_version or "0.1",
                },
            },
            timeout_seconds=self.request_timeout_seconds,
        )
        self._notify_once_locked("initialized", {})
        if not isinstance(result, dict):
            raise AppServerClientError("local App Server initialize returned an invalid result")
        return result

    def _load_descriptor(self) -> dict[str, Any]:
        path = local_app_server_descriptor_path(self.runtime_home)
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise LocalAppServerUnavailable(
                "local App Server endpoint is not available"
            ) from exc
        except OSError as exc:
            raise LocalAppServerSecurityError(
                f"could not safely read local App Server descriptor: {exc}"
            ) from exc
        except json.JSONDecodeError as exc:
            raise LocalAppServerSecurityError(
                "local App Server descriptor is invalid JSON"
            ) from exc
        if not isinstance(raw, dict) or int(raw.get("version") or 0) != _DESCRIPTOR_VERSION:
            raise LocalAppServerSecurityError("unsupported local App Server descriptor")
        return raw

    def request(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        timeout_seconds: float | None = None,
    ) -> Any:
        resolved_method = str(method or "").strip()
        if not resolved_method:
            raise ValueError("JSON-RPC method must not be empty")
        payload = dict(params or {})

        with self._guard:
            self._ensure_current_endpoint_locked()
            try:
                return self._request_once_locked(
                    resolved_method,
                    payload,
                    timeout_seconds=timeout_seconds,
                )
            except JsonRpcClientError:
                raise
            except LocalAppServerSecurityError:
                raise
            except AppServerClientError as exc:
                self._close_transport_locked()
                if not self._method_is_safe_to_retry(resolved_method, payload):
                    raise AppServerClientError(
                        "local App Server connection was lost while handling "
                        f"{resolved_method}; the write outcome may be unknown, "
                        "so Loom refused to replay it automatically"
                    ) from exc

                self._ensure_current_endpoint_locked()
                return self._request_once_locked(
                    resolved_method,
                    payload,
                    timeout_seconds=timeout_seconds,
                )

    def _request_once_locked(
        self,
        method: str,
        params: dict[str, Any],
        *,
        timeout_seconds: float | None = None,
    ) -> Any:
        if not self.running or self._reader is None or self._writer is None or self._socket is None:
            raise AppServerClientError("local App Server is not connected")
        self._next_id += 1
        request_id = self._next_id
        payload = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": params,
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

    @staticmethod
    def _method_is_safe_to_retry(method: str, params: dict[str, Any]) -> bool:
        if method in _READ_ONLY_METHODS:
            return True
        if method in _DURABLE_RETRY_METHODS:
            return bool(str(params.get("clientInputId") or "").strip())
        return False

    def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        resolved_method = str(method or "").strip()
        if not resolved_method:
            raise ValueError("JSON-RPC notification method must not be empty")
        with self._guard:
            self._ensure_current_endpoint_locked()
            self._notify_once_locked(resolved_method, dict(params or {}))

    def _notify_once_locked(self, method: str, params: dict[str, Any]) -> None:
        if not self.running or self._writer is None:
            raise AppServerClientError("local App Server is not connected")
        payload = {"jsonrpc": "2.0", "method": method, "params": params}
        try:
            self._writer.write(
                json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n"
            )
            self._writer.flush()
        except (OSError, ValueError) as exc:
            self._close_transport_locked()
            raise AppServerClientError(
                f"local App Server notification failed: {exc}"
            ) from exc

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
        client_input_id: str = "",
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
        if client_input_id:
            params["clientInputId"] = str(client_input_id)
        return dict(self.request("thread/start", params))

    def turn_start(
        self,
        thread_id: str,
        text: str,
        attachments=(),
        *,
        client_input_id: str = "",
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"threadId": str(thread_id), "input": str(text)}
        if attachments:
            params["attachments"] = list(attachments)
        if client_input_id:
            params["clientInputId"] = str(client_input_id)
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
        with self._guard:
            self._close_transport_locked()
            self._client_name = ""
            self._client_version = ""
            self._auto_initialize = False

    def _close_transport_locked(self) -> None:
        writer, self._writer = self._writer, None
        reader, self._reader = self._reader, None
        sock, self._socket = self._socket, None
        self._attached_identity = None
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
    "LocalAppServerSecurityError",
    "LocalAppServerUnavailable",
    "LoomLocalAppServerClient",
    "local_app_server_descriptor_path",
    "resolve_runtime_home",
    "runtime_home_from_store",
]
