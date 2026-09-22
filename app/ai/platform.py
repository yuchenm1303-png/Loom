from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from typing import Any, Protocol

from .capabilities import ModelCapability
from .contracts import (
    AIMessage,
    ChatRequest,
    ImagePart,
    ModelResponse,
    StreamEvent,
    StructuredRequest,
    TextPart,
)
from .profiles import ModelProfile, ModelRegistry
from .reasoning_text import merge_visible_reasoning, split_inline_reasoning

_UNSUPPORTED_IMAGE_PLACEHOLDER = "[image omitted because the current model does not support image input]"


class StructuredModelBackend(Protocol):
    """Legacy narrow structured contract kept for detached compatibility tests."""

    name: str

    def extract_json(self, request_payload: dict[str, Any]) -> dict[str, Any]:
        ...


class ChatModelBackend(Protocol):
    name: str

    def complete(self, request: ChatRequest) -> ModelResponse:
        ...

    def complete_structured(self, request: StructuredRequest) -> dict[str, Any]:
        ...

    def stream(self, request: ChatRequest) -> Iterator[StreamEvent]:
        ...


class AIPlatform:
    """Provider-neutral runtime router keyed only by stable model profile IDs."""

    def __init__(self, registry: ModelRegistry | None = None) -> None:
        self.registry = registry or ModelRegistry()
        self._backends: dict[str, object] = {}

    def register(self, profile: ModelProfile, backend: object) -> None:
        self.registry.register(profile)
        self._backends[profile.profile_id] = backend

    def _backend_for(self, profile_id: str) -> tuple[ModelProfile, object]:
        profile = self.registry.get(profile_id)
        try:
            backend = self._backends[profile.profile_id]
        except KeyError as exc:
            raise RuntimeError(f"model profile is not bound to a backend: {profile.profile_id}") from exc
        return profile, backend

    def _require_chat_capabilities(
        self,
        profile_id: str,
        request: ChatRequest,
        *,
        streaming: bool = False,
        structured: bool = False,
    ) -> ModelProfile:
        required = {ModelCapability.TEXT}
        if request.uses_vision:
            required.add(ModelCapability.VISION)
        if request.tools:
            required.add(ModelCapability.TOOL_CALLING)
        if streaming:
            required.add(ModelCapability.STREAMING)
        if structured:
            required.add(ModelCapability.STRUCTURED_OUTPUT)
        return self.registry.require(profile_id, capabilities=required)

    def _normalize_chat_request(self, profile_id: str, request: ChatRequest) -> ChatRequest:
        """Project unsupported media out of a request without mutating history.

        A durable conversation can legitimately contain images from an earlier
        turn and later continue on a text-only model.  Capability validation
        must describe the request that will actually be sent, not every medium
        ever stored in the transcript.  Match Codex's context normalization:
        retain the message boundary and replace each unsupported image with a
        visible text fragment in this request copy only.
        """

        profile = self.registry.get(profile_id)
        if ModelCapability.VISION in profile.capabilities or not request.uses_vision:
            return request

        changed = False
        messages: list[AIMessage] = []
        for message in request.messages:
            if not message.uses_vision:
                messages.append(message)
                continue
            assert not isinstance(message.content, str)
            content = tuple(
                TextPart(_UNSUPPORTED_IMAGE_PLACEHOLDER)
                if isinstance(part, ImagePart)
                else part
                for part in message.content
            )
            messages.append(replace(message, content=content))
            changed = True

        return replace(request, messages=tuple(messages)) if changed else request

    def execute_chat(self, profile_id: str, request: ChatRequest) -> ModelResponse:
        request = self._normalize_chat_request(profile_id, request)
        self._require_chat_capabilities(profile_id, request)
        _profile, backend = self._backend_for(profile_id)
        complete = getattr(backend, "complete", None)
        if not callable(complete):
            raise RuntimeError(f"backend for {profile_id!r} does not support chat completion")
        result = complete(request)
        if not isinstance(result, ModelResponse):
            raise TypeError("chat model backend must return ModelResponse")
        public_text, inline_reasoning = split_inline_reasoning(result.text)
        if inline_reasoning:
            result = replace(
                result,
                text=public_text,
                visible_reasoning=merge_visible_reasoning(
                    result.visible_reasoning,
                    inline_reasoning,
                ),
            )
        return result

    def execute_structured_chat(
        self,
        profile_id: str,
        request: StructuredRequest,
    ) -> dict[str, Any]:
        normalized_chat = self._normalize_chat_request(profile_id, request.chat)
        if normalized_chat is not request.chat:
            request = replace(request, chat=normalized_chat)
        self._require_chat_capabilities(profile_id, request.chat, structured=True)
        _profile, backend = self._backend_for(profile_id)
        complete_structured = getattr(backend, "complete_structured", None)
        if not callable(complete_structured):
            raise RuntimeError(f"backend for {profile_id!r} does not support structured output")
        result = complete_structured(request)
        if not isinstance(result, dict):
            raise TypeError("structured model backend must return a JSON object")
        return result

    def stream_chat(self, profile_id: str, request: ChatRequest) -> Iterator[StreamEvent]:
        request = self._normalize_chat_request(profile_id, request)
        self._require_chat_capabilities(profile_id, request, streaming=True)
        _profile, backend = self._backend_for(profile_id)
        stream = getattr(backend, "stream", None)
        if not callable(stream):
            raise RuntimeError(f"backend for {profile_id!r} does not support streaming")
        for event in stream(request):
            if not isinstance(event, StreamEvent):
                raise TypeError("streaming model backend must yield StreamEvent values")
            yield event

    def execute_structured(
        self,
        profile_id: str,
        request_payload: dict[str, Any],
    ) -> dict[str, Any]:
        """Legacy structured packet lane retained until Listing migration is explicit."""

        self.registry.require(
            profile_id,
            capabilities=(ModelCapability.STRUCTURED_OUTPUT,),
        )
        _profile, backend = self._backend_for(profile_id)
        extract_json = getattr(backend, "extract_json", None)
        if not callable(extract_json):
            raise RuntimeError(f"backend for {profile_id!r} does not support legacy JSON tasks")
        result = extract_json(request_payload)
        if not isinstance(result, dict):
            raise TypeError("structured model backend must return a JSON object")
        return result


__all__ = ["AIPlatform", "ChatModelBackend", "StructuredModelBackend"]
