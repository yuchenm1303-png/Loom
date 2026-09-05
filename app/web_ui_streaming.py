from __future__ import annotations

import threading
import webbrowser
from pathlib import Path
from typing import Any

from app.agent_runtime import AgentEvent, AgentEventKind, AgentStreamEvent, AgentStreamEventKind, PermissionMode

from .web_ui import LoomWebService, create_web_server


_MAX_LIVE_TEXT = 1_000_000


class StreamingLoomWebService(LoomWebService):
    """Existing local Web UI adapter plus transient assistant stream state.

    The browser still polls the local snapshot endpoint as a fallback/debug client,
    but each active snapshot now contains the provider text accumulated so far
    instead of waiting for the final MODEL_RESPONSE commit.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._live_streams: dict[str, dict[str, Any]] = {}
        subscribe_stream = getattr(self.runtime, "subscribe_stream", None)
        if callable(subscribe_stream):
            subscribe_stream(self._on_runtime_stream)
        subscribe = getattr(self.runtime, "subscribe", None)
        if callable(subscribe):
            subscribe(self._on_runtime_event)

    @property
    def provider_streaming_enabled(self) -> bool:
        return bool(getattr(self.runtime, "provider_streaming_enabled", False))

    def bootstrap(self) -> dict[str, Any]:
        payload = super().bootstrap()
        payload["provider_streaming"] = self.provider_streaming_enabled
        return payload

    def _on_runtime_stream(self, event: AgentStreamEvent) -> None:
        if event.kind is not AgentStreamEventKind.ASSISTANT_TEXT_DELTA:
            return
        delta = str(event.data.get("delta") or "")
        if not delta:
            return
        with self._guard:
            current = self._live_streams.get(event.session_id)
            if (
                current is None
                or current.get("turn_id") != event.turn_id
                or current.get("step_id") != event.step_id
            ):
                current = {
                    "turn_id": event.turn_id,
                    "step_id": event.step_id,
                    "text": "",
                    "revision": 0,
                }
                self._live_streams[event.session_id] = current
            text = str(current.get("text") or "") + delta
            if len(text) > _MAX_LIVE_TEXT:
                text = text[:_MAX_LIVE_TEXT]
            current["text"] = text
            current["revision"] = int(current.get("revision") or 0) + 1

    def _on_runtime_event(self, event: AgentEvent) -> None:
        if event.kind in {
            AgentEventKind.TURN_STARTED,
            AgentEventKind.MODEL_RESPONSE,
            AgentEventKind.TURN_COMPLETED,
            AgentEventKind.TURN_FAILED,
            AgentEventKind.TURN_CANCELLED,
            AgentEventKind.TURN_INTERRUPTED,
            AgentEventKind.LIMIT_REACHED,
        }:
            with self._guard:
                self._live_streams.pop(event.session_id, None)

    def snapshot(self, session_id: str) -> dict[str, Any]:
        payload = super().snapshot(session_id)
        with self._guard:
            current = dict(self._live_streams.get(session_id) or {})
        text = str(current.get("text") or "")
        revision = int(current.get("revision") or 0)
        if text and payload.get("active"):
            payload["messages"] = [
                *payload.get("messages", []),
                {
                    "role": "assistant",
                    "content": text,
                    "name": "",
                    "tool_call_id": "",
                    "tool_calls": [],
                    "streaming": True,
                },
            ]
            # Existing Web UI render invalidation keys already include updated_at.
            # A presentation-only suffix makes each observed stream revision render
            # without mutating the durable session timestamp on disk.
            session = payload.get("session") or {}
            session["updated_at"] = f"{session.get('updated_at', '')}#stream-{revision}"
        payload["streaming"] = {
            "enabled": self.provider_streaming_enabled,
            "active": bool(text),
            "revision": revision,
            "turn_id": str(current.get("turn_id") or ""),
            "step_id": str(current.get("step_id") or ""),
        }
        return payload


def serve_streaming_local_ui(
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
    service = StreamingLoomWebService(
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


__all__ = ["StreamingLoomWebService", "serve_streaming_local_ui"]
