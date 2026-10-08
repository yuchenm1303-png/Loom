from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from collections.abc import Iterator
from typing import Any
from types import SimpleNamespace

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
    ToolChoice,
)
from .errors import AIResponseError, AITransportError, AIQuotaExceeded
from .openai_runtime import _provider_status_code, _retryable_provider_error, _quota_exhausted
from .responses_protocol import ResponsesToolDecoder, ResponsesTextDecoder, responses_finish_reason
from .sse import json_sse_events
from .tool_protocol import parse_tool_call, validate_unique_calls
from .execution_control import ModelCancelled, check_cancelled, current_control, note_progress, note_chunk
from .openai_streaming import OpenAIStreamingChatBackend
from .profiles import ModelProfile
from .provider_catalog import ProviderAdapter, ProviderConnection
from .reasoning import ReasoningKind


OPENCODE_GO_BASE_URL = "https://opencode.ai/zen/go/v1"
OPENCODE_GO_USER_AGENT = "Loom/0.1 (coding-agent)"


def _transport_error(message: str, exc: BaseException) -> AITransportError:
    if _quota_exhausted(exc):
        return AIQuotaExceeded(status_code=_provider_status_code(exc))
    return AITransportError(message, retryable=_retryable_provider_error(exc),
                            status_code=_provider_status_code(exc))


def _stream_failure(error: Any) -> AITransportError:
    def field(name: str) -> str:
        return str((error.get(name) if isinstance(error, dict) else getattr(error, name, "")) or "")
    code = field("code") or field("type")
    body = error if isinstance(error, dict) else {"code": code, "message": field("message")}
    if _quota_exhausted(SimpleNamespace(body=body)):
        return AIQuotaExceeded()
    return AITransportError(f"Provider stream error: {code or 'unspecified'}: {field('message')}",
        retryable=code in {"server_error", "internal_error", "api_error", "overloaded_error", "rate_limit_error"})

_RESPONSES_MODELS = frozenset(
    {
        "gpt-5.6-luna",
        "grok-4.7",
        "grok-4.6",
        "grok-4.5",
        "muse-spark-1.3-contributor",
        "muse-spark-1.2-contributor",
    }
)
_MESSAGES_PREFIXES = ("minimax-", "qwen")

_QWEN_BUDGET_PRESETS = {
    "qwen3.5-plus": {"high": 32_768, "max": 65_535},
    "qwen3.6-plus": {"high": 32_768, "max": 65_535},
    "qwen3.7-plus": {"high": 32_768, "max": 65_535},
    "qwen3.7-max": {"high": 32_768, "max": 65_535},
}


def _messages_reasoning_payload(model: str, reasoning) -> dict[str, Any]:
    if reasoning is None:
        return {}
    key = str(model or "").strip().casefold()

    if key == "minimax-m3" and reasoning.kind is ReasoningKind.MINIMAX_THINKING:
        if reasoning.value == "disabled":
            return {"thinking": {"type": "disabled"}}
        if reasoning.value == "adaptive":
            return {"thinking": {"type": "adaptive"}}

    if key in {"qwen3.8-flash", "qwen3.8-max"} and reasoning.kind is ReasoningKind.OPENAI_EFFORT:
        if reasoning.value == "none":
            return {"thinking": {"type": "disabled"}}
        return {
            "thinking": {"type": "enabled"},
            "output_config": {"effort": reasoning.value},
        }

    if key in _QWEN_BUDGET_PRESETS and reasoning.kind is ReasoningKind.THINKING_BUDGET:
        if reasoning.value == "none":
            return {"thinking": {"type": "disabled"}}
        budget = _QWEN_BUDGET_PRESETS[key].get(reasoning.value)
        if budget is not None:
            return {"thinking": {"type": "enabled", "budget_tokens": budget}}

    raise AITransportError(
        f"OpenCode Go Messages cannot encode reasoning {reasoning.kind.value}:{reasoning.value} "
        f"for model {model!r}"
    )


def opencode_go_protocol(model: str) -> str:
    value = str(model or "").strip().casefold()
    if value in _RESPONSES_MODELS or value.startswith("muse-spark-"):
        return "responses"
    if value.startswith(_MESSAGES_PREFIXES):
        return "messages"
    return "chat-completions"


def _session_headers(request: ChatRequest) -> dict[str, str]:
    headers = {"User-Agent": OPENCODE_GO_USER_AGENT}
    if request.session_id:
        headers["x-opencode-session"] = request.session_id
    return headers


def _text_content(message: AIMessage) -> str:
    if isinstance(message.content, str):
        return message.content
    return "\n".join(
        part.text for part in message.content if isinstance(part, TextPart)
    )


def _field(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value, dict):
        return value.get(name, default)
    return getattr(value, name, default)


def _responses_visible_reasoning(response: Any) -> str:
    """Return only provider-exposed Responses reasoning summaries.

    OpenAI does not expose hidden raw reasoning tokens. The supported public
    surface is the reasoning item's summary; other providers using the same
    protocol may omit it, in which case Loom surfaces nothing.
    """
    parts: list[str] = []
    for item in _field(response, "output", ()) or ():
        if str(_field(item, "type", "") or "") != "reasoning":
            continue
        for summary in _field(item, "summary", ()) or ():
            text = str(_field(summary, "text", "") or "")
            if text:
                parts.append(text)
    return "\n\n".join(part for part in parts if part).strip()


def _usage(input_tokens: int = 0, output_tokens: int = 0, cached_input_tokens: int = 0,
           cache_creation_input_tokens: int = 0) -> ModelUsage:
    # Anthropic input_tokens excludes cache reads and cache creation. Responses
    # input_tokens already includes cache reads, so its callers pass those only
    # as the cached subset through _responses_usage below.
    cached = max(0, int(cached_input_tokens or 0))
    inputs = max(0, int(input_tokens or 0)) + cached + max(0, int(cache_creation_input_tokens or 0))
    return ModelUsage(
        input_tokens=inputs,
        output_tokens=max(0, int(output_tokens or 0)),
        total_tokens=inputs + max(0, int(output_tokens or 0)),
        cached_input_tokens=cached,
    )


def _responses_usage(raw: Any) -> ModelUsage:
    if raw is None:
        return ModelUsage()
    details = getattr(raw, "input_tokens_details", None)
    inputs = max(0, int(getattr(raw, "input_tokens", 0) or 0))
    outputs = max(0, int(getattr(raw, "output_tokens", 0) or 0))
    return ModelUsage(inputs, outputs,
                      int(getattr(raw, "total_tokens", 0) or inputs + outputs),
                      max(0, int(getattr(details, "cached_tokens", 0) or 0)))


class _OpenCodeGoChatBackend(OpenAIStreamingChatBackend):
    """OpenAI-compatible chat with OpenCode coding-agent identity headers."""

    def _request_kwargs(self, request: ChatRequest) -> dict[str, Any]:
        kwargs = super()._request_kwargs(request)
        kwargs["extra_headers"] = _session_headers(request)
        return kwargs


class _OpenCodeGoResponsesBackend:
    def __init__(
        self,
        *,
        profile: ModelProfile,
        api_key: str,
        request_timeout_seconds: float,
    ) -> None:
        from openai import OpenAI

        self.profile = profile
        self.client = OpenAI(
            api_key=api_key,
            base_url=OPENCODE_GO_BASE_URL,
            timeout=float(request_timeout_seconds),
            max_retries=0,
        )
        self._stream_local = threading.local()

    @property
    def name(self) -> str:
        return "opencode-go-responses"

    def last_stream_metadata(self) -> dict[str, Any]:
        value = getattr(self._stream_local, "metadata", None)
        return dict(value) if isinstance(value, dict) else {}

    def _input(self, request: ChatRequest) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for message in request.messages:
            if message.role is MessageRole.TOOL:
                items.append(
                    {
                        "type": "function_call_output",
                        "call_id": message.tool_call_id,
                        "output": _text_content(message),
                    }
                )
                continue

            content: Any = message.content
            if not isinstance(content, str):
                parts: list[dict[str, Any]] = []
                for part in content:
                    if isinstance(part, TextPart):
                        parts.append(
                            {
                                "type": (
                                    "output_text"
                                    if message.role is MessageRole.ASSISTANT
                                    else "input_text"
                                ),
                                "text": part.text,
                            }
                        )
                    elif isinstance(part, ImagePart):
                        parts.append({"type": "input_image", "image_url": part.image_url})
                content = parts

            if content or not message.tool_calls:
                item = {"role": message.role.value, "content": content}
                if message.role is MessageRole.ASSISTANT and message.phase is not None:
                    item["phase"] = message.phase
                items.append(item)
            for call in message.tool_calls:
                items.append(
                    {
                        "type": "function_call",
                        "call_id": call.call_id,
                        "name": call.name,
                        "arguments": json.dumps(call.arguments, ensure_ascii=False, separators=(",", ":")),
                    }
                )
        return items

    def _kwargs(self, request: ChatRequest) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "model": self.profile.model,
            "input": self._input(request),
            "extra_headers": _session_headers(request),
        }
        if request.tools:
            kwargs["tools"] = [
                {
                    "type": "function",
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.input_schema,
                }
                for tool in request.tools
            ]
            kwargs["tool_choice"] = request.tool_choice.value
        elif request.tool_choice is ToolChoice.NONE:
            kwargs["tool_choice"] = "none"
        if request.temperature is not None:
            kwargs["temperature"] = request.temperature
        if request.max_output_tokens is not None:
            kwargs["max_output_tokens"] = request.max_output_tokens
        if request.reasoning is not None and request.reasoning.kind is ReasoningKind.OPENAI_EFFORT:
            reasoning_payload: dict[str, Any] = {"effort": request.reasoning.value}
            # OpenAI reasoning models expose summaries only when explicitly
            # requested. Keep this scoped to GPT Responses models; third-party
            # Responses providers may not implement the summary option.
            if (
                str(self.profile.model or "").strip().casefold().startswith("gpt-")
                and request.reasoning.value != "none"
            ):
                reasoning_payload["summary"] = "auto"
            kwargs["reasoning"] = reasoning_payload
        return kwargs

    @staticmethod
    def _response_calls(response: Any) -> tuple[ToolCall, ...]:
        calls: list[ToolCall] = []
        for item in getattr(response, "output", None) or ():
            if str(getattr(item, "type", "") or "") != "function_call":
                continue
            calls.append(parse_tool_call(
                str(getattr(item, "call_id", "") or ""),
                str(getattr(item, "name", "") or ""),
                str(getattr(item, "arguments", "") or ""),
                finish_reason=responses_finish_reason(response),
            ))
        return validate_unique_calls(calls)

    def complete(self, request: ChatRequest) -> ModelResponse:
        check_cancelled()
        try:
            response = self.client.responses.create(**self._kwargs(request))
        except Exception as exc:
            raise _transport_error(
                f"OpenCode Go Responses request failed: {type(exc).__name__}: {exc}", exc
            ) from exc
        usage = getattr(response, "usage", None)
        if getattr(response, "status", "") == "failed":
            raise _stream_failure(getattr(response, "error", None))
        return ModelResponse(
            text="".join(
                str(getattr(part, "refusal" if getattr(part, "type", "") == "refusal" else "text", "") or "")
                for item in (getattr(response, "output", ()) or ())
                if getattr(item, "type", "") == "message"
                for part in (getattr(item, "content", ()) or ())
                if getattr(part, "type", "") in {"output_text", "refusal"}
            ) or str(getattr(response, "output_text", "") or ""),
            tool_calls=self._response_calls(response),
            usage=_responses_usage(usage),
            finish_reason=responses_finish_reason(response),
            phase=next((getattr(item, "phase", None) for item in reversed(getattr(response, "output", ()) or ())
                        if getattr(item, "type", "") == "message"), None),
            end_turn=getattr(response, "end_turn", None),
            response_id=str(getattr(response, "id", "") or ""),
            visible_reasoning=_responses_visible_reasoning(response),
        )

    def stream(self, request: ChatRequest) -> Iterator[StreamEvent]:
        self._stream_local.metadata = {}
        check_cancelled()
        kwargs = self._kwargs(request)
        kwargs["stream"] = True
        try:
            stream = self.client.responses.create(**kwargs)
        except Exception as exc:
            raise _transport_error(
                f"OpenCode Go Responses stream failed: {type(exc).__name__}: {exc}", exc
            ) from exc

        control = current_control.get()
        close = getattr(stream, "close", None)
        if control is not None and callable(close):
            control.on_cancel(close)

        calls = ResponsesToolDecoder()
        texts = ResponsesTextDecoder()
        usage = ModelUsage()
        response_id = ""
        finish_reason = ""
        native_phase = None
        native_end_turn = None
        chunks = 0
        try:
            for event in stream:
                check_cancelled()
                note_chunk()
                chunks += 1
                event_type = str(getattr(event, "type", "") or "")
                if event_type.endswith(".delta") and getattr(event, "delta", None):
                    note_progress(tool_fragment=event_type == "response.function_call_arguments.delta")
                if event_type in {"response.output_text.delta", "response.output_text.done",
                                  "response.refusal.delta", "response.refusal.done"}:
                    snapshot = event_type.endswith(".done")
                    field = ("refusal" if "refusal" in event_type else "text") if snapshot else "delta"
                    yield from texts.text(int(getattr(event, "output_index", 0)),
                        int(getattr(event, "content_index", 0)),
                        str(getattr(event, field, "") or ""), snapshot=snapshot)
                    continue
                if event_type == "response.reasoning_summary_text.delta":
                    delta = str(getattr(event, "delta", "") or "")
                    if delta:
                        yield StreamEvent(
                            kind=StreamEventKind.REASONING_DELTA,
                            reasoning_delta=delta,
                        )
                    continue
                if event_type in {"response.output_item.added", "response.output_item.done"}:
                    item = getattr(event, "item", None)
                    if str(getattr(item, "type", "") or "") == "function_call":
                        yield from calls.item(item, getattr(event, "output_index", None),
                                              snapshot=event_type.endswith(".done"))
                    continue
                if event_type in {"response.function_call_arguments.delta", "response.function_call_arguments.done"}:
                    snapshot = event_type.endswith(".done")
                    yield from calls.arguments(
                        index=getattr(event, "output_index", None),
                        item_id=str(getattr(event, "item_id", "") or ""),
                        call_id=str(getattr(event, "call_id", "") or ""),
                        value=str(getattr(event, "arguments" if snapshot else "delta", "") or ""),
                        snapshot=snapshot,
                    )
                    continue
                if event_type in {"response.completed", "response.incomplete"}:
                    response = getattr(event, "response", None)
                    response_id = str(getattr(response, "id", "") or "")
                    native_phase = next((getattr(item, "phase", None) for item in reversed(getattr(response, "output", ()) or ())
                                         if getattr(item, "type", "") == "message"), None)
                    native_end_turn = getattr(response, "end_turn", None)
                    finish_reason = responses_finish_reason(response)
                    raw_usage = getattr(response, "usage", None)
                    if raw_usage is not None:
                        usage = _responses_usage(raw_usage)
                    for index, item in enumerate(getattr(response, "output", ()) or ()):
                        if getattr(item, "type", "") == "function_call":
                            yield from calls.item(item, index, snapshot=True)
                        elif getattr(item, "type", "") == "message":
                            for content_index, part in enumerate(getattr(item, "content", ()) or ()):
                                kind = getattr(part, "type", "")
                                if kind in {"output_text", "refusal"}:
                                    yield from texts.text(index, content_index,
                                        str(getattr(part, "text" if kind == "output_text" else "refusal", "") or ""),
                                        snapshot=True)
                    break
                if event_type in {"response.failed", "error"}:
                    failure = getattr(getattr(event, "response", None), "error", None)
                    raise _stream_failure(failure or getattr(event, "error", None) or event)
            if not finish_reason:
                raise AITransportError("OpenCode Go Responses stream ended without completion")
            self._stream_local.metadata = {
                "usage": usage,
                "finish_reason": finish_reason,
                "response_id": response_id,
                "chunk_count": chunks,
            }
            yield StreamEvent(kind=StreamEventKind.COMPLETED, finish_reason=finish_reason,
                              phase=native_phase, end_turn=native_end_turn)
        except (AITransportError, AIResponseError, ModelCancelled):
            raise
        except Exception as exc:
            raise _transport_error(
                f"OpenCode Go Responses stream failed: {type(exc).__name__}: {exc}", exc
            ) from exc
        finally:
            if callable(close):
                close()


class _OpenCodeGoMessagesBackend:
    def __init__(
        self,
        *,
        profile: ModelProfile,
        api_key: str,
        request_timeout_seconds: float,
    ) -> None:
        self.profile = profile
        self.api_key = api_key
        self.timeout = float(request_timeout_seconds)
        self._stream_local = threading.local()

    @property
    def name(self) -> str:
        return "opencode-go-messages"

    def last_stream_metadata(self) -> dict[str, Any]:
        value = getattr(self._stream_local, "metadata", None)
        return dict(value) if isinstance(value, dict) else {}

    def _headers(self, request: ChatRequest) -> dict[str, str]:
        return {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Authorization": "Bearer " + self.api_key,
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            **_session_headers(request),
        }

    def _messages(self, request: ChatRequest) -> tuple[str, list[dict[str, Any]]]:
        system_parts: list[str] = []
        messages: list[dict[str, Any]] = []
        for message in request.messages:
            if message.role is MessageRole.SYSTEM:
                system_parts.append(_text_content(message))
                continue
            if message.role is MessageRole.TOOL:
                messages.append(
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": message.tool_call_id,
                                "content": _text_content(message),
                            }
                        ],
                    }
                )
                continue

            blocks: list[dict[str, Any]] = []
            if isinstance(message.content, str):
                if message.content:
                    blocks.append({"type": "text", "text": message.content})
            else:
                for part in message.content:
                    if isinstance(part, TextPart):
                        blocks.append({"type": "text", "text": part.text})
                    elif isinstance(part, ImagePart):
                        blocks.append(
                            {
                                "type": "image",
                                "source": {"type": "url", "url": part.image_url},
                            }
                        )
            for call in message.tool_calls:
                blocks.append(
                    {
                        "type": "tool_use",
                        "id": call.call_id,
                        "name": call.name,
                        "input": call.arguments,
                    }
                )
            messages.append({"role": message.role.value, "content": blocks or ""})
        return "\n\n".join(part for part in system_parts if part), messages

    def _payload(self, request: ChatRequest, *, stream: bool) -> dict[str, Any]:
        system, messages = self._messages(request)
        payload: dict[str, Any] = {
            "model": self.profile.model,
            "messages": messages,
            "max_tokens": int(request.max_output_tokens or 8192),
            "stream": bool(stream),
        }
        if system:
            payload["system"] = system
        if request.tools and request.tool_choice is not ToolChoice.NONE:
            payload["tools"] = [
                {
                    "name": tool.name,
                    "description": tool.description,
                    "input_schema": tool.input_schema,
                }
                for tool in request.tools
            ]
            if request.tool_choice is ToolChoice.REQUIRED:
                payload["tool_choice"] = {"type": "any"}
            elif request.tool_choice is ToolChoice.AUTO:
                payload["tool_choice"] = {"type": "auto"}
        if request.temperature is not None:
            payload["temperature"] = request.temperature
        payload.update(_messages_reasoning_payload(self.profile.model, request.reasoning))
        return payload

    def _open(self, request: ChatRequest, *, stream: bool):
        raw = json.dumps(self._payload(request, stream=stream), ensure_ascii=False).encode("utf-8")
        http_request = urllib.request.Request(
            OPENCODE_GO_BASE_URL + "/messages",
            data=raw,
            headers=self._headers(request),
            method="POST",
        )
        try:
            return urllib.request.urlopen(http_request, timeout=self.timeout)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            try:
                body = json.loads(detail)
            except (ValueError, TypeError):
                body = None
            classification = SimpleNamespace(status_code=exc.code, body=body)
            if _quota_exhausted(classification):
                raise AIQuotaExceeded(status_code=exc.code) from exc
            raise AITransportError(
                f"OpenCode Go Messages HTTP {exc.code}: {detail or exc.reason}",
                status_code=exc.code, retryable=_retryable_provider_error(classification),
            ) from exc
        except urllib.error.URLError as exc:
            raise AITransportError(f"OpenCode Go Messages transport failed: {exc.reason}") from exc

    def complete(self, request: ChatRequest) -> ModelResponse:
        check_cancelled()
        with self._open(request, stream=False) as response:
            try:
                payload = json.loads(response.read().decode("utf-8"))
            except (ValueError, UnicodeDecodeError) as exc:
                raise AIResponseError("Messages response contains invalid JSON") from exc
        if not isinstance(payload, dict):
            raise AIResponseError("Messages response must be an object")
        if payload.get("type") == "error":
            raise _stream_failure(payload.get("error") or {})
        text_parts: list[str] = []
        reasoning_parts: list[str] = []
        calls: list[ToolCall] = []
        for block in payload.get("content") or []:
            kind = str(block.get("type") or "")
            if kind == "text":
                text_parts.append(str(block.get("text") or ""))
            elif kind == "thinking":
                thinking = str(block.get("thinking") or "")
                if thinking:
                    reasoning_parts.append(thinking)
            elif kind == "tool_use":
                arguments = block.get("input", {})
                calls.append(parse_tool_call(
                    str(block.get("id") or ""), str(block.get("name") or ""),
                    json.dumps(arguments, ensure_ascii=False),
                    finish_reason=str(payload.get("stop_reason") or ""),
                ))

        raw_usage = payload.get("usage") or {}
        return ModelResponse(
            text="".join(text_parts),
            tool_calls=validate_unique_calls(calls),
            usage=_usage(raw_usage.get("input_tokens", 0), raw_usage.get("output_tokens", 0),
                         raw_usage.get("cache_read_input_tokens", 0),
                         raw_usage.get("cache_creation_input_tokens", 0)),
            finish_reason=str(payload.get("stop_reason") or "end_turn"),
            end_turn=(True if payload.get("stop_reason") == "end_turn" else
                      False if payload.get("stop_reason") == "tool_use" else None),
            response_id=str(payload.get("id") or ""),
            visible_reasoning="".join(reasoning_parts),
        )

    def stream(self, request: ChatRequest) -> Iterator[StreamEvent]:
        self._stream_local.metadata = {}
        check_cancelled()
        response = self._open(request, stream=True)
        control = current_control.get()
        if control is not None:
            control.on_cancel(response.close)

        block_info: dict[int, dict[str, str]] = {}
        usage = ModelUsage()
        response_id = ""
        finish_reason = ""
        completed = False
        chunks = 0
        try:
            def timed_lines():
                for raw_line in response:
                    check_cancelled()
                    note_chunk()
                    yield raw_line

            for event in json_sse_events(timed_lines()):
                check_cancelled()
                chunks += 1
                event_type = str(event.get("type") or "")
                if event_type == "error":
                    raise _stream_failure(event.get("error") or {})
                payload = event.get("delta") or event.get("content_block") or {}
                if event_type in {"content_block_delta", "content_block_start"} and any(
                    payload.get(key) for key in ("text", "thinking", "partial_json", "signature", "name")
                ):
                    note_progress(tool_fragment=bool(payload.get("partial_json")))
                if event_type == "message_start":
                    message = event.get("message") or {}
                    response_id = str(message.get("id") or "")
                    raw_usage = message.get("usage") or {}
                    usage = _usage(raw_usage.get("input_tokens", 0), raw_usage.get("output_tokens", 0),
                                   raw_usage.get("cache_read_input_tokens", 0),
                                   raw_usage.get("cache_creation_input_tokens", 0))
                    continue
                if event_type == "content_block_start":
                    index = int(event.get("index") or 0)
                    block = event.get("content_block") or {}
                    kind = str(block.get("type") or "")
                    if kind == "text":
                        text = str(block.get("text") or "")
                        if text:
                            yield StreamEvent(kind=StreamEventKind.TEXT_DELTA, text_delta=text)
                    elif kind == "thinking":
                        thinking = str(block.get("thinking") or "")
                        if thinking:
                            yield StreamEvent(
                                kind=StreamEventKind.REASONING_DELTA,
                                reasoning_delta=thinking,
                            )
                    elif kind == "tool_use":
                        if not isinstance(block.get("input", {}), dict):
                            raise AIResponseError("Messages tool input must be an object")
                        if index in block_info:
                            raise AIResponseError("Messages tool block index reused")
                        call_id = str(block.get("id") or "")
                        name = str(block.get("name") or "")
                        block_info[index] = {"id": call_id, "name": name}
                        yield StreamEvent(
                            kind=StreamEventKind.TOOL_CALL_DELTA,
                            tool_call_index=index,
                            tool_call_id=call_id,
                            tool_name=name,
                            arguments_delta=(json.dumps(block["input"], ensure_ascii=False)
                                             if block.get("input") else ""),
                        )
                    continue
                if event_type == "content_block_delta":
                    index = int(event.get("index") or 0)
                    delta = event.get("delta") or {}
                    kind = str(delta.get("type") or "")
                    if kind == "text_delta":
                        text = str(delta.get("text") or "")
                        if text:
                            yield StreamEvent(kind=StreamEventKind.TEXT_DELTA, text_delta=text)
                    elif kind == "thinking_delta":
                        thinking = str(delta.get("thinking") or "")
                        if thinking:
                            yield StreamEvent(
                                kind=StreamEventKind.REASONING_DELTA,
                                reasoning_delta=thinking,
                            )
                    elif kind == "input_json_delta":
                        info = block_info.get(index)
                        if info is None:
                            raise AIResponseError("Messages tool delta has no tool_use block")
                        yield StreamEvent(
                            kind=StreamEventKind.TOOL_CALL_DELTA,
                            tool_call_index=index,
                            tool_call_id=info.get("id", ""),
                            tool_name=info.get("name", ""),
                            arguments_delta=str(delta.get("partial_json") or ""),
                        )
                    continue
                if event_type == "message_delta":
                    delta = event.get("delta") or {}
                    finish_reason = str(delta.get("stop_reason") or finish_reason or "")
                    raw_usage = event.get("usage") or {}
                    if raw_usage:
                        outputs = max(0, int(raw_usage.get("output_tokens", usage.output_tokens) or 0))
                        usage = ModelUsage(usage.input_tokens, outputs,
                                           usage.input_tokens + outputs, usage.cached_input_tokens)
                    continue
                if event_type == "message_stop":
                    completed = True
                    break

            if not completed or not finish_reason:
                raise AITransportError("OpenCode Go Messages stream ended without completion")
            self._stream_local.metadata = {
                "usage": usage,
                "finish_reason": finish_reason,
                "response_id": response_id,
                "chunk_count": chunks,
            }
            yield StreamEvent(kind=StreamEventKind.COMPLETED, finish_reason=finish_reason,
                              end_turn=True if finish_reason == "end_turn" else
                              False if finish_reason == "tool_use" else None)
        except (AITransportError, AIResponseError, ModelCancelled):
            raise
        except Exception as exc:
            raise AITransportError(
                f"OpenCode Go Messages stream failed: {type(exc).__name__}: {exc}"
            ) from exc
        finally:
            response.close()


class OpenCodeGoBackend:
    """Protocol router for OpenCode Go's mixed Responses/Messages/Chat surface."""

    def __init__(
        self,
        *,
        connection: ProviderConnection,
        profile: ModelProfile,
        api_key: str,
        request_timeout_seconds: float = 120.0,
    ) -> None:
        if connection.adapter is not ProviderAdapter.OPENCODE_GO:
            raise ValueError("OpenCodeGoBackend requires the opencode-go adapter")
        protocol = opencode_go_protocol(profile.model)
        request_timeout_seconds = max(request_timeout_seconds, profile.stream_idle_timeout_seconds)
        self.protocol = protocol
        if protocol == "responses":
            self.backend: Any = _OpenCodeGoResponsesBackend(
                profile=profile,
                api_key=api_key,
                request_timeout_seconds=request_timeout_seconds,
            )
        elif protocol == "messages":
            self.backend = _OpenCodeGoMessagesBackend(
                profile=profile,
                api_key=api_key,
                request_timeout_seconds=request_timeout_seconds,
            )
        else:
            compatible = ProviderConnection(
                provider_id=connection.provider_id,
                adapter=ProviderAdapter.OPENAI_COMPATIBLE,
                credential_ref=connection.credential_ref,
                base_url=OPENCODE_GO_BASE_URL,
                display_name=connection.display_name,
            )
            self.backend = _OpenCodeGoChatBackend(
                connection=compatible,
                profile=profile,
                api_key=api_key,
                request_timeout_seconds=request_timeout_seconds,
            )

    @property
    def name(self) -> str:
        return "opencode-go-" + self.protocol

    def last_stream_metadata(self) -> dict[str, Any]:
        getter = getattr(self.backend, "last_stream_metadata", None)
        return getter() if callable(getter) else {}

    def complete(self, request: ChatRequest) -> ModelResponse:
        return self.backend.complete(request)

    def stream(self, request: ChatRequest) -> Iterator[StreamEvent]:
        yield from self.backend.stream(request)

    def complete_structured(self, request: StructuredRequest) -> dict[str, Any]:
        schema_instruction = (
            "Return exactly one JSON object matching this JSON Schema. Do not emit markdown.\n"
            + json.dumps(request.json_schema, ensure_ascii=False, separators=(",", ":"))
        )
        chat = ChatRequest(
            messages=(
                AIMessage(role=MessageRole.SYSTEM, content=schema_instruction),
                *request.chat.messages,
            ),
            tools=request.chat.tools,
            tool_choice=request.chat.tool_choice,
            temperature=request.chat.temperature,
            max_output_tokens=request.chat.max_output_tokens,
            reasoning=request.chat.reasoning,
            session_id=request.chat.session_id,
            parallel_tool_calls=request.chat.parallel_tool_calls,
        )
        response = self.complete(chat)
        try:
            payload = json.loads(response.text.strip())
        except json.JSONDecodeError as exc:
            raise AIResponseError("OpenCode Go structured response was not valid JSON") from exc
        if not isinstance(payload, dict):
            raise AIResponseError("OpenCode Go structured response must be a JSON object")
        return payload


__all__ = [
    "OPENCODE_GO_BASE_URL",
    "OpenCodeGoBackend",
    "opencode_go_protocol",
]
