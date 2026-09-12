from __future__ import annotations

from types import SimpleNamespace

from app.agent_runtime import AgentRuntime, FileAgentSessionStore, ToolRegistry
from app.agent_runtime.contracts import AgentSession, AgentStatus
from app.agent_runtime.storage import session_from_dict, session_to_dict
from app.ai import (
    AIMessage,
    ChatRequest,
    CredentialRef,
    MessageRole,
    ModelCapability,
    ModelProfile,
    ModelResponse,
    ProviderAdapter,
    ProviderConnection,
    ReasoningKind,
    ReasoningRequest,
    ToolDefinition,
)
from app.ai.openai_runtime import OpenAIChatBackend
from app.ai.openai_streaming import OpenAIStreamingChatBackend
from app.ai.streaming_platform import ProviderStreamEventKind, StreamingAIPlatform


def _connection(base_url: str = "https://api.deepseek.com") -> ProviderConnection:
    return ProviderConnection(
        provider_id="test-provider",
        adapter=ProviderAdapter.OPENAI_COMPATIBLE,
        credential_ref=CredentialRef.runtime("test-key"),
        base_url=base_url,
    )


def _profile(model: str, *, streaming: bool = False) -> ModelProfile:
    capabilities = {ModelCapability.TEXT, ModelCapability.TOOL_CALLING}
    if streaming:
        capabilities.add(ModelCapability.STREAMING)
    return ModelProfile(
        profile_id="test-profile",
        provider="test-provider",
        model=model,
        capabilities=frozenset(capabilities),
    )


def _tool() -> ToolDefinition:
    return ToolDefinition(
        name="echo",
        description="echo a value",
        input_schema={
            "type": "object",
            "properties": {"value": {"type": "string"}},
        },
    )


def _reasoning() -> ReasoningRequest:
    return ReasoningRequest(ReasoningKind.OPENAI_EFFORT, "high")


def test_deepseek_tool_request_replays_private_reasoning_content() -> None:
    backend = OpenAIChatBackend(
        connection=_connection(),
        profile=_profile("deepseek-v4-pro"),
        api_key="test-secret",
        client=object(),
    )
    request = ChatRequest(
        messages=(
            AIMessage(
                role=MessageRole.ASSISTANT,
                content="I will use the tool.",
                reasoning_content="provider-private-reasoning",
            ),
        ),
        tools=(_tool(),),
        reasoning=_reasoning(),
    )

    kwargs = backend._request_kwargs(request)

    assert kwargs["messages"][0]["reasoning_content"] == "provider-private-reasoning"
    assert kwargs["extra_body"] == {
        "reasoning_effort": "high",
        "thinking": {"type": "enabled"},
    }


def test_deepseek_tool_request_keeps_empty_reasoning_field_for_synthetic_assistant() -> None:
    backend = OpenAIChatBackend(
        connection=_connection(),
        profile=_profile("deepseek-v4-pro"),
        api_key="test-secret",
        client=object(),
    )
    request = ChatRequest(
        messages=(AIMessage(role=MessageRole.ASSISTANT, content="synthetic recovery"),),
        tools=(_tool(),),
        reasoning=_reasoning(),
    )

    kwargs = backend._request_kwargs(request)

    assert kwargs["messages"][0]["reasoning_content"] == ""


def test_non_deepseek_provider_never_replays_private_reasoning_field() -> None:
    backend = OpenAIChatBackend(
        connection=_connection("https://relay.example.invalid/v1"),
        profile=_profile("openai/gpt-5.6-sol"),
        api_key="test-secret",
        client=object(),
    )
    request = ChatRequest(
        messages=(
            AIMessage(
                role=MessageRole.ASSISTANT,
                content="visible",
                reasoning_content="must-not-leak",
            ),
        ),
        tools=(_tool(),),
        reasoning=ReasoningRequest(ReasoningKind.OPENAI_EFFORT, "high"),
    )

    kwargs = backend._request_kwargs(request)

    assert "reasoning_content" not in kwargs["messages"][0]


def test_deepseek_non_streaming_response_retains_private_reasoning() -> None:
    message = SimpleNamespace(
        content="done",
        reasoning_content="private-continuation",
        tool_calls=[],
    )
    response = SimpleNamespace(
        id="resp-1",
        usage=SimpleNamespace(prompt_tokens=3, completion_tokens=2, total_tokens=5),
        choices=[SimpleNamespace(message=message, finish_reason="stop")],
    )
    completions = SimpleNamespace(create=lambda **_kwargs: response)
    backend = OpenAIChatBackend(
        connection=_connection(),
        profile=_profile("deepseek-v4-flash"),
        api_key="test-secret",
        client=SimpleNamespace(chat=SimpleNamespace(completions=completions)),
    )

    result = backend.complete(
        ChatRequest(
            messages=(AIMessage(role=MessageRole.USER, content="hello"),),
            reasoning=_reasoning(),
        )
    )

    assert result.text == "done"
    assert result.reasoning_content == "private-continuation"


def test_provider_private_reasoning_survives_session_snapshot_round_trip(tmp_path) -> None:
    session = AgentSession(
        session_id="00000000-0000-0000-0000-000000000001",
        profile_id="test-profile",
        system_prompt="system",
        workspace_dir=str(tmp_path),
        created_at="2026-09-12T00:00:00+00:00",
        updated_at="2026-09-12T00:00:00+00:00",
        messages=[
            AIMessage(
                role=MessageRole.ASSISTANT,
                content="visible",
                reasoning_content="private-continuation",
            )
        ],
    )

    payload = session_to_dict(session)
    restored = session_from_dict(payload)

    assert payload["messages"][0]["_provider_reasoning_content"] == "private-continuation"
    assert "reasoning_content" not in payload["messages"][0]
    assert restored.messages[0].reasoning_content == "private-continuation"


class _ScriptedPlatform:
    def __init__(self, responses) -> None:
        self.responses = iter(responses)
        self.requests = []

    def execute_chat(self, _profile, request):
        self.requests.append(request)
        return next(self.responses)


def test_truncated_recovery_replays_matching_provider_reasoning_state(tmp_path) -> None:
    platform = _ScriptedPlatform(
        [
            ModelResponse(
                text="partial answer",
                finish_reason="length",
                reasoning_content="private-truncated-reasoning",
            ),
            ModelResponse(text="done", finish_reason="stop"),
        ]
    )
    runtime = AgentRuntime(
        platform=platform,
        store=FileAgentSessionStore(tmp_path),
        tools=ToolRegistry(),
    )
    session = runtime.create_session("agent.fast")

    result = runtime.start_turn(session.session_id, "continue")

    assert result.status is AgentStatus.COMPLETED
    retry_messages = platform.requests[1].messages
    replay = next(
        message
        for message in retry_messages
        if message.role is MessageRole.ASSISTANT and message.content == "partial answer"
    )
    assert replay.reasoning_content == "private-truncated-reasoning"
    stored = runtime.store.load(session.session_id)
    assert not any(message.content == "partial answer" for message in stored.messages)
    runtime.close()


class _RecordingCompletions:
    def __init__(self, chunks) -> None:
        self.chunks = list(chunks)
        self.calls: list[dict[str, object]] = []

    def create(self, **kwargs):
        self.calls.append(dict(kwargs))
        return iter(self.chunks)


def test_deepseek_streaming_retains_reasoning_without_publishing_it() -> None:
    chunks = [
        SimpleNamespace(
            id="resp-stream",
            usage=None,
            choices=[SimpleNamespace(
                delta=SimpleNamespace(
                    content="Hel",
                    reasoning_content="private-1",
                    tool_calls=[],
                ),
                finish_reason=None,
            )],
        ),
        SimpleNamespace(
            id="resp-stream",
            usage=None,
            choices=[SimpleNamespace(
                delta=SimpleNamespace(
                    content="lo",
                    reasoning_content="private-2",
                    tool_calls=[],
                ),
                finish_reason="stop",
            )],
        ),
        SimpleNamespace(
            id="resp-stream",
            usage=SimpleNamespace(prompt_tokens=4, completion_tokens=2, total_tokens=6),
            choices=[],
        ),
    ]
    completions = _RecordingCompletions(chunks)
    profile = _profile("deepseek-v4-pro", streaming=True)
    backend = OpenAIStreamingChatBackend(
        connection=_connection(),
        profile=profile,
        api_key="test-secret",
        client=SimpleNamespace(chat=SimpleNamespace(completions=completions)),
    )
    platform = StreamingAIPlatform(prefer_streaming=True)
    platform.register(profile, backend)
    published = []
    platform.subscribe_stream(published.append)

    result = platform.execute_chat(
        profile.profile_id,
        ChatRequest(
            messages=(AIMessage(role=MessageRole.USER, content="hello"),),
            tools=(_tool(),),
            reasoning=_reasoning(),
        ),
    )

    assert result.text == "Hello"
    assert result.reasoning_content == "private-1private-2"
    assert all(event.kind in {
        ProviderStreamEventKind.TEXT_DELTA,
        ProviderStreamEventKind.TOOL_CALL_DELTA,
        ProviderStreamEventKind.COMPLETED,
    } for event in published)
    assert all("private" not in str(event) for event in published)
    assert completions.calls[0]["stream"] is True
    assert completions.calls[0]["stream_options"] == {"include_usage": True}
    assert completions.calls[0]["extra_body"] == {
        "reasoning_effort": "high",
        "thinking": {"type": "enabled"},
    }
    assert [
        event.text_delta
        for event in published
        if event.kind is ProviderStreamEventKind.TEXT_DELTA
    ] == ["Hel", "lo"]
