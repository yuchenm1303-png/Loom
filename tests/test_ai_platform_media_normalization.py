from __future__ import annotations

from dataclasses import replace
from loom_image_capability import ImageInput
from app.ai.errors import AITransportError
from app.ai.image_input import AIImageInputUnsupported

import pytest

from app.ai import (
    AGENT_FAST_ROLE,
    AIMessage,
    AIPlatform,
    ChatRequest,
    ImagePart,
    MessageRole,
    ModelCapability,
    ModelProfile,
    ModelResponse,
    StreamEvent,
    StreamEventKind,
    StreamingAIPlatform,
    StructuredRequest,
    TextPart,
    ToolChoice,
    ToolDefinition,
)


class _RecordingBackend:
    def __init__(self) -> None:
        self.requests: list[ChatRequest] = []

    def complete(self, request: ChatRequest) -> ModelResponse:
        self.requests.append(request)
        return ModelResponse(text="ok")

    def stream(self, request: ChatRequest):
        self.requests.append(request)
        yield StreamEvent(kind=StreamEventKind.TEXT_DELTA, text_delta="ok")
        yield StreamEvent(kind=StreamEventKind.COMPLETED, finish_reason="stop")

    def last_stream_metadata(self):
        return {}

    def complete_structured(self, request: StructuredRequest):
        self.requests.append(request.chat)
        return {"ok": True}


@pytest.mark.parametrize("path", ["complete", "stream", "structured", "streaming_complete"])
def test_disabled_tools_do_not_require_tool_capability_or_mutate_request(path):
    backend = _RecordingBackend()
    platform = StreamingAIPlatform() if path == "streaming_complete" else AIPlatform()
    profile = _profile(vision=False, structured=True)
    platform.register(replace(profile, capabilities=profile.capabilities - {ModelCapability.TOOL_CALLING}), backend)
    original = ChatRequest(
        messages=(AIMessage(role=MessageRole.USER, content="title"),),
        tools=(ToolDefinition(name="read_file", description="Read", input_schema={"type": "object"}),),
        tool_choice=ToolChoice.NONE,
    )
    if path == "stream":
        list(platform.stream_chat(profile.profile_id, original))
    elif path == "structured":
        platform.execute_structured_chat(profile.profile_id, StructuredRequest(chat=original, json_schema={"type": "object"}))
    else:
        platform.execute_chat(profile.profile_id, original)
    assert backend.requests[0].tools == ()
    assert len(original.tools) == 1


def _profile(*, vision: bool, structured: bool = False) -> ModelProfile:
    capabilities = {
        ModelCapability.TEXT,
        ModelCapability.TOOL_CALLING,
        ModelCapability.STREAMING,
    }
    if vision:
        capabilities.add(ModelCapability.VISION)
    if structured:
        capabilities.add(ModelCapability.STRUCTURED_OUTPUT)
    return ModelProfile(
        profile_id=AGENT_FAST_ROLE.role_id,
        provider="test-provider",
        model="test-model",
        capabilities=frozenset(capabilities),
    )


def _request() -> ChatRequest:
    return ChatRequest(
        messages=(
            AIMessage(
                role=MessageRole.USER,
                content=(
                    TextPart("earlier screenshot"),
                    ImagePart("data:image/png;base64,AA"),
                ),
            ),
            AIMessage(role=MessageRole.ASSISTANT, content="understood"),
            AIMessage(role=MessageRole.USER, content="continue without vision"),
        )
    )


def test_text_only_profile_replaces_historical_images_in_request_copy():
    backend = _RecordingBackend()
    platform = AIPlatform()
    platform.register(_profile(vision=False), backend)
    original = _request()

    result = platform.execute_chat(AGENT_FAST_ROLE.role_id, original)

    assert result.text == "ok"
    assert original.uses_vision is True
    sent = backend.requests[0]
    assert sent.uses_vision is False
    first = sent.messages[0]
    assert isinstance(first.content, tuple)
    assert [part.text for part in first.content if isinstance(part, TextPart)] == [
        "earlier screenshot",
        "[image omitted because the current model does not support image input]",
    ]


def test_vision_profile_preserves_images_unchanged():
    backend = _RecordingBackend()
    platform = AIPlatform()
    platform.register(_profile(vision=True), backend)
    original = _request()

    platform.execute_chat(AGENT_FAST_ROLE.role_id, original)

    assert backend.requests[0] is original
    assert backend.requests[0].uses_vision is True


def test_streaming_platform_normalizes_before_capability_validation():
    backend = _RecordingBackend()
    platform = StreamingAIPlatform(prefer_streaming=True)
    platform.register(_profile(vision=False), backend)

    result = platform.execute_chat(AGENT_FAST_ROLE.role_id, _request())

    assert result.text == "ok"
    assert backend.requests[0].uses_vision is False


def test_structured_chat_normalizes_before_capability_validation():
    backend = _RecordingBackend()
    platform = AIPlatform()
    platform.register(_profile(vision=False, structured=True), backend)

    result = platform.execute_structured_chat(
        AGENT_FAST_ROLE.role_id,
        StructuredRequest(chat=_request(), json_schema={"type": "object"}),
    )

    assert result == {"ok": True}
    assert backend.requests[0].uses_vision is False


class _RejectImages(_RecordingBackend):
    def _record(self, request):
        self.requests.append(request)
        if request.uses_vision:
            raise AITransportError("[invalid_value] Beam supports text input only. This conversation contains an image.",
                retryable=False, status_code=400)

    def complete(self, request):
        self._record(request)
        return ModelResponse(text="ok")

    def stream(self, request):
        self._record(request)
        yield StreamEvent(kind=StreamEventKind.TEXT_DELTA, text_delta="ok")
        yield StreamEvent(kind=StreamEventKind.COMPLETED, finish_reason="stop")

    def complete_structured(self, request):
        self._record(request.chat)
        return {"ok": True}


def _invoke(platform, path, request):
    if path == "stream":
        return list(platform.stream_chat(AGENT_FAST_ROLE.role_id, request))
    if path == "structured":
        return platform.execute_structured_chat(AGENT_FAST_ROLE.role_id, StructuredRequest(chat=request, json_schema={"type": "object"}))
    return platform.execute_chat(AGENT_FAST_ROLE.role_id, request)


@pytest.mark.parametrize("path", ["complete", "stream", "structured", "streaming_complete"])
def test_unknown_image_rejection_recovers_text_turn_once_without_mutating_history(path):
    platform = StreamingAIPlatform(prefer_streaming=True) if path == "streaming_complete" else AIPlatform()
    profile = replace(_profile(vision=False, structured=True), image_input=ImageInput.UNKNOWN)
    backend = _RejectImages()
    platform.register(profile, backend)
    original = _request()
    _invoke(platform, path, original)
    assert len(backend.requests) == 2
    assert backend.requests[0].uses_vision
    assert not backend.requests[1].uses_vision
    assert original.uses_vision
    assert backend.requests[1].messages[-1].content == "continue without vision"
    _invoke(platform, path, original)
    assert len(backend.requests) == 3, "later text turns no longer resend known-unsupported history images"


@pytest.mark.parametrize("path", ["complete", "stream", "structured", "streaming_complete"])
def test_new_image_is_not_silently_removed_or_retried(path):
    platform = StreamingAIPlatform(prefer_streaming=True) if path == "streaming_complete" else AIPlatform()
    platform.register(replace(_profile(vision=False, structured=True), image_input=ImageInput.UNKNOWN), backend := _RejectImages())
    request = replace(_request(), messages=_request().messages[:1])
    with pytest.raises(AIImageInputUnsupported):
        _invoke(platform, path, request)
    assert len(backend.requests) == 1
    with pytest.raises(AIImageInputUnsupported):
        _invoke(platform, path, request)
    assert len(backend.requests) == 1, "learned text-only capability blocks new images before dispatch"
    assert request.uses_vision


@pytest.mark.parametrize("status,message", [(401, "image input not supported"), (500, "supports text input only"),
    (400, "Invalid image URL"), (400, "Image exceeds size limit")])
def test_other_errors_do_not_change_image_support_or_replay_requests(status, message):
    class Backend(_RecordingBackend):
        def complete(self, request):
            self.requests.append(request)
            raise AITransportError(message, status_code=status)
    platform = AIPlatform()
    platform.register(replace(_profile(vision=False), image_input=ImageInput.UNKNOWN), backend := Backend())
    with pytest.raises(AITransportError, match=message):
        platform.execute_chat(AGENT_FAST_ROLE.role_id, _request())
    assert len(backend.requests) == 1
    assert not platform._image_rejections


def test_partial_stream_is_never_replayed_on_image_rejection():
    class Backend(_RejectImages):
        def stream(self, request):
            self.requests.append(request)
            yield StreamEvent(kind=StreamEventKind.TEXT_DELTA, text_delta="partial")
            raise AITransportError("supports text input only", status_code=400)
    platform = AIPlatform()
    platform.register(replace(_profile(vision=False), image_input=ImageInput.UNKNOWN), backend := Backend())
    with pytest.raises(AIImageInputUnsupported):
        list(platform.stream_chat(AGENT_FAST_ROLE.role_id, _request()))
    assert len(backend.requests) == 1


def test_rejection_evidence_reaches_bridge_and_does_not_affect_other_models(tmp_path, monkeypatch):
    monkeypatch.setenv("LOOM_HOME", str(tmp_path))
    from loom_model_bridge import _describe_model
    from app.ai.model_store import ModelConfigStore
    from app.ai.reasoning_store import ReasoningConfigStore
    profile = replace(_profile(vision=False), image_input=ImageInput.UNKNOWN, model="beam-free")
    platform = AIPlatform()
    platform.register(profile, _RejectImages())
    platform._loom_model_connections = ({"profile_id": profile.profile_id,
        "base_url": "https://account.smirel.com/model/v1/opencode-go"},)
    platform.execute_chat(profile.profile_id, _request())
    metadata = _describe_model(ModelConfigStore(tmp_path), ReasoningConfigStore(tmp_path), "builtin:opencode-go:beam-free", "beam-free")
    assert metadata["vision"] is False
    assert metadata["imageInput"]["source"] == "provider-rejection"
    vision = replace(_profile(vision=True), profile_id="other.role", model="other-model")
    other = _RecordingBackend()
    platform.register(vision, other)
    platform.execute_chat(vision.profile_id, _request())
    assert other.requests[0].uses_vision


def test_corrected_request_failure_is_not_retried_again():
    class Backend(_RejectImages):
        def complete(self, request):
            self._record(request)
            raise AITransportError("provider unavailable", status_code=503)
    platform = AIPlatform()
    platform.register(replace(_profile(vision=False), image_input=ImageInput.UNKNOWN), backend := Backend())
    with pytest.raises(AITransportError, match="provider unavailable"):
        platform.execute_chat(AGENT_FAST_ROLE.role_id, _request())
    assert len(backend.requests) == 2
