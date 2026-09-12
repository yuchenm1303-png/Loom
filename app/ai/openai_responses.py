from __future__ import annotations

import json
import threading
import time
from collections.abc import Iterator
from typing import Any

from .contracts import (
    AIMessage,
    ChatRequest,
    ImagePart,
    MessageRole,
    ModelResponse,
    ModelUsage,
    StreamEvent,
    StreamEventKind,
    StructuredOutputMode,
    StructuredRequest,
    TextPart,
    ToolCall,
)
from .errors import AIEmptyResponseError, AIResponseError, AITransportError
from .execution_control import ModelCancelled, check_cancelled, current_control
from .openai_runtime import (
    _PROVIDER_RETRY_DELAYS_SECONDS,
    _retryable_provider_error,
    _usage_from,
)
from .profiles import ModelProfile
from .provider_catalog import ProviderAdapter, ProviderConnection
from .reasoning import ReasoningKind
from .reasoning_catalog import resolve_reasoning_wire_value


def _plain_value(value: Any) -> Any:
    """Convert SDK/fake response values to JSON-safe Python values."""

    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(key): _plain_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain_value(item) for item in value]
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        try:
            return _plain_value(model_dump(mode="json", exclude_none=True))
        except TypeError:
            return _plain_value(model_dump())
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        return _plain_value(to_dict())
    raw = getattr(value, "__dict__", None)
    if isinstance(raw, dict):
        return {
            str(key): _plain_value(item)
            for key, item in raw.items()
            if not str(key).startswith("_")
        }
    return str(value)


def _input_content(message: AIMessage) -> str | list[dict[str, Any]]:
    if isinstance(message.content, str):
        return message.content
    output: list[dict[str, Any]] = []
    for part in message.content:
        if isinstance(part, TextPart):
            output.append({"type": "input_text", "text": part.text})
        elif isinstance(part, ImagePart):
            output.append(
                {
                    "type": "input_image",
                    "image_url": part.image_url,
                    "detail": part.detail,
                }
            )
        else:  # pragma: no cover - contracts reject unsupported content
            raise TypeError("unsupported Responses input content part")
    return output


def _tool_output(message: AIMessage) -> Any:
    content = _input_content(message)
    if isinstance(content, str):
        return content
    return content


def _fallback_assistant_items(message: AIMessage) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    if isinstance(message.content, str):
        if message.content:
            items.append({"role": "assistant", "content": message.content})
    else:
        # Loom model output is text today, but keep a defensive representation
        # for imported/legacy sessions rather than silently dropping content.
        items.append({"role": "assistant", "content": _input_content(message)})
    for call in message.tool_calls:
        items.append(
            {
                "type": "function_call",
                "call_id": call.call_id,
                "name": call.name,
                "arguments": json.dumps(
                    call.arguments,
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
            }
        )
    return items


def _response_input(messages: tuple[AIMessage, ...]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for message in messages:
        if message.role is MessageRole.TOOL:
            items.append(
                {
                    "type": "function_call_output",
                    "call_id": message.tool_call_id,
                    "output": _tool_output(message),
                }
            )
            continue
        if message.role is MessageRole.ASSISTANT:
            if message.provider_state:
                items.extend(dict(item) for item in message.provider_state)
            else:
                items.extend(_fallback_assistant_items(message))
            continue
        item: dict[str, Any] = {
            "role": message.role.value,
            "content": _input_content(message),
        }
        items.append(item)
    return items


def _response_tool(tool: Any) -> dict[str, Any]:
    return {
        "type": "function",
        "name": tool.name,
        "description": tool.description,
        "parameters": tool.input_schema,
    }


def _response_state(response: Any) -> tuple[dict[str, Any], ...]:
    output = getattr(response, "output", None) or ()
    state: list[dict[str, Any]] = []
    for item in output:
        value = _plain_value(item)
        if isinstance(value, dict):
            state.append(value)
    return tuple(state)


def _response_text(response: Any) -> str:
    convenience = getattr(response, "output_text", None)
    if isinstance(convenience, str) and convenience:
        return convenience
    parts: list[str] = []
    for item in getattr(response, "output", None) or ():
        if str(getattr(item, "type", "") or "") != "message":
            continue
        for content in getattr(item, "content", None) or ():
            if str(getattr(content, "type", "") or "") not in {"output_text", "text"}:
                continue
            text = str(getattr(content, "text", "") or "")
            if text:
                parts.append(text)
    return "".join(parts)


def _response_tool_calls(response: Any) -> tuple[ToolCall, ...]:
    calls: list[ToolCall] = []
    for item in getattr(response, "output", None) or ():
        if str(getattr(item, "type", "") or "") != "function_call":
            continue
        call_id = str(getattr(item, "call_id", "") or "").strip()
        name = str(getattr(item, "name", "") or "").strip()
        raw_arguments = str(getattr(item, "arguments", "") or "").strip()
        if not call_id or not name:
            raise AIResponseError("Responses function call is missing call_id or name")
        try:
            arguments = json.loads(raw_arguments) if raw_arguments else {}
        except json.JSONDecodeError as exc:
            raise AIResponseError(f"tool call {name!r} returned invalid JSON arguments") from exc
        if not isinstance(arguments, dict):
            raise AIResponseError(f"tool call {name!r} arguments must be a JSON object")
        calls.append(ToolCall(call_id=call_id, name=name, arguments=arguments))
    return tuple(calls)


def _incomplete_reason(response: Any) -> str:
    details = getattr(response, "incomplete_details", None)
    return str(getattr(details, "reason", "") or "").strip().casefold()


def _finish_reason(response: Any) -> str:
    status = str(getattr(response, "status", "") or "").strip().casefold()
    if status == "completed":
        return "completed"
    if status == "incomplete":
        reason = _incomplete_reason(response)
        if reason in {"max_output_tokens", "max_tokens", "length"}:
            return "length"
        return f"incomplete:{reason or 'unknown'}"
    return status or "completed"


def _reasoning_marker_count(state: tuple[dict[str, Any], ...]) -> int:
    total = 0
    for item in state:
        if str(item.get("type") or "") != "reasoning":
            continue
        encrypted = str(item.get("encrypted_content") or "")
        if encrypted:
            total += len(encrypted)
        else:
            total += 1
    return total


class OpenAIResponsesBackend:
    """Direct OpenAI backend using the Responses API with stateless replay.

    OpenAI-compatible relays deliberately do not use this backend. Loom requests
    ``store=False`` and retains returned output items locally so reasoning state,
    function calls, and assistant phase metadata can be replayed without relying
    on server-side response storage.
    """

    def __init__(
        self,
        *,
        connection: ProviderConnection,
        profile: ModelProfile,
        api_key: str,
        client: Any | None = None,
        request_timeout_seconds: float = 120.0,
    ) -> None:
        if connection.provider_id != profile.provider:
            raise ValueError("provider connection/profile mismatch")
        if connection.adapter is not ProviderAdapter.OPENAI:
            raise ValueError("OpenAI Responses backend requires the direct OpenAI adapter")
        api_key = str(api_key or "").strip()
        if not api_key:
            raise ValueError("api_key must not be empty")
        timeout = float(request_timeout_seconds)
        if not 10.0 <= timeout <= 600.0:
            raise ValueError("request_timeout_seconds must be within 10..600")
        if client is None:
            try:
                from openai import OpenAI
            except ImportError as exc:  # pragma: no cover
                raise RuntimeError("openai Python SDK is required") from exc
            client = OpenAI(
                api_key=api_key,
                timeout=timeout,
                max_retries=0,
            )
        responses = getattr(client, "responses", None)
        if responses is None or not callable(getattr(responses, "create", None)):
            raise RuntimeError(
                "configured OpenAI SDK/client does not expose responses.create; "
                "upgrade the OpenAI Python SDK"
            )
        self.connection = connection
        self.profile = profile
        self.client = client
        self.request_timeout_seconds = timeout
        self._stream_local = threading.local()

    @property
    def name(self) -> str:
        return "openai-responses"

    def _request_kwargs(self, request: ChatRequest) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "model": self.profile.model,
            "input": _response_input(request.messages),
            "store": False,
            "include": ["reasoning.encrypted_content"],
            "timeout": self.request_timeout_seconds,
        }
        if request.tools:
            kwargs["tools"] = [_response_tool(tool) for tool in request.tools]
            kwargs["tool_choice"] = request.tool_choice.value
        if request.temperature is not None:
            kwargs["temperature"] = request.temperature
        if request.max_output_tokens is not None:
            kwargs["max_output_tokens"] = request.max_output_tokens
        if request.reasoning is not None:
            if request.reasoning.kind is not ReasoningKind.OPENAI_EFFORT:
                raise ValueError("direct OpenAI Responses requests require OpenAI effort reasoning")
            kwargs["reasoning"] = {
                "effort": resolve_reasoning_wire_value(
                    model=self.profile.model,
                    adapter=self.connection.adapter.value,
                    base_url=self.connection.base_url,
                    reasoning=request.reasoning,
                )
            }
        return kwargs

    def _create(self, kwargs: dict[str, Any]) -> Any:
        attempts = len(_PROVIDER_RETRY_DELAYS_SECONDS) + 1
        last_error: BaseException | None = None
        for attempt in range(attempts):
            try:
                return self.client.responses.create(**kwargs)
            except Exception as exc:
                last_error = exc
                if attempt >= attempts - 1 or not _retryable_provider_error(exc):
                    break
                time.sleep(_PROVIDER_RETRY_DELAYS_SECONDS[attempt])
        assert last_error is not None
        raise AITransportError(
            f"AI request failed via provider {self.connection.provider_id!r}: "
            f"{type(last_error).__name__}: {last_error}",
            retryable=_retryable_provider_error(last_error),
        ) from last_error

    def _model_response(self, response: Any) -> ModelResponse:
        state = _response_state(response)
        text = _response_text(response)
        calls = _response_tool_calls(response)
        usage = _usage_from(response)
        finish = _finish_reason(response)
        response_id = str(getattr(response, "id", "") or "")
        if not text and not calls:
            raise AIEmptyResponseError(
                "AI response completed without public text or tool calls",
                finish_reason=finish,
                response_id=response_id,
                reasoning_char_count=_reasoning_marker_count(state),
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                total_tokens=usage.total_tokens,
            )
        return ModelResponse(
            text=text,
            tool_calls=calls,
            usage=usage,
            finish_reason=finish,
            response_id=response_id,
            provider_state=state,
        )

    def complete(self, request: ChatRequest) -> ModelResponse:
        if not isinstance(request, ChatRequest):
            raise TypeError("request must be ChatRequest")
        return self._model_response(self._create(self._request_kwargs(request)))

    def _structured_kwargs(self, request: StructuredRequest) -> dict[str, Any]:
        kwargs = self._request_kwargs(request.chat)
        mode = request.mode
        if mode is StructuredOutputMode.AUTO:
            mode = StructuredOutputMode.JSON_SCHEMA
        if mode is StructuredOutputMode.JSON_SCHEMA:
            kwargs["text"] = {
                "format": {
                    "type": "json_schema",
                    "name": request.schema_name,
                    "strict": True,
                    "schema": request.json_schema,
                }
            }
        elif mode is StructuredOutputMode.JSON_OBJECT:
            kwargs["text"] = {"format": {"type": "json_object"}}
        elif mode is StructuredOutputMode.PROMPT_ONLY:
            instruction = (
                "Return exactly one JSON object matching this JSON Schema. Do not emit markdown.\n"
                + json.dumps(request.json_schema, ensure_ascii=False, separators=(",", ":"))
            )
            kwargs["input"] = [
                {"role": "system", "content": instruction},
                *list(kwargs["input"]),
            ]
        else:  # pragma: no cover - enum guards this
            raise ValueError(f"unsupported structured output mode: {mode.value}")
        return kwargs

    def complete_structured(self, request: StructuredRequest) -> dict[str, Any]:
        if not isinstance(request, StructuredRequest):
            raise TypeError("request must be StructuredRequest")
        response = self._create(self._structured_kwargs(request))
        finish = _finish_reason(response)
        if finish not in {"", "completed"}:
            raise AIResponseError(f"structured AI response did not complete: {finish}")
        text = _response_text(response).strip()
        if not text:
            raise AIResponseError("structured AI response contained empty content")
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise AIResponseError("structured AI response was not valid JSON") from exc
        if not isinstance(payload, dict):
            raise AIResponseError("structured AI response top level must be a JSON object")
        return payload

    def last_stream_metadata(self) -> dict[str, Any]:
        value = getattr(self._stream_local, "metadata", None)
        return dict(value) if isinstance(value, dict) else {}

    def _terminal_stream_metadata(self, response: Any, *, chunk_count: int) -> dict[str, Any]:
        state = _response_state(response)
        return {
            "usage": _usage_from(response),
            "finish_reason": _finish_reason(response),
            "response_id": str(getattr(response, "id", "") or ""),
            "reasoning_char_count": _reasoning_marker_count(state),
            "reasoning_content": "",
            "provider_state": state,
            "chunk_count": int(chunk_count),
        }

    def stream(self, request: ChatRequest) -> Iterator[StreamEvent]:
        if not isinstance(request, ChatRequest):
            raise TypeError("request must be ChatRequest")

        self._stream_local.metadata = {}
        check_cancelled()
        kwargs = self._request_kwargs(request)
        kwargs["stream"] = True
        stream = self._create(kwargs)
        control = current_control.get()
        close = getattr(stream, "close", None)
        if control is not None and callable(close):
            control.on_cancel(close)

        chunk_count = 0
        terminal_seen = False
        call_meta: dict[int, tuple[str, str]] = {}
        argument_delta_seen: set[int] = set()
        try:
            for event in stream:
                check_cancelled()
                chunk_count += 1
                event_type = str(getattr(event, "type", "") or "")
                if event_type == "response.output_text.delta":
                    delta = str(getattr(event, "delta", "") or "")
                    if delta:
                        yield StreamEvent(kind=StreamEventKind.TEXT_DELTA, text_delta=delta)
                    continue
                if event_type == "response.output_item.added":
                    item = getattr(event, "item", None)
                    if str(getattr(item, "type", "") or "") == "function_call":
                        index = int(getattr(event, "output_index", 0) or 0)
                        call_id = str(getattr(item, "call_id", "") or "")
                        name = str(getattr(item, "name", "") or "")
                        call_meta[index] = (call_id, name)
                        yield StreamEvent(
                            kind=StreamEventKind.TOOL_CALL_DELTA,
                            tool_call_index=index,
                            tool_call_id=call_id,
                            tool_name=name,
                        )
                    continue
                if event_type == "response.function_call_arguments.delta":
                    index = int(getattr(event, "output_index", 0) or 0)
                    argument_delta_seen.add(index)
                    call_id, name = call_meta.get(index, ("", ""))
                    yield StreamEvent(
                        kind=StreamEventKind.TOOL_CALL_DELTA,
                        tool_call_index=index,
                        tool_call_id=call_id,
                        tool_name=name,
                        arguments_delta=str(getattr(event, "delta", "") or ""),
                    )
                    continue
                if event_type == "response.output_item.done":
                    item = getattr(event, "item", None)
                    if str(getattr(item, "type", "") or "") == "function_call":
                        index = int(getattr(event, "output_index", 0) or 0)
                        call_id = str(getattr(item, "call_id", "") or "")
                        name = str(getattr(item, "name", "") or "")
                        if index not in call_meta:
                            call_meta[index] = (call_id, name)
                            yield StreamEvent(
                                kind=StreamEventKind.TOOL_CALL_DELTA,
                                tool_call_index=index,
                                tool_call_id=call_id,
                                tool_name=name,
                            )
                        if index not in argument_delta_seen:
                            arguments = str(getattr(item, "arguments", "") or "")
                            if arguments:
                                yield StreamEvent(
                                    kind=StreamEventKind.TOOL_CALL_DELTA,
                                    tool_call_index=index,
                                    arguments_delta=arguments,
                                )
                    continue
                if event_type in {"response.completed", "response.incomplete"}:
                    response = getattr(event, "response", None)
                    if response is None:
                        raise AITransportError("Responses stream terminal event omitted response metadata")
                    metadata = self._terminal_stream_metadata(response, chunk_count=chunk_count)
                    self._stream_local.metadata = metadata
                    terminal_seen = True
                    yield StreamEvent(
                        kind=StreamEventKind.COMPLETED,
                        finish_reason=str(metadata["finish_reason"]),
                    )
                    continue
                if event_type == "response.failed":
                    response = getattr(event, "response", None)
                    error = getattr(response, "error", None)
                    message = str(getattr(error, "message", "") or "Responses stream failed")
                    raise AITransportError(message, retryable=False)

            if not terminal_seen:
                raise AITransportError("AI Responses stream ended without a terminal event")
        except (AITransportError, ModelCancelled):
            raise
        except Exception as exc:
            raise AITransportError(
                f"AI Responses stream failed via provider {self.connection.provider_id!r}: "
                f"{type(exc).__name__}: {exc}",
                retryable=_retryable_provider_error(exc),
            ) from exc
        finally:
            if callable(close):
                close()


__all__ = ["OpenAIResponsesBackend"]
