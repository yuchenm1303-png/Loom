from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

from .contracts import ChatRequest, ModelResponse, ModelUsage, StreamEvent, StreamEventKind, ToolCall
from .errors import AIResponseError
from .platform import AIPlatform


class ProviderStreamEventKind(str, Enum):
    TEXT_DELTA = "text_delta"
    TOOL_CALL_DELTA = "tool_call_delta"
    COMPLETED = "completed"


@dataclass(frozen=True, slots=True)
class ProviderStreamEvent:
    profile_id: str
    kind: ProviderStreamEventKind
    text_delta: str = ""
    tool_call_index: int | None = None
    tool_call_id: str = ""
    tool_name: str = ""
    arguments_delta: str = ""
    finish_reason: str = ""
    response_id: str = ""
    usage: ModelUsage = field(default_factory=ModelUsage)


ProviderStreamListener = Callable[[ProviderStreamEvent], None]


@dataclass(slots=True)
class _ToolCallBuffer:
    call_id: str = ""
    name: str = ""
    argument_parts: list[str] = field(default_factory=list)


class _StreamAccumulator:
    def __init__(self) -> None:
        self.text_parts: list[str] = []
        self.tool_calls: dict[int, _ToolCallBuffer] = {}
        self.call_indexes: dict[str, int] = {}
        self.last_tool_index: int | None = None
        self.finish_reason = ""

    def consume(self, event: StreamEvent) -> None:
        if event.kind is StreamEventKind.TEXT_DELTA:
            if event.text_delta:
                self.text_parts.append(event.text_delta)
            return
        if event.kind is StreamEventKind.TOOL_CALL_DELTA:
            self._consume_tool_delta(event)
            return
        if event.kind is StreamEventKind.COMPLETED:
            if event.finish_reason:
                self.finish_reason = event.finish_reason
            return
        raise AIResponseError(f"unsupported stream event kind: {event.kind!r}")

    def _consume_tool_delta(self, event: StreamEvent) -> None:
        index = event.tool_call_index
        if index is None:
            if event.tool_call_id and event.tool_call_id in self.call_indexes:
                index = self.call_indexes[event.tool_call_id]
            elif event.tool_call_id:
                index = len(self.tool_calls)
            elif self.last_tool_index is not None:
                index = self.last_tool_index
            else:
                index = len(self.tool_calls)
        index = int(index)
        if index < 0:
            raise AIResponseError("tool call stream index must not be negative")

        buffer = self.tool_calls.setdefault(index, _ToolCallBuffer())
        self.last_tool_index = index
        if event.tool_call_id:
            if buffer.call_id and buffer.call_id != event.tool_call_id:
                raise AIResponseError("tool call stream changed call id for the same index")
            buffer.call_id = event.tool_call_id
            self.call_indexes[event.tool_call_id] = index
        if event.tool_name:
            if not buffer.name:
                buffer.name = event.tool_name
            elif event.tool_name != buffer.name:
                buffer.name += event.tool_name
        if event.arguments_delta:
            buffer.argument_parts.append(event.arguments_delta)

    def finalize(
        self,
        *,
        usage: ModelUsage | None = None,
        response_id: str = "",
        finish_reason: str = "",
    ) -> ModelResponse:
        calls: list[ToolCall] = []
        for index in sorted(self.tool_calls):
            buffer = self.tool_calls[index]
            call_id = buffer.call_id.strip()
            name = buffer.name.strip()
            if not call_id or not name:
                raise AIResponseError("streamed tool call is missing id or function name")
            raw_arguments = "".join(buffer.argument_parts).strip()
            try:
                arguments = json.loads(raw_arguments) if raw_arguments else {}
            except json.JSONDecodeError as exc:
                raise AIResponseError(
                    f"tool call {name!r} returned invalid streamed JSON arguments"
                ) from exc
            if not isinstance(arguments, dict):
                raise AIResponseError(f"tool call {name!r} arguments must be a JSON object")
            calls.append(ToolCall(call_id=call_id, name=name, arguments=arguments))

        text = "".join(self.text_parts)
        if not text and not calls:
            raise AIResponseError("AI stream contained neither text nor tool calls")
        return ModelResponse(
            text=text,
            tool_calls=tuple(calls),
            usage=usage or ModelUsage(),
            finish_reason=str(finish_reason or self.finish_reason or ""),
            response_id=str(response_id or ""),
        )


class StreamingAIPlatform(AIPlatform):
    """AIPlatform variant that normalizes provider streams into one final response.

    The runtime still receives one atomic ``ModelResponse`` for canonical history,
    while interested clients can subscribe to transient provider-normalized deltas.
    Streaming is opt-in so detached/legacy platform users keep the old completion
    semantics until a Runtime enables it.
    """

    def __init__(self, registry=None, *, prefer_streaming: bool = False) -> None:
        super().__init__(registry)
        self.prefer_streaming = bool(prefer_streaming)
        self._stream_listeners: list[ProviderStreamListener] = []
        self._stream_listener_guard = threading.RLock()

    def enable_streaming(self) -> None:
        self.prefer_streaming = True

    def subscribe_stream(self, listener: ProviderStreamListener) -> None:
        if not callable(listener):
            raise TypeError("provider stream listener must be callable")
        with self._stream_listener_guard:
            self._stream_listeners.append(listener)

    def _publish(self, event: ProviderStreamEvent) -> None:
        with self._stream_listener_guard:
            listeners = tuple(self._stream_listeners)
        for listener in listeners:
            try:
                listener(event)
            except Exception:
                continue

    def execute_chat(self, profile_id: str, request: ChatRequest) -> ModelResponse:
        if not self.prefer_streaming:
            return super().execute_chat(profile_id, request)

        self._require_chat_capabilities(profile_id, request, streaming=True)
        profile, backend = self._backend_for(profile_id)
        stream_method = getattr(backend, "stream", None)
        if not callable(stream_method):
            return super().execute_chat(profile_id, request)

        accumulator = _StreamAccumulator()
        for raw_event in stream_method(request):
            if not isinstance(raw_event, StreamEvent):
                raise TypeError("streaming model backend must yield StreamEvent values")
            accumulator.consume(raw_event)
            if raw_event.kind is StreamEventKind.TEXT_DELTA and raw_event.text_delta:
                self._publish(
                    ProviderStreamEvent(
                        profile_id=profile.profile_id,
                        kind=ProviderStreamEventKind.TEXT_DELTA,
                        text_delta=raw_event.text_delta,
                    )
                )
            elif raw_event.kind is StreamEventKind.TOOL_CALL_DELTA:
                self._publish(
                    ProviderStreamEvent(
                        profile_id=profile.profile_id,
                        kind=ProviderStreamEventKind.TOOL_CALL_DELTA,
                        tool_call_index=raw_event.tool_call_index,
                        tool_call_id=raw_event.tool_call_id,
                        tool_name=raw_event.tool_name,
                        arguments_delta=raw_event.arguments_delta,
                    )
                )

        metadata: dict[str, Any] = {}
        metadata_getter = getattr(backend, "last_stream_metadata", None)
        if callable(metadata_getter):
            candidate = metadata_getter()
            if isinstance(candidate, dict):
                metadata = candidate
        usage = metadata.get("usage")
        if not isinstance(usage, ModelUsage):
            usage = ModelUsage()
        result = accumulator.finalize(
            usage=usage,
            response_id=str(metadata.get("response_id") or ""),
            finish_reason=str(metadata.get("finish_reason") or ""),
        )
        self._publish(
            ProviderStreamEvent(
                profile_id=profile.profile_id,
                kind=ProviderStreamEventKind.COMPLETED,
                finish_reason=result.finish_reason,
                response_id=result.response_id,
                usage=result.usage,
            )
        )
        return result


__all__ = [
    "ProviderStreamEvent",
    "ProviderStreamEventKind",
    "ProviderStreamListener",
    "StreamingAIPlatform",
]
