from __future__ import annotations

import threading
from collections.abc import Iterator
from typing import Any

from .contracts import ChatRequest, ModelUsage, StreamEvent, StreamEventKind
from .errors import AITransportError
from .execution_control import ModelCancelled, check_cancelled, current_control
from .openai_runtime import OpenAIChatBackend, _retryable_provider_error, _usage_from
from .provider_catalog import ProviderAdapter


class OpenAIStreamingChatBackend(OpenAIChatBackend):
    """OpenAI Chat backend with end-to-end stream completion metadata.

    Text and tool arguments are yielded as provider deltas. Provider-private
    reasoning continuity is retained only for models whose catalog explicitly
    requires replay and is never exposed as a public stream event.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._stream_local = threading.local()

    def last_stream_metadata(self) -> dict[str, Any]:
        value = getattr(self._stream_local, "metadata", None)
        if not isinstance(value, dict):
            return {}
        return dict(value)

    def _create_stream(self, kwargs: dict[str, Any]):
        stream_kwargs = dict(kwargs)
        stream_kwargs["stream"] = True
        stream_kwargs["stream_options"] = {"include_usage": True}
        try:
            return self._create(stream_kwargs)
        except AITransportError as exc:
            message = str(exc).casefold()
            compatible = self.connection.adapter is ProviderAdapter.OPENAI_COMPATIBLE
            retry_without_usage = any(
                marker in message
                for marker in (
                    "stream_options",
                    "include_usage",
                    "service temporarily unavailable",
                    "temporarily unavailable",
                    "503",
                    "502",
                    "504",
                )
            )
            if not compatible or not retry_without_usage:
                raise
            stream_kwargs.pop("stream_options", None)
            return self._create(stream_kwargs)

    def stream(self, request: ChatRequest) -> Iterator[StreamEvent]:
        if not isinstance(request, ChatRequest):
            raise TypeError("request must be ChatRequest")

        self._stream_local.metadata = {}
        check_cancelled()
        stream = self._create_stream(self._request_kwargs(request))
        control = current_control.get()
        close = getattr(stream, "close", None)
        if control is not None and callable(close):
            control.on_cancel(close)
        usage = ModelUsage()
        finish_reason = ""
        response_id = ""
        chunk_count = 0
        reasoning_char_count = 0
        reasoning_parts: list[str] = []
        retain_reasoning = self._replays_reasoning_content()
        try:
            for chunk in stream:
                check_cancelled()
                chunk_count += 1
                chunk_id = str(getattr(chunk, "id", "") or "").strip()
                if chunk_id:
                    response_id = chunk_id
                if getattr(chunk, "usage", None) is not None:
                    usage = _usage_from(chunk)

                choices = getattr(chunk, "choices", None) or ()
                if not choices:
                    continue
                choice = choices[0]
                delta = getattr(choice, "delta", None)
                if delta is not None:
                    raw_reasoning = getattr(delta, "reasoning_content", None)
                    if raw_reasoning is not None:
                        reasoning_piece = str(raw_reasoning)
                        reasoning_char_count += len(reasoning_piece)
                        if retain_reasoning and reasoning_piece:
                            reasoning_parts.append(reasoning_piece)
                    text = str(getattr(delta, "content", "") or "")
                    if text:
                        yield StreamEvent(kind=StreamEventKind.TEXT_DELTA, text_delta=text)
                    for raw_call in getattr(delta, "tool_calls", None) or ():
                        function = getattr(raw_call, "function", None)
                        raw_index = getattr(raw_call, "index", None)
                        yield StreamEvent(
                            kind=StreamEventKind.TOOL_CALL_DELTA,
                            tool_call_index=int(raw_index) if raw_index is not None else None,
                            tool_call_id=str(getattr(raw_call, "id", "") or ""),
                            tool_name=str(getattr(function, "name", "") or ""),
                            arguments_delta=str(getattr(function, "arguments", "") or ""),
                        )
                candidate_finish = str(getattr(choice, "finish_reason", "") or "")
                if candidate_finish:
                    finish_reason = candidate_finish

            if not finish_reason:
                raise AITransportError("AI stream ended without a completion marker")
            self._stream_local.metadata = {
                "usage": usage,
                "finish_reason": finish_reason,
                "response_id": response_id,
                "reasoning_char_count": reasoning_char_count,
                "reasoning_content": "".join(reasoning_parts),
                "chunk_count": chunk_count,
            }
            yield StreamEvent(
                kind=StreamEventKind.COMPLETED,
                finish_reason=finish_reason,
            )
        except (AITransportError, ModelCancelled):
            raise
        except Exception as exc:
            raise AITransportError(
                f"AI stream failed via provider {self.connection.provider_id!r}: "
                f"{type(exc).__name__}: {exc}",
                retryable=_retryable_provider_error(exc),
            ) from exc
        finally:
            if callable(close):
                close()


__all__ = ["OpenAIStreamingChatBackend"]
