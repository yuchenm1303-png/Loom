from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

from app.ai.streaming_platform import ProviderStreamEvent, ProviderStreamEventKind

from .code_mode_runtime import CodeModeRuntime
from .contracts import AgentEvent, AgentEventKind, AgentSession
from .storage import utc_now


class AgentStreamEventKind(str, Enum):
    ASSISTANT_TEXT_DELTA = "assistant_text_delta"
    TOOL_CALL_ARGUMENT_DELTA = "tool_call_argument_delta"
    MODEL_STREAM_COMPLETED = "model_stream_completed"


@dataclass(frozen=True, slots=True)
class AgentStreamEvent:
    session_id: str
    turn_id: str
    step_id: str
    kind: AgentStreamEventKind
    created_at: str
    data: dict[str, Any] = field(default_factory=dict)


AgentStreamListener = Callable[[AgentStreamEvent], None]


@dataclass(frozen=True, slots=True)
class _ModelStreamContext:
    session_id: str
    turn_id: str
    step_id: str
    profile_id: str


class StreamingAgentRuntime(CodeModeRuntime):
    """Runtime v2 top layer that correlates provider deltas with active model steps.

    Provider chunks are transient observable state. They are delivered through a
    separate event bus and are never appended to ``events.jsonl`` or canonical
    thread history. The normal MODEL_RESPONSE event remains the atomic durable
    commit boundary, so disconnecting a UI cannot corrupt a Turn.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self._stream_listener_guard = threading.RLock()
        self._stream_listeners: list[AgentStreamListener] = []
        self._stream_context = threading.local()
        self._provider_streaming_enabled = False
        super().__init__(*args, **kwargs)

        enable = getattr(self.platform, "enable_streaming", None)
        subscribe = getattr(self.platform, "subscribe_stream", None)
        if callable(enable) and callable(subscribe):
            enable()
            subscribe(self._on_provider_stream)
            self._provider_streaming_enabled = True

    @property
    def provider_streaming_enabled(self) -> bool:
        return self._provider_streaming_enabled

    def subscribe_stream(self, listener: AgentStreamListener) -> None:
        if not callable(listener):
            raise TypeError("runtime stream listener must be callable")
        with self._stream_listener_guard:
            self._stream_listeners.append(listener)

    def _emit_stream(
        self,
        context: _ModelStreamContext,
        kind: AgentStreamEventKind,
        data: dict[str, Any],
    ) -> None:
        json.dumps(data, ensure_ascii=False)
        event = AgentStreamEvent(
            session_id=context.session_id,
            turn_id=context.turn_id,
            step_id=context.step_id,
            kind=kind,
            created_at=utc_now(),
            data=dict(data),
        )
        with self._stream_listener_guard:
            listeners = tuple(self._stream_listeners)
        for listener in listeners:
            try:
                listener(event)
            except Exception:
                continue

    def _on_provider_stream(self, event: ProviderStreamEvent) -> None:
        context = getattr(self._stream_context, "current", None)
        if not isinstance(context, _ModelStreamContext):
            # Detached model tasks such as memory extraction/compaction may use
            # the same platform but are not part of an active Agent model step.
            return
        if context.profile_id != event.profile_id:
            return

        if event.kind is ProviderStreamEventKind.TEXT_DELTA:
            if event.text_delta:
                self._emit_stream(
                    context,
                    AgentStreamEventKind.ASSISTANT_TEXT_DELTA,
                    {"delta": event.text_delta, "profile_id": event.profile_id},
                )
            return
        if event.kind is ProviderStreamEventKind.TOOL_CALL_DELTA:
            self._emit_stream(
                context,
                AgentStreamEventKind.TOOL_CALL_ARGUMENT_DELTA,
                {
                    "profile_id": event.profile_id,
                    "index": event.tool_call_index,
                    "call_id": event.tool_call_id,
                    "tool": event.tool_name,
                    "arguments_delta": event.arguments_delta,
                },
            )
            return
        if event.kind is ProviderStreamEventKind.COMPLETED:
            self._emit_stream(
                context,
                AgentStreamEventKind.MODEL_STREAM_COMPLETED,
                {
                    "profile_id": event.profile_id,
                    "finish_reason": event.finish_reason,
                    "response_id": event.response_id,
                    "usage": {
                        "input_tokens": event.usage.input_tokens,
                        "output_tokens": event.usage.output_tokens,
                        "total_tokens": event.usage.total_tokens,
                    },
                },
            )

    def _record(
        self,
        session: AgentSession,
        kind: AgentEventKind,
        *,
        data: dict[str, object],
    ) -> AgentEvent:
        event = super()._record(session, kind, data=data)
        if kind is AgentEventKind.MODEL_REQUESTED:
            step_id = str(data.get("step_id") or "").strip()
            self._stream_context.current = _ModelStreamContext(
                session_id=session.session_id,
                turn_id=session.current_turn_id,
                step_id=step_id,
                profile_id=session.profile_id,
            )
        elif kind in {
            AgentEventKind.MODEL_RESPONSE,
            AgentEventKind.TURN_COMPLETED,
            AgentEventKind.TURN_FAILED,
            AgentEventKind.TURN_CANCELLED,
            AgentEventKind.TURN_INTERRUPTED,
            AgentEventKind.LIMIT_REACHED,
        }:
            self._stream_context.current = None
        return event

    def close(self) -> None:
        with self._stream_listener_guard:
            self._stream_listeners.clear()
        self._stream_context.current = None
        super().close()


__all__ = [
    "AgentStreamEvent",
    "AgentStreamEventKind",
    "AgentStreamListener",
    "StreamingAgentRuntime",
]
