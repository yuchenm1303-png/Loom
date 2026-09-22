from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
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
    ToolChoice,
)
from .errors import AIResponseError, AITransportError
from .execution_control import ModelCancelled, check_cancelled, current_control, note_progress
from .openai_streaming import OpenAIStreamingChatBackend
from .profiles import ModelProfile
from .provider_catalog import ProviderAdapter, ProviderConnection
from .reasoning import ReasoningKind


OPENCODE_GO_BASE_URL = "https://opencode.ai/zen/go/v1"
OPENCODE_GO_USER_AGENT = "Loom/0.1 (coding-agent)"

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


def _usage(input_tokens: int = 0, output_tokens: int = 0) -> ModelUsage:
    return ModelUsage(
        input_tokens=max(0, int(input_tokens or 0)),
        output_tokens=max(0, int(output_tokens or 0)),
        total_tokens=max(0, int(input_tokens or 0)) + max(0, int(output_tokens or 0)),
    )


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
                items.append({"role": message.role.value, "content": content})
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
            raw = str(getattr(item, "arguments", "") or "")
            try:
                arguments = json.loads(raw) if raw else {}
            except json.JSONDecodeError as exc:
                raise AIResponseError("OpenCode Responses tool call returned invalid JSON") from exc
            calls.append(
                ToolCall(
                    call_id=str(getattr(item, "call_id", "") or getattr(item, "id", "") or ""),
                    name=str(getattr(item, "name", "") or ""),
                    arguments=arguments,
                )
            )
        return tuple(calls)

    def complete(self, request: ChatRequest) -> ModelResponse:
        try:
            response = self.client.responses.create(**self._kwargs(request))
        except Exception as exc:
            raise AITransportError(
                f"OpenCode Go Responses request failed: {type(exc).__name__}: {exc}"
            ) from exc
        usage = getattr(response, "usage", None)
        return ModelResponse(
            text=str(getattr(response, "output_text", "") or ""),
            tool_calls=self._response_calls(response),
            usage=_usage(
                getattr(usage, "input_tokens", 0) if usage is not None else 0,
                getattr(usage, "output_tokens", 0) if usage is not None else 0,
            ),
            finish_reason=str(getattr(response, "status", "") or "completed"),
            response_id=str(getattr(response, "id", "") or ""),
            visible_reasoning=_responses_visible_reasoning(response),
        )

    def stream(self, request: ChatRequest) -> Iterator[StreamEvent]:
        self._stream_local.metadata = {}
        kwargs = self._kwargs(request)
        kwargs["stream"] = True
        try:
            stream = self.client.responses.create(**kwargs)
        except Exception as exc:
            raise AITransportError(
                f"OpenCode Go Responses stream failed: {type(exc).__name__}: {exc}"
            ) from exc

        control = current_control.get()
        close = getattr(stream, "close", None)
        if control is not None and callable(close):
            control.on_cancel(close)

        call_indexes: dict[str, int] = {}
        usage = ModelUsage()
        response_id = ""
        finish_reason = ""
        chunks = 0
        try:
            for event in stream:
                check_cancelled()
                note_progress()
                chunks += 1
                event_type = str(getattr(event, "type", "") or "")
                if event_type == "response.output_text.delta":
                    delta = str(getattr(event, "delta", "") or "")
                    if delta:
                        yield StreamEvent(kind=StreamEventKind.TEXT_DELTA, text_delta=delta)
                    continue
                if event_type == "response.reasoning_summary_text.delta":
                    delta = str(getattr(event, "delta", "") or "")
                    if delta:
                        yield StreamEvent(
                            kind=StreamEventKind.REASONING_DELTA,
                            reasoning_delta=delta,
                        )
                    continue
                if event_type == "response.output_item.added":
                    item = getattr(event, "item", None)
                    if str(getattr(item, "type", "") or "") == "function_call":
                        index = int(getattr(event, "output_index", len(call_indexes)) or 0)
                        call_id = str(getattr(item, "call_id", "") or getattr(item, "id", "") or "")
                        if call_id:
                            call_indexes[call_id] = index
                        yield StreamEvent(
                            kind=StreamEventKind.TOOL_CALL_DELTA,
                            tool_call_index=index,
                            tool_call_id=call_id,
                            tool_name=str(getattr(item, "name", "") or ""),
                        )
                    continue
                if event_type == "response.function_call_arguments.delta":
                    call_id = str(getattr(event, "call_id", "") or getattr(event, "item_id", "") or "")
                    index = getattr(event, "output_index", None)
                    if index is None and call_id:
                        index = call_indexes.get(call_id)
                    yield StreamEvent(
                        kind=StreamEventKind.TOOL_CALL_DELTA,
                        tool_call_index=int(index) if index is not None else None,
                        tool_call_id=call_id,
                        arguments_delta=str(getattr(event, "delta", "") or ""),
                    )
                    continue
                if event_type == "response.completed":
                    response = getattr(event, "response", None)
                    response_id = str(getattr(response, "id", "") or "")
                    finish_reason = str(getattr(response, "status", "") or "completed")
                    raw_usage = getattr(response, "usage", None)
                    if raw_usage is not None:
                        usage = _usage(
                            getattr(raw_usage, "input_tokens", 0),
                            getattr(raw_usage, "output_tokens", 0),
                        )
                    break
                if event_type in {"response.failed", "response.incomplete"}:
                    raise AITransportError(f"OpenCode Go Responses ended as {event_type}")
            if not finish_reason:
                raise AITransportError("OpenCode Go Responses stream ended without completion")
            self._stream_local.metadata = {
                "usage": usage,
                "finish_reason": finish_reason,
                "response_id": response_id,
                "chunk_count": chunks,
            }
            yield StreamEvent(kind=StreamEventKind.COMPLETED, finish_reason=finish_reason)
        except (AITransportError, ModelCancelled):
            raise
        except Exception as exc:
            raise AITransportError(
                f"OpenCode Go Responses stream failed: {type(exc).__name__}: {exc}"
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
        if request.tools:
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
            raise AITransportError(
                f"OpenCode Go Messages HTTP {exc.code}: {detail or exc.reason}"
            ) from exc
        except urllib.error.URLError as exc:
            raise AITransportError(f"OpenCode Go Messages transport failed: {exc.reason}") from exc

    def complete(self, request: ChatRequest) -> ModelResponse:
        with self._open(request, stream=False) as response:
            payload = json.loads(response.read().decode("utf-8"))
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
                arguments = block.get("input") or {}
                if not isinstance(arguments, dict):
                    raise AIResponseError("OpenCode Messages tool input must be an object")
                calls.append(
                    ToolCall(
                        call_id=str(block.get("id") or ""),
                        name=str(block.get("name") or ""),
                        arguments=arguments,
                    )
                )
        raw_usage = payload.get("usage") or {}
        return ModelResponse(
            text="".join(text_parts),
            tool_calls=tuple(calls),
            usage=_usage(raw_usage.get("input_tokens", 0), raw_usage.get("output_tokens", 0)),
            finish_reason=str(payload.get("stop_reason") or "end_turn"),
            response_id=str(payload.get("id") or ""),
            visible_reasoning="".join(reasoning_parts),
        )

    def stream(self, request: ChatRequest) -> Iterator[StreamEvent]:
        self._stream_local.metadata = {}
        response = self._open(request, stream=True)
        control = current_control.get()
        if control is not None:
            control.on_cancel(response.close)

        block_info: dict[int, dict[str, str]] = {}
        usage = ModelUsage()
        response_id = ""
        finish_reason = ""
        chunks = 0
        try:
            for raw_line in response:
                check_cancelled()
                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if not data or data == "[DONE]":
                    continue
                note_progress()
                chunks += 1
                event = json.loads(data)
                event_type = str(event.get("type") or "")
                if event_type == "message_start":
                    message = event.get("message") or {}
                    response_id = str(message.get("id") or "")
                    raw_usage = message.get("usage") or {}
                    usage = _usage(raw_usage.get("input_tokens", 0), raw_usage.get("output_tokens", 0))
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
                        call_id = str(block.get("id") or "")
                        name = str(block.get("name") or "")
                        block_info[index] = {"id": call_id, "name": name}
                        yield StreamEvent(
                            kind=StreamEventKind.TOOL_CALL_DELTA,
                            tool_call_index=index,
                            tool_call_id=call_id,
                            tool_name=name,
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
                        info = block_info.get(index, {})
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
                        usage = _usage(
                            usage.input_tokens,
                            raw_usage.get("output_tokens", usage.output_tokens),
                        )
                    continue
                if event_type == "message_stop":
                    finish_reason = finish_reason or "end_turn"
                    break

            if not finish_reason:
                raise AITransportError("OpenCode Go Messages stream ended without completion")
            self._stream_local.metadata = {
                "usage": usage,
                "finish_reason": finish_reason,
                "response_id": response_id,
                "chunk_count": chunks,
            }
            yield StreamEvent(kind=StreamEventKind.COMPLETED, finish_reason=finish_reason)
        except (AITransportError, ModelCancelled):
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
