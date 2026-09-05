from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Sequence


NotificationListener = Callable[[str, dict[str, Any]], None]
TextListener = Callable[[str], None]


class AppServerClientError(RuntimeError):
    pass


class JsonRpcClientError(AppServerClientError):
    def __init__(self, *, code: int, message: str, data: Any = None) -> None:
        super().__init__(f"JSON-RPC {code}: {message}")
        self.code = int(code)
        self.message = str(message)
        self.data = data


@dataclass(frozen=True, slots=True)
class AppServerProcessConfig:
    """Source/packaged process configuration without carrying provider secrets."""

    workspace: str | Path
    provider: str | None = None
    base_url: str | None = None
    model: str | None = None
    home: str | Path | None = None
    permission_mode: str | None = None
    timeout_seconds: float = 120.0
    app_server_executable: str | Path | None = None

    def command(self) -> list[str]:
        if self.app_server_executable:
            command = [str(Path(self.app_server_executable).expanduser())]
        else:
            command = [sys.executable, "-m", "loom_app_server"]
        command.extend(
            [
                "--workspace",
                str(Path(self.workspace).expanduser().resolve()),
                "--timeout",
                str(float(self.timeout_seconds)),
            ]
        )
        if self.provider:
            command.extend(["--provider", str(self.provider)])
        if self.base_url:
            command.extend(["--base-url", str(self.base_url)])
        if self.model:
            command.extend(["--model", str(self.model)])
        if self.home:
            command.extend(["--home", str(Path(self.home).expanduser().resolve())])
        if self.permission_mode:
            command.extend(["--permission-mode", str(self.permission_mode)])
        return command


class LoomAppServerClient:
    """Thread-safe stdio JSON-RPC client for Loom's local App Server.

    The client owns only transport/process lifecycle. It never imports or embeds
    AgentRuntime, provider credentials, tool handlers, or the permission engine.
    """

    def __init__(
        self,
        command: Sequence[str],
        *,
        cwd: str | Path | None = None,
        env: dict[str, str] | None = None,
        request_timeout_seconds: float = 30.0,
    ) -> None:
        if not command:
            raise ValueError("app-server command must not be empty")
        self.command = [str(part) for part in command]
        self.cwd = None if cwd is None else str(Path(cwd).expanduser().resolve())
        self.env = dict(env) if env is not None else None
        self.request_timeout_seconds = max(1.0, float(request_timeout_seconds))
        self._process: subprocess.Popen[str] | None = None
        self._guard = threading.RLock()
        self._write_guard = threading.Lock()
        self._next_id = 0
        self._pending: dict[int, queue.Queue[tuple[str, Any]]] = {}
        self._notification_listeners: list[NotificationListener] = []
        self._stderr_listeners: list[TextListener] = []
        self._exit_listeners: list[TextListener] = []
        self._stderr_tail: deque[str] = deque(maxlen=200)
        self._closing = False
        self._stdout_thread: threading.Thread | None = None
        self._stderr_thread: threading.Thread | None = None

    @property
    def running(self) -> bool:
        process = self._process
        return process is not None and process.poll() is None

    @property
    def stderr_tail(self) -> tuple[str, ...]:
        with self._guard:
            return tuple(self._stderr_tail)

    def subscribe_notifications(self, listener: NotificationListener) -> None:
        if not callable(listener):
            raise TypeError("notification listener must be callable")
        with self._guard:
            self._notification_listeners.append(listener)

    def subscribe_stderr(self, listener: TextListener) -> None:
        if not callable(listener):
            raise TypeError("stderr listener must be callable")
        with self._guard:
            self._stderr_listeners.append(listener)

    def subscribe_exit(self, listener: TextListener) -> None:
        if not callable(listener):
            raise TypeError("exit listener must be callable")
        with self._guard:
            self._exit_listeners.append(listener)

    def start(self) -> None:
        with self._guard:
            if self.running:
                return
            if self._process is not None:
                raise AppServerClientError("app-server client cannot be restarted after close")
            self._closing = False

        kwargs: dict[str, Any] = {
            "stdin": subprocess.PIPE,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
            "text": True,
            "encoding": "utf-8",
            "errors": "replace",
            "bufsize": 1,
            "cwd": self.cwd,
            "env": self.env,
        }
        if os.name == "nt":
            kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            process = subprocess.Popen(self.command, **kwargs)
        except OSError as exc:
            raise AppServerClientError(f"failed to start Loom App Server: {exc}") from exc
        if process.stdin is None or process.stdout is None or process.stderr is None:
            process.kill()
            raise AppServerClientError("failed to create App Server stdio pipes")
        self._process = process
        self._stdout_thread = threading.Thread(
            target=self._stdout_loop,
            name="loom-app-server-stdout",
            daemon=True,
        )
        self._stderr_thread = threading.Thread(
            target=self._stderr_loop,
            name="loom-app-server-stderr",
            daemon=True,
        )
        self._stdout_thread.start()
        self._stderr_thread.start()

    def start_and_initialize(
        self,
        *,
        client_name: str,
        client_version: str = "0.1",
    ) -> dict[str, Any]:
        self.start()
        result = self.request(
            "initialize",
            {
                "protocolVersion": 1,
                "clientInfo": {"name": str(client_name), "version": str(client_version)},
            },
        )
        if not isinstance(result, dict):
            raise AppServerClientError("App Server initialize returned an invalid result")
        self.notify("initialized", {})
        return result

    def request(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        timeout_seconds: float | None = None,
    ) -> Any:
        method = str(method or "").strip()
        if not method:
            raise ValueError("JSON-RPC method must not be empty")
        if not self.running:
            raise AppServerClientError(self._exit_message("Loom App Server is not running"))
        with self._guard:
            self._next_id += 1
            request_id = self._next_id
            waiter: queue.Queue[tuple[str, Any]] = queue.Queue(maxsize=1)
            self._pending[request_id] = waiter
        try:
            self._write_frame(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "method": method,
                    "params": params or {},
                }
            )
            timeout = self.request_timeout_seconds if timeout_seconds is None else max(
                1.0, float(timeout_seconds)
            )
            try:
                kind, payload = waiter.get(timeout=timeout)
            except queue.Empty as exc:
                raise AppServerClientError(f"App Server request timed out: {method}") from exc
            if kind == "result":
                return payload
            if isinstance(payload, Exception):
                raise payload
            raise AppServerClientError(str(payload))
        finally:
            with self._guard:
                self._pending.pop(request_id, None)

    def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        method = str(method or "").strip()
        if not method:
            raise ValueError("JSON-RPC notification method must not be empty")
        if not self.running:
            raise AppServerClientError(self._exit_message("Loom App Server is not running"))
        self._write_frame({"jsonrpc": "2.0", "method": method, "params": params or {}})

    def runtime_status(self) -> dict[str, Any]:
        return dict(self.request("runtime/status", {}))

    def thread_list(self, *, limit: int = 100) -> dict[str, Any]:
        return dict(self.request("thread/list", {"limit": int(limit)}))

    def thread_start(
        self,
        *,
        workspace: str | Path,
        permission_mode: str | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "workspace": str(Path(workspace).expanduser().resolve()),
        }
        if permission_mode:
            params["permissionMode"] = str(permission_mode)
        return dict(self.request("thread/start", params))

    def thread_resume(self, thread_id: str) -> dict[str, Any]:
        return dict(self.request("thread/resume", {"threadId": str(thread_id)}))

    def thread_read(self, thread_id: str) -> dict[str, Any]:
        return dict(self.request("thread/read", {"threadId": str(thread_id)}))

    def thread_fork(self, thread_id: str) -> dict[str, Any]:
        return dict(self.request("thread/fork", {"threadId": str(thread_id)}))

    def turn_start(self, thread_id: str, text: str) -> dict[str, Any]:
        return dict(self.request("turn/start", {"threadId": str(thread_id), "input": str(text)}))

    def turn_interrupt(self, thread_id: str, turn_id: str | None = None) -> dict[str, Any]:
        params: dict[str, Any] = {"threadId": str(thread_id)}
        if turn_id:
            params["turnId"] = str(turn_id)
        return dict(self.request("turn/interrupt", params))

    def approval_respond(self, thread_id: str, call_id: str, *, approved: bool) -> dict[str, Any]:
        return dict(
            self.request(
                "approval/respond",
                {
                    "threadId": str(thread_id),
                    "callId": str(call_id),
                    "approved": bool(approved),
                },
            )
        )

    def _write_frame(self, payload: dict[str, Any]) -> None:
        process = self._process
        if process is None or process.stdin is None or process.poll() is not None:
            raise AppServerClientError(self._exit_message("Loom App Server is not running"))
        line = json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n"
        with self._write_guard:
            try:
                process.stdin.write(line)
                process.stdin.flush()
            except (BrokenPipeError, OSError, ValueError) as exc:
                raise AppServerClientError(self._exit_message("App Server stdin closed")) from exc

    def _stdout_loop(self) -> None:
        process = self._process
        if process is None or process.stdout is None:
            return
        try:
            for raw_line in process.stdout:
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    frame = json.loads(line)
                except json.JSONDecodeError:
                    self._emit_stderr(f"Invalid JSON from App Server stdout: {line[:500]}")
                    continue
                if not isinstance(frame, dict):
                    continue
                if "id" in frame and ("result" in frame or "error" in frame):
                    self._route_response(frame)
                    continue
                method = str(frame.get("method") or "").strip()
                params = frame.get("params")
                if method and isinstance(params, dict):
                    with self._guard:
                        listeners = tuple(self._notification_listeners)
                    for listener in listeners:
                        try:
                            listener(method, dict(params))
                        except Exception:
                            continue
        finally:
            message = self._exit_message("Loom App Server exited")
            self._fail_pending(message)
            self._emit_exit(message)

    def _stderr_loop(self) -> None:
        process = self._process
        if process is None or process.stderr is None:
            return
        for raw_line in process.stderr:
            line = raw_line.rstrip("\r\n")
            if line:
                self._emit_stderr(line)

    def _route_response(self, frame: dict[str, Any]) -> None:
        try:
            request_id = int(frame.get("id"))
        except (TypeError, ValueError):
            return
        with self._guard:
            waiter = self._pending.get(request_id)
        if waiter is None:
            return
        if "error" in frame:
            raw = frame.get("error") or {}
            if isinstance(raw, dict):
                error: Exception = JsonRpcClientError(
                    code=int(raw.get("code") or -32603),
                    message=str(raw.get("message") or "JSON-RPC error"),
                    data=raw.get("data"),
                )
            else:
                error = AppServerClientError(str(raw))
            self._put_waiter(waiter, ("error", error))
        else:
            self._put_waiter(waiter, ("result", frame.get("result")))

    @staticmethod
    def _put_waiter(waiter: queue.Queue[tuple[str, Any]], value: tuple[str, Any]) -> None:
        try:
            waiter.put_nowait(value)
        except queue.Full:
            pass

    def _fail_pending(self, message: str) -> None:
        with self._guard:
            waiters = tuple(self._pending.values())
        error = AppServerClientError(message)
        for waiter in waiters:
            self._put_waiter(waiter, ("error", error))

    def _emit_stderr(self, text: str) -> None:
        with self._guard:
            self._stderr_tail.append(text)
            listeners = tuple(self._stderr_listeners)
        for listener in listeners:
            try:
                listener(text)
            except Exception:
                continue

    def _emit_exit(self, text: str) -> None:
        with self._guard:
            if self._closing:
                return
            listeners = tuple(self._exit_listeners)
        for listener in listeners:
            try:
                listener(text)
            except Exception:
                continue

    def _exit_message(self, prefix: str) -> str:
        process = self._process
        code = None if process is None else process.poll()
        tail = "\n".join(self.stderr_tail[-8:])
        message = prefix if code is None else f"{prefix} (exit code {code})"
        if tail:
            message += f"\n{tail}"
        return message

    def close(self) -> None:
        with self._guard:
            if self._closing:
                return
            self._closing = True
        process = self._process
        if process is None:
            return
        if process.stdin is not None:
            try:
                process.stdin.close()
            except OSError:
                pass
        if process.poll() is None:
            try:
                process.terminate()
                process.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2.0)
        self._fail_pending("Loom App Server client closed")

    def __enter__(self) -> "LoomAppServerClient":
        self.start()
        return self

    def __exit__(self, _exc_type, _exc, _tb) -> None:
        self.close()


__all__ = [
    "AppServerClientError",
    "AppServerProcessConfig",
    "JsonRpcClientError",
    "LoomAppServerClient",
]
