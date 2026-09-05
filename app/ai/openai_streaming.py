from __future__ import annotations

import threading
from collections.abc import Iterator
from typing import Any

from .contracts import ChatRequest, ModelUsage, StreamEvent, StreamEventKind
from .errors import AITransportError
from .openai_runtime import OpenAIChatBackend, _usage_from
from .provider_catalog import ProviderAdapter


class OpenAIStreamingChatBackend(OpenAIChatBackend):
    """OpenAI Chat backend with end-to-end stream completion metadata.

    Text and tool arguments are yielded as provider deltas. Token usage is
    requested with the OpenAI ``stream_options.include_usage`` contract and kept
    in thread-local completion metadata so the final canonical ModelResponse can
    still be committed atomically by the runtime.
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
            # Some nominally OpenAI-compatible endpoints have not implemented
            # stream_options yet. A rejection mentioning the unsupported field
            # happens before generation, so one retry without usage metadata is
            # safe and preserves streaming instead of silently disabling it.
            message = str(exc).casefold()
            unsupported = "stream_options" in message or "include_usage" in message
            if self.connection.adapter is not ProviderAdapter.OPENAI_COMPATIBLE or not unsupported:
                raise
            stream_kwargs.pop("stream_options", None)
            return self._create(stream_kwargs)

    def stream(self, request: ChatRequest) -> Iterator[StreamEvent]:
        if not isinstance(request, ChatRequest):
            raise TypeError("request must be ChatRequest")

        self._stream_local.metadata = {}
        stream = self._create_stream(self._request_kwargs(request))
        usage = ModelUsage()
        finish_reason = ""
        response_id = ""
        try:
            for chunk in stream:
                chunk_id = str(getattr(chunk, "id", "") or "").strip()
                if chunk_id:
                    response_id = chunk_id
                if getattr(chunk, "usage", None) is not None:
                    usage = _usage_from(chunk)

                choices = getattr(chunk, "choices", None) or ()
                if not choices:
                    # With include_usage the final OpenAI-compatible chunk has
                    # no choices and carries only token accounting.
                    continue
                choice = choices[0]
                delta = getattr(choice, "delta", None)
                if delta is not None:
                    # Deliberately expose only public assistant content. Provider
                    # reasoning_content / hidden reasoning fields are ignored.
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

            self._stream_local.metadata = {
                "usage": usage,
                "finish_reason": finish_reason,
                "response_id": response_id,
            }
            yield StreamEvent(
                kind=StreamEventKind.COMPLETED,
                finish_reason=finish_reason,
            )
        except AITransportError:
            raise
        except Exception as exc:
            raise AITransportError(
                f"AI stream failed via provider {self.connection.provider_id!r}: "
                f"{type(exc).__name__}: {exc}"
            ) from exc


__all__ = ["OpenAIStreamingChatBackend"]
