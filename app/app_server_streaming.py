from __future__ import annotations

import copy
from pathlib import Path
from typing import Any, TextIO

from app.agent_runtime import AgentEvent, AgentEventKind, AgentStreamEvent, AgentStreamEventKind, PermissionMode

from .app_server import (
    JsonRpcStdioServer,
    LoomAppServerService,
    LoomRpcController,
    PROTOCOL_VERSION,
    _apply_event_to_item,
    _base_item,
)


def _assistant_step_item_id(step_id: str) -> str:
    return f"assistant:step:{step_id}"


class StreamingLoomAppServerService(LoomAppServerService):
    """App-server adapter that forwards transient Runtime v2 stream events."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._streamed_assistant_steps: set[tuple[str, str, str]] = set()
        subscribe = getattr(self.runtime, "subscribe_stream", None)
        if callable(subscribe):
            subscribe(self._on_runtime_stream)

    @property
    def provider_streaming_enabled(self) -> bool:
        return bool(getattr(self.runtime, "provider_streaming_enabled", False))

    def thread_read(self, params: dict[str, Any]) -> dict[str, Any]:
        payload = super().thread_read(params)
        replacements: dict[str, str] = {}
        for event in payload.get("events") or []:
            if event.get("kind") != AgentEventKind.MODEL_RESPONSE.value:
                continue
            data = event.get("data") or {}
            if not str(data.get("text") or ""):
                continue
            step_id = str(data.get("step_id") or "").strip()
            event_id = str(event.get("eventId") or "").strip()
            if step_id and event_id:
                replacements[f"assistant:{event_id}"] = _assistant_step_item_id(step_id)
        if replacements:
            for turn in payload.get("turns") or []:
                for item in turn.get("items") or []:
                    replacement = replacements.get(str(item.get("id") or ""))
                    if replacement:
                        item["id"] = replacement
        return payload

    def _on_runtime_stream(self, event: AgentStreamEvent) -> None:
        if event.kind is AgentStreamEventKind.ASSISTANT_TEXT_DELTA:
            delta = str(event.data.get("delta") or "")
            if not delta:
                return
            key = (event.session_id, event.turn_id, event.step_id)
            item_id = _assistant_step_item_id(event.step_id)
            with self._guard:
                first = key not in self._streamed_assistant_steps
                self._streamed_assistant_steps.add(key)
            if first:
                self._notify(
                    "item/started",
                    {
                        "item": {
                            "id": item_id,
                            "threadId": event.session_id,
                            "turnId": event.turn_id,
                            "type": "assistant_message",
                            "status": "streaming",
                            "createdAt": event.created_at,
                            "updatedAt": event.created_at,
                        }
                    },
                )
            self._notify(
                "item/delta",
                {
                    "threadId": event.session_id,
                    "turnId": event.turn_id,
                    "itemId": item_id,
                    "delta": {"text": delta},
                },
            )
            return

        if event.kind is AgentStreamEventKind.TOOL_CALL_ARGUMENT_DELTA:
            self._notify(
                "item/delta",
                {
                    "threadId": event.session_id,
                    "turnId": event.turn_id,
                    "itemId": f"model-tool:{event.step_id}:{event.data.get('index', 0)}",
                    "delta": {
                        "kind": "tool_call_argument",
                        "callId": str(event.data.get("call_id") or "") or None,
                        "toolName": str(event.data.get("tool") or "") or None,
                        "arguments": str(event.data.get("arguments_delta") or ""),
                    },
                },
            )
            return

        if event.kind is AgentStreamEventKind.MODEL_STREAM_COMPLETED:
            key = (event.session_id, event.turn_id, event.step_id)
            with self._guard:
                streamed = key in self._streamed_assistant_steps
            if streamed:
                usage = event.data.get("usage") or {}
                self._notify(
                    "item/delta",
                    {
                        "threadId": event.session_id,
                        "turnId": event.turn_id,
                        "itemId": _assistant_step_item_id(event.step_id),
                        "delta": {
                            "finishReason": str(event.data.get("finish_reason") or "") or None,
                            "responseId": str(event.data.get("response_id") or "") or None,
                            "usage": {
                                "inputTokens": int(usage.get("input_tokens") or 0),
                                "outputTokens": int(usage.get("output_tokens") or 0),
                                "totalTokens": int(usage.get("total_tokens") or 0),
                            },
                        },
                    },
                )

    def _on_runtime_event(self, event: AgentEvent) -> None:
        if event.kind is AgentEventKind.MODEL_RESPONSE and str(event.data.get("text") or ""):
            step_id = str(event.data.get("step_id") or "").strip()
            key = (event.session_id, event.turn_id, step_id)
            with self._guard:
                streamed = key in self._streamed_assistant_steps
                if streamed:
                    self._streamed_assistant_steps.discard(key)
            if streamed:
                item = _base_item(
                    event,
                    item_id=_assistant_step_item_id(step_id),
                    item_type="assistant_message",
                    status="started",
                )
                _apply_event_to_item(item, event)
                self._notify("item/completed", {"item": item})
                return

        if event.kind in {
            AgentEventKind.TURN_FAILED,
            AgentEventKind.TURN_CANCELLED,
            AgentEventKind.TURN_INTERRUPTED,
            AgentEventKind.LIMIT_REACHED,
        }:
            with self._guard:
                stale = [
                    key
                    for key in self._streamed_assistant_steps
                    if key[0] == event.session_id and key[1] == event.turn_id
                ]
                for key in stale:
                    self._streamed_assistant_steps.discard(key)
            for _session_id, _turn_id, step_id in stale:
                self._notify(
                    "item/completed",
                    {
                        "item": {
                            "id": _assistant_step_item_id(step_id),
                            "threadId": event.session_id,
                            "turnId": event.turn_id,
                            "type": "assistant_message",
                            "status": event.kind.value.removeprefix("turn_"),
                            "updatedAt": event.created_at,
                        }
                    },
                )

        super()._on_runtime_event(event)


class StreamingLoomRpcController(LoomRpcController):
    def _initialize(self, params: dict[str, Any]) -> dict[str, Any]:
        result = super()._initialize(params)
        service = self.service
        enabled = bool(getattr(service, "provider_streaming_enabled", False))
        result["capabilities"]["providerStreaming"] = enabled
        result["capabilities"]["runtimeStream"] = {
            "assistantTextDelta": enabled,
            "toolCallArgumentDelta": enabled,
            "usageCompletionMetadata": enabled,
            "privateReasoning": False,
        }
        return result


class StreamingJsonRpcStdioServer(JsonRpcStdioServer):
    def __init__(self, service: StreamingLoomAppServerService, **kwargs: Any) -> None:
        super().__init__(service, **kwargs)
        self.controller = StreamingLoomRpcController(service)


def serve_streaming_stdio(
    *,
    runtime: Any,
    store: Any,
    model: str,
    default_workspace: str | Path,
    default_permission_mode: PermissionMode | str,
    reader: TextIO | None = None,
    writer: TextIO | None = None,
) -> int:
    service = StreamingLoomAppServerService(
        runtime=runtime,
        store=store,
        model=model,
        default_workspace=default_workspace,
        default_permission_mode=default_permission_mode,
    )
    server = StreamingJsonRpcStdioServer(service)
    try:
        return server.serve(reader=reader, writer=writer)
    finally:
        close = getattr(runtime, "close", None)
        if callable(close):
            close()


__all__ = [
    "StreamingJsonRpcStdioServer",
    "StreamingLoomAppServerService",
    "StreamingLoomRpcController",
    "serve_streaming_stdio",
]
