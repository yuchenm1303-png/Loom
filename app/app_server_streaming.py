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
    _tool_item_id,
)


def _assistant_step_item_id(step_id: str) -> str:
    return f"assistant:step:{step_id}"


class StreamingLoomAppServerService(LoomAppServerService):
    """App-server adapter that forwards transient Runtime v2 stream events."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._streamed_assistant_steps: set[tuple[str, str, str]] = set()
        # Provider tool-call chunks can precede the chunk that carries the final
        # call id. Buffer by model-step/index until the durable call identity is
        # known, then use tool:<call_id> for the whole live/durable lifecycle.
        self._streamed_tool_calls: dict[tuple[str, str, str, int], dict[str, Any]] = {}
        self._stream_last_tool_index: dict[tuple[str, str, str], int] = {}
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

    def _tool_stream_state(self, event: AgentStreamEvent) -> tuple[dict[str, Any], int]:
        base_key = (event.session_id, event.turn_id, event.step_id)
        raw_index = event.data.get("index")
        with self._guard:
            if raw_index is None:
                index = self._stream_last_tool_index.get(base_key, 0)
            else:
                index = max(0, int(raw_index))
                self._stream_last_tool_index[base_key] = index
            key = (*base_key, index)
            state = self._streamed_tool_calls.setdefault(
                key,
                {
                    "call_id": "",
                    "tool_name": "",
                    "pending_arguments": [],
                    "started": False,
                },
            )
        return state, index

    def _emit_tool_argument_delta(
        self,
        event: AgentStreamEvent,
        *,
        call_id: str,
        tool_name: str,
        fragment: str,
    ) -> None:
        self._notify(
            "item/delta",
            {
                "threadId": event.session_id,
                "turnId": event.turn_id,
                "itemId": _tool_item_id(call_id),
                "delta": {
                    "kind": "tool_call_argument",
                    "callId": call_id,
                    "toolName": tool_name or None,
                    "arguments": fragment,
                },
            },
        )

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
            state, _index = self._tool_stream_state(event)
            incoming_call_id = str(event.data.get("call_id") or "").strip()
            incoming_tool_name = str(event.data.get("tool") or "").strip()
            fragment = str(event.data.get("arguments_delta") or "")
            with self._guard:
                if incoming_call_id:
                    existing_call_id = str(state.get("call_id") or "")
                    if existing_call_id and existing_call_id != incoming_call_id:
                        # The provider accumulator will reject this malformed
                        # stream before a canonical ToolCall is committed. Keep
                        # the first public identity stable rather than orphaning
                        # a second client item.
                        incoming_call_id = existing_call_id
                    else:
                        state["call_id"] = incoming_call_id
                if incoming_tool_name:
                    existing_name = str(state.get("tool_name") or "")
                    if not existing_name:
                        state["tool_name"] = incoming_tool_name
                    elif existing_name != incoming_tool_name:
                        state["tool_name"] = existing_name + incoming_tool_name
                if fragment:
                    state["pending_arguments"].append(fragment)
                call_id = str(state.get("call_id") or "")
                tool_name = str(state.get("tool_name") or "")
                first = bool(call_id) and not bool(state.get("started"))
                if first:
                    state["started"] = True
                pending = list(state.get("pending_arguments") or []) if call_id else []
                if call_id:
                    state["pending_arguments"].clear()

            # Do not emit a provisional index-only item. Wait until the provider
            # has supplied the real call id, then flush all earlier fragments to
            # that same identity used by TOOL_REQUESTED/STARTED/COMPLETED.
            if not call_id:
                return
            if first:
                self._notify(
                    "item/started",
                    {
                        "item": {
                            "id": _tool_item_id(call_id),
                            "threadId": event.session_id,
                            "turnId": event.turn_id,
                            "type": "tool_call",
                            "status": "streaming_arguments",
                            "createdAt": event.created_at,
                            "updatedAt": event.created_at,
                            "callId": call_id,
                            "toolName": tool_name or None,
                        }
                    },
                )
            for buffered_fragment in pending:
                self._emit_tool_argument_delta(
                    event,
                    call_id=call_id,
                    tool_name=tool_name,
                    fragment=buffered_fragment,
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

    def _take_streamed_tool_call(self, event: AgentEvent) -> dict[str, Any] | None:
        call_id = str(event.data.get("call_id") or "").strip()
        if not call_id:
            return None
        with self._guard:
            for key, state in tuple(self._streamed_tool_calls.items()):
                if key[0] != event.session_id or key[1] != event.turn_id:
                    continue
                if str(state.get("call_id") or "") != call_id:
                    continue
                self._streamed_tool_calls.pop(key, None)
                return dict(state)
        return None

    def _clear_turn_tool_streams(self, event: AgentEvent, *, close_started: bool) -> None:
        with self._guard:
            stale = [
                (key, dict(state))
                for key, state in self._streamed_tool_calls.items()
                if key[0] == event.session_id and key[1] == event.turn_id
            ]
            for key, _state in stale:
                self._streamed_tool_calls.pop(key, None)
            stale_steps = [
                key
                for key in self._stream_last_tool_index
                if key[0] == event.session_id and key[1] == event.turn_id
            ]
            for key in stale_steps:
                self._stream_last_tool_index.pop(key, None)
        if not close_started:
            return
        for _key, state in stale:
            call_id = str(state.get("call_id") or "")
            if not call_id or not state.get("started"):
                continue
            self._notify(
                "item/completed",
                {
                    "item": {
                        "id": _tool_item_id(call_id),
                        "threadId": event.session_id,
                        "turnId": event.turn_id,
                        "type": "tool_call",
                        "status": event.kind.value.removeprefix("turn_"),
                        "updatedAt": event.created_at,
                        "callId": call_id,
                        "toolName": str(state.get("tool_name") or "") or None,
                    }
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

        if event.kind is AgentEventKind.TOOL_REQUESTED:
            streamed_tool = self._take_streamed_tool_call(event)
            if streamed_tool is not None and streamed_tool.get("started"):
                call_id = str(event.data.get("call_id") or "")
                self._notify(
                    "item/delta",
                    {
                        "threadId": event.session_id,
                        "turnId": event.turn_id,
                        "itemId": _tool_item_id(call_id),
                        "delta": {
                            "status": "started",
                            "callId": call_id,
                            "toolName": str(event.data.get("tool") or ""),
                            "arguments": copy.deepcopy(event.data.get("arguments") or {}),
                            "nested": bool(event.data.get("nested")),
                            "parentCallId": str(event.data.get("parent_call_id") or "") or None,
                        },
                    },
                )
                return

        terminal_kinds = {
            AgentEventKind.TURN_COMPLETED,
            AgentEventKind.TURN_FAILED,
            AgentEventKind.TURN_CANCELLED,
            AgentEventKind.TURN_INTERRUPTED,
            AgentEventKind.LIMIT_REACHED,
        }
        if event.kind in terminal_kinds:
            self._clear_turn_tool_streams(
                event,
                close_started=event.kind is not AgentEventKind.TURN_COMPLETED,
            )

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
