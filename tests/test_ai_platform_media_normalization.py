from __future__ import annotations

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
