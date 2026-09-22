from __future__ import annotations

import threading
from collections.abc import Iterator
from typing import Any

from .contracts import ChatRequest, ModelUsage, StreamEvent, StreamEventKind
from .errors import AITransportError
from .execution_control import current_control, check_cancelled, note_progress, ModelCancelled
from .openai_runtime import OpenAIChatBackend, _retryable_provider_error, _usage_from
from .provider_catalog import ProviderAdapter


def _normalize_compatible_text_fragment(accumulated: str, fragment: str) -> tuple[str, str]:
    """Turn non-standard cumulative OpenAI-compatible snapshots into deltas.

    A few relays populate ``delta.content`` with the whole answer-so-far instead
    of the newly generated suffix. Everything above this transport layer follows
    the OpenAI contract and appends deltas, so forwarding those snapshots creates
    triangular repetition (for example ``403403...`` and repeated words) and can
    even tear Loom's inline-sticker control markers into visible prose.

    Only a strict extension of the text already assembled is treated as a
    snapshot. Equal/repeated fragments remain ordinary deltas, preserving valid
    outputs that intentionally repeat the same token or word.
    """
    if accumulated and len(fragment) > len(accumulated) and fragment.startswith(accumulated):
        return fragment[len(accumulated):], fragment
    return fragment, accumulated + fragment


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
            # Some OpenAI-compatible endpoints reject ``stream_options`` with an
            # ordinary provider error instead of a precise unsupported-field
            # response. Retry once without usage metadata before failing the
            # whole turn so MiniMax/relays are not taken down by fragile
            # compatibility around optional stream accounting.
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
        public_text = ""
        reasoning_text_so_far = ""
        compatible = self.connection.adapter is ProviderAdapter.OPENAI_COMPATIBLE
        try:
            for chunk in stream:
                check_cancelled()
                # Every chunk counts as progress, including the reasoning-only
                # ones below that never leave this loop as a StreamEvent. This
                # is what lets the executor tell a model that is thinking hard
                # from a connection that has died.
                note_progress()
                chunk_count += 1
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
                    # Keep provider reasoning separate from assistant text. It
                    # remains replayable transport state and, because this
                    # provider explicitly exposed it, also flows through Loom's
                    # visible reasoning stream.
                    reasoning = getattr(delta, "reasoning_content", None)
                    if reasoning is not None:
                        reasoning_text = str(reasoning)
                        if compatible:
                            reasoning_text, reasoning_text_so_far = _normalize_compatible_text_fragment(
                                reasoning_text_so_far,
                                reasoning_text,
                            )
                        else:
                            reasoning_text_so_far += reasoning_text
                        if reasoning_text:
                            reasoning_char_count += len(reasoning_text)
                            reasoning_parts.append(reasoning_text)
                            yield StreamEvent(
                                kind=StreamEventKind.REASONING_DELTA,
                                reasoning_delta=reasoning_text,
                            )
                    text = str(getattr(delta, "content", "") or "")
                    if text:
                        if compatible:
                            text, public_text = _normalize_compatible_text_fragment(public_text, text)
                        else:
                            public_text += text
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
                "reasoning": "".join(reasoning_parts),
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
