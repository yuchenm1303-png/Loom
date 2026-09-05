from __future__ import annotations

import json
import threading
import uuid
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from app.ai import AGENT_FAST_ROLE, ImagePart, MessageRole, TextPart
from app.agent_runtime import AgentStatus, PermissionMode


_STATIC_DIR = Path(__file__).with_name("web_static")
_MAX_JSON_BODY = 1_000_000
_ALLOWED_HOSTS = {"127.0.0.1", "localhost", "::1"}


def _message_text(message: Any) -> str:
    content = message.content
    if isinstance(content, str):
        return content
    parts: list[str] = []
    for part in content:
        if isinstance(part, TextPart):
            parts.append(part.text)
        elif isinstance(part, ImagePart):
            parts.append("[image]")
    return "\n".join(parts)


def _message_record(message: Any) -> dict[str, Any]:
    return {
        "role": message.role.value,
        "content": _message_text(message),
        "name": message.name,
        "tool_call_id": message.tool_call_id,
        "tool_calls": [
            {
                "call_id": call.call_id,
                "name": call.name,
                "arguments": call.arguments,
            }
            for call in message.tool_calls
        ],
    }


def _approval_record(pending: Any) -> dict[str, Any] | None:
    if pending is None:
        return None
    return {
        "call_id": pending.call_id,
        "tool_name": pending.tool_name,
        "arguments": pending.arguments,
        "effect": pending.effect.value,
        "reason": pending.reason,
    }


def _event_record(event: Any) -> dict[str, Any]:
    return {
        "event_id": event.event_id,
        "turn_id": event.turn_id,
        "kind": event.kind.value,
        "created_at": event.created_at,
        "data": event.data,
    }


def _session_title(session: Any) -> str:
    for message in session.messages:
        if message.role is MessageRole.USER:
            text = _message_text(message).strip().replace("\n", " ")
            if text:
                return text[:56]
    workspace = Path(session.workspace_dir)
    return workspace.name or "New session"


class LoomWebService:
    """Thin same-process adapter from the local web UI to the real Loom runtime."""

    def __init__(
        self,
        *,
        runtime: Any,
        store: Any,
        model: str,
        default_workspace: str | Path,
        default_permission_mode: PermissionMode | str = PermissionMode.APPROVAL,
        preferred_session_id: str = "",
    ) -> None:
        self.runtime = runtime
        self.store = store
        self.model = str(model or "").strip()
        self.default_workspace = Path(default_workspace).expanduser().resolve()
        self.default_permission_mode = PermissionMode(default_permission_mode)
        self.preferred_session_id = str(preferred_session_id or "").strip()
        self._guard = threading.RLock()
        self._active_sessions: set[str] = set()
        self._task_errors: dict[str, str] = {}

    def _load(self, session_id: str):
        session = self.runtime.get_session(session_id)
        with self._guard:
            active = session_id in self._active_sessions
        if session.status is AgentStatus.RUNNING and not active:
            self.runtime.recover_interrupted(session_id)
            session = self.runtime.get_session(session_id)
        return session

    def list_sessions(self) -> list[dict[str, Any]]:
        sessions = []
        if self.store.root.is_dir():
            for directory in self.store.root.iterdir():
                if not (directory / "session.json").is_file():
                    continue
                try:
                    session = self.store.load(directory.name)
                except Exception:
                    continue
                sessions.append(session)
        sessions.sort(key=lambda item: item.updated_at, reverse=True)
        return [self._summary(item) for item in sessions[:100]]

    def _summary(self, session: Any) -> dict[str, Any]:
        with self._guard:
            active = session.session_id in self._active_sessions
        return {
            "session_id": session.session_id,
            "title": _session_title(session),
            "workspace_dir": session.workspace_dir,
            "permission_mode": session.permission_mode.value,
            "status": session.status.value,
            "updated_at": session.updated_at,
            "active": active,
            "tokens": int(session.usage.total_tokens),
        }

    def bootstrap(self) -> dict[str, Any]:
        return {
            "model": self.model,
            "default_workspace": str(self.default_workspace),
            "default_permission_mode": self.default_permission_mode.value,
            "permission_modes": [mode.value for mode in PermissionMode],
            "preferred_session_id": self.preferred_session_id,
            "sessions": self.list_sessions(),
        }

    def create_session(self, *, workspace: str, permission_mode: str) -> dict[str, Any]:
        root = Path(workspace or self.default_workspace).expanduser().resolve()
        if not root.exists():
            raise ValueError(f"Workspace does not exist: {root}")
        if not root.is_dir():
            raise ValueError(f"Workspace is not a directory: {root}")
        mode = PermissionMode(permission_mode or self.default_permission_mode.value)
        session = self.runtime.create_session(
            AGENT_FAST_ROLE.role_id,
            workspace_dir=root,
            permission_mode=mode,
        )
        return self.snapshot(session.session_id)

    def snapshot(self, session_id: str) -> dict[str, Any]:
        session = self._load(session_id)
        events = self.store.events(session_id)
        with self._guard:
            active = session_id in self._active_sessions
            task_error = self._task_errors.get(session_id, "")
        return {
            "session": self._summary(session),
            "messages": [_message_record(message) for message in session.messages],
            "events": [_event_record(event) for event in events[-240:]],
            "pending_approval": _approval_record(session.pending_approval),
            "usage": {
                "input_tokens": int(session.usage.input_tokens),
                "output_tokens": int(session.usage.output_tokens),
                "total_tokens": int(session.usage.total_tokens),
            },
            "error": session.error or task_error,
            "final_text": session.final_text,
            "active": active,
        }

    def _launch(self, session_id: str, operation: Any) -> None:
        with self._guard:
            if session_id in self._active_sessions:
                raise RuntimeError("A turn is already running for this session")
            self._active_sessions.add(session_id)
            self._task_errors.pop(session_id, None)

        def runner() -> None:
            try:
                operation()
            except Exception as exc:  # runtime persists its own detailed failure state when available
                with self._guard:
                    self._task_errors[session_id] = f"{type(exc).__name__}: local UI task failed"
            finally:
                with self._guard:
                    self._active_sessions.discard(session_id)

        threading.Thread(
            target=runner,
            name=f"loom-ui-{session_id[:8]}-{uuid.uuid4().hex[:6]}",
            daemon=True,
        ).start()

    def start_turn(self, session_id: str, text: str) -> dict[str, Any]:
        prompt = str(text or "").strip()
        if not prompt:
            raise ValueError("Message must not be empty")
        session = self._load(session_id)
        if session.status is AgentStatus.WAITING_APPROVAL:
            raise RuntimeError("Resolve the pending approval before starting another turn")
        self._launch(session_id, lambda: self.runtime.start_turn(session_id, prompt))
        return {"accepted": True, "session_id": session_id}

    def resume_approval(self, session_id: str, *, call_id: str, approved: bool) -> dict[str, Any]:
        session = self._load(session_id)
        pending = session.pending_approval
        if pending is None:
            raise RuntimeError("This session has no pending approval")
        if pending.call_id != str(call_id or "").strip():
            raise ValueError("Approval call id does not match the pending request")
        self._launch(
            session_id,
            lambda: self.runtime.resume_approval(
                session_id,
                pending.call_id,
                approved=bool(approved),
            ),
        )
        return {"accepted": True, "session_id": session_id}

    def cancel(self, session_id: str) -> dict[str, Any]:
        self.runtime.cancel(session_id)
        return self.snapshot(session_id)

    def set_permission_mode(self, session_id: str, mode: str) -> dict[str, Any]:
        with self._guard:
            if session_id in self._active_sessions:
                raise RuntimeError("Cannot change permissions while a turn is running")
        self.runtime.set_permission_mode(session_id, PermissionMode(mode))
        return self.snapshot(session_id)


class LoomWebHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, server_address: tuple[str, int], service: LoomWebService) -> None:
        self.service = service
        super().__init__(server_address, LoomWebHandler)


class LoomWebHandler(BaseHTTPRequestHandler):
    server_version = "LoomLocalUI/1"

    @property
    def service(self) -> LoomWebService:
        return self.server.service  # type: ignore[attr-defined]

    def log_message(self, format: str, *args: Any) -> None:
        return

    def _host_allowed(self) -> bool:
        raw = str(self.headers.get("Host") or "").strip()
        if not raw:
            return False
        try:
            parsed = urlsplit(f"http://{raw}")
        except ValueError:
            return False
        return (parsed.hostname or "").casefold() in _ALLOWED_HOSTS

    def _origin_allowed(self) -> bool:
        origin = str(self.headers.get("Origin") or "").strip()
        if not origin:
            return True
        try:
            parsed = urlsplit(origin)
        except ValueError:
            return False
        request_host = str(self.headers.get("Host") or "").strip().casefold()
        return parsed.scheme in {"http", "https"} and parsed.netloc.casefold() == request_host

    def _security_headers(self) -> None:
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
            "connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'",
        )

    def _send_bytes(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self._security_headers()
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, payload: Any) -> None:
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self._send_bytes(status, body, "application/json; charset=utf-8")

    def _error(self, status: int, message: str) -> None:
        self._json(status, {"error": str(message or HTTPStatus(status).phrase)})

    def _read_json(self) -> dict[str, Any]:
        content_type = str(self.headers.get("Content-Type") or "").split(";", 1)[0].strip().casefold()
        if content_type != "application/json":
            raise ValueError("Content-Type must be application/json")
        raw_length = str(self.headers.get("Content-Length") or "0")
        try:
            length = int(raw_length)
        except ValueError as exc:
            raise ValueError("Invalid Content-Length") from exc
        if length < 0 or length > _MAX_JSON_BODY:
            raise ValueError("JSON request body is too large")
        raw = self.rfile.read(length)
        payload = json.loads(raw.decode("utf-8") or "{}")
        if not isinstance(payload, dict):
            raise ValueError("JSON request body must be an object")
        return payload

    def _segments(self) -> list[str]:
        return [segment for segment in urlsplit(self.path).path.split("/") if segment]

    def do_GET(self) -> None:
        if not self._host_allowed():
            self._error(HTTPStatus.FORBIDDEN, "Local UI only accepts localhost requests")
            return
        path = urlsplit(self.path).path
        if path == "/api/bootstrap":
            self._json(HTTPStatus.OK, self.service.bootstrap())
            return
        segments = self._segments()
        if len(segments) == 3 and segments[:2] == ["api", "sessions"]:
            try:
                self._json(HTTPStatus.OK, self.service.snapshot(segments[2]))
            except (FileNotFoundError, KeyError, ValueError) as exc:
                self._error(HTTPStatus.NOT_FOUND, str(exc))
            return
        assets = {
            "/": ("index.html", "text/html; charset=utf-8"),
            "/index.html": ("index.html", "text/html; charset=utf-8"),
            "/app.js": ("app.js", "text/javascript; charset=utf-8"),
            "/styles.css": ("styles.css", "text/css; charset=utf-8"),
        }
        asset = assets.get(path)
        if asset is None:
            self._error(HTTPStatus.NOT_FOUND, "Not found")
            return
        target = _STATIC_DIR / asset[0]
        try:
            body = target.read_bytes()
        except FileNotFoundError:
            self._error(HTTPStatus.INTERNAL_SERVER_ERROR, "UI asset is missing")
            return
        self._send_bytes(HTTPStatus.OK, body, asset[1])

    def do_POST(self) -> None:
        if not self._host_allowed() or not self._origin_allowed():
            self._error(HTTPStatus.FORBIDDEN, "Cross-origin requests are not allowed")
            return
        try:
            payload = self._read_json()
            segments = self._segments()
            if segments == ["api", "sessions"]:
                result = self.service.create_session(
                    workspace=str(payload.get("workspace") or ""),
                    permission_mode=str(payload.get("permission_mode") or ""),
                )
                self._json(HTTPStatus.CREATED, result)
                return
            if len(segments) == 4 and segments[:2] == ["api", "sessions"]:
                session_id = segments[2]
                action = segments[3]
                if action == "turn":
                    self._json(
                        HTTPStatus.ACCEPTED,
                        self.service.start_turn(session_id, str(payload.get("text") or "")),
                    )
                    return
                if action == "approval":
                    self._json(
                        HTTPStatus.ACCEPTED,
                        self.service.resume_approval(
                            session_id,
                            call_id=str(payload.get("call_id") or ""),
                            approved=bool(payload.get("approved")),
                        ),
                    )
                    return
                if action == "cancel":
                    self._json(HTTPStatus.OK, self.service.cancel(session_id))
                    return
                if action == "permission":
                    self._json(
                        HTTPStatus.OK,
                        self.service.set_permission_mode(
                            session_id,
                            str(payload.get("permission_mode") or ""),
                        ),
                    )
                    return
            self._error(HTTPStatus.NOT_FOUND, "Not found")
        except (ValueError, KeyError, FileNotFoundError) as exc:
            self._error(HTTPStatus.BAD_REQUEST, str(exc))
        except RuntimeError as exc:
            self._error(HTTPStatus.CONFLICT, str(exc))


def create_web_server(service: LoomWebService, *, port: int = 8765) -> LoomWebHTTPServer:
    resolved_port = int(port)
    if not 0 <= resolved_port <= 65535:
        raise ValueError("web port must be within 0..65535")
    return LoomWebHTTPServer(("127.0.0.1", resolved_port), service)


def serve_local_ui(
    *,
    runtime: Any,
    store: Any,
    model: str,
    default_workspace: str | Path,
    default_permission_mode: PermissionMode | str,
    preferred_session_id: str = "",
    port: int = 8765,
    open_browser: bool = True,
) -> int:
    service = LoomWebService(
        runtime=runtime,
        store=store,
        model=model,
        default_workspace=default_workspace,
        default_permission_mode=default_permission_mode,
        preferred_session_id=preferred_session_id,
    )
    server = create_web_server(service, port=port)
    actual_port = int(server.server_address[1])
    url = f"http://127.0.0.1:{actual_port}/"
    print(f"Loom local UI · {model}")
    print(f"Open · {url}")
    print("Local-only server; press Ctrl+C to stop.")
    if open_browser:
        threading.Timer(0.15, lambda: webbrowser.open(url, new=2)).start()
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        print("\nStopping Loom local UI…")
    finally:
        server.server_close()
        close = getattr(runtime, "close", None)
        if callable(close):
            close()
    return 0


__all__ = [
    "LoomWebHTTPServer",
    "LoomWebService",
    "create_web_server",
    "serve_local_ui",
]
