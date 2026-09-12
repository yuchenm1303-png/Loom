from __future__ import annotations

from types import SimpleNamespace

from app.ai import (
    AGENT_FAST_ROLE,
    AIConfiguration,
    AIMessage,
    ChatRequest,
    CredentialRef,
    CredentialResolver,
    MessageRole,
    ModelBinding,
    ModelCapability,
    ModelProfile,
    ProviderAdapter,
    ProviderConnection,
    ReasoningKind,
    ReasoningRequest,
    StructuredOutputMode,
    StructuredRequest,
    ToolCall,
    ToolDefinition,
    build_ai_platform,
)
from app.ai.openai_responses import OpenAIResponsesBackend
from app.ai.openai_streaming import OpenAIStreamingChatBackend
from app.ai.streaming_platform import StreamingAIPlatform


class FakeResponses:
    def __init__(self, results):
        self.results = list(results)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if not self.results:
            raise AssertionError("fake responses client ran out of results")
        return self.results.pop(0)


class FakeClient:
    def __init__(self, results):
        self.responses = FakeResponses(results)


def _connection(adapter=ProviderAdapter.OPENAI):
    return ProviderConnection(
        provider_id="test-openai",
        adapter=adapter,
        credential_ref=CredentialRef.runtime("test-key"),
        base_url="https://relay.example.invalid/v1" if adapter is ProviderAdapter.OPENAI_COMPATIBLE else "",
    )


def _profile(connection, *, capabilities=None):
    return ModelProfile(
        profile_id="agent.fast",
        provider=connection.provider_id,
        model="gpt-5.6-sol",
        capabilities=frozenset(
            capabilities
            or {
                ModelCapability.TEXT,
                ModelCapability.TOOL_CALLING,
                ModelCapability.STREAMING,
                ModelCapability.REASONING,
                ModelCapability.STRUCTURED_OUTPUT,
            }
        ),
    )


def _tool():
    return ToolDefinition(
        name="lookup",
        description="Look something up",
        input_schema={
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    )


def _usage(input_tokens=10, output_tokens=4):
    return SimpleNamespace(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=input_tokens + output_tokens,
    )


def test_responses_request_is_stateless_and_uses_flat_function_tools() -> None:
    connection = _connection()
    profile = _profile(connection)
    response = SimpleNamespace(
        id="resp_1",
        status="completed",
        incomplete_details=None,
        output_text="",
        usage=_usage(),
        output=[
            SimpleNamespace(
                id="rs_1",
                type="reasoning",
                encrypted_content="ciphertext",
                summary=[],
            ),
            SimpleNamespace(
                id="fc_1",
                type="function_call",
                call_id="call_1",
                name="lookup",
                arguments='{"query":"loom"}',
                status="completed",
            ),
        ],
    )
    client = FakeClient([response])
    backend = OpenAIResponsesBackend(
        connection=connection,
        profile=profile,
        api_key="secret",
        client=client,
    )

    result = backend.complete(
        ChatRequest(
            messages=(AIMessage(role=MessageRole.USER, content="Find Loom"),),
            tools=(_tool(),),
            reasoning=ReasoningRequest(ReasoningKind.OPENAI_EFFORT, "high"),
        )
    )

    assert result.tool_calls == (
        ToolCall(call_id="call_1", name="lookup", arguments={"query": "loom"}),
    )
    assert result.finish_reason == "completed"
    assert result.response_id == "resp_1"
    assert result.provider_state[0]["type"] == "reasoning"
    assert result.provider_state[0]["encrypted_content"] == "ciphertext"
    kwargs = client.responses.calls[0]
    assert kwargs["store"] is False
    assert kwargs["include"] == ["reasoning.encrypted_content"]
    assert kwargs["reasoning"] == {"effort": "high"}
    assert kwargs["tool_choice"] == "auto"
    assert kwargs["tools"][0] == {
        "type": "function",
        "name": "lookup",
        "description": "Look something up",
        "parameters": _tool().input_schema,
    }
    assert "function" not in kwargs["tools"][0]


def test_responses_replays_raw_output_items_and_function_result() -> None:
    connection = _connection()
    profile = _profile(connection)
    backend = OpenAIResponsesBackend(
        connection=connection,
        profile=profile,
        api_key="secret",
        client=FakeClient([]),
    )
    state = (
        {
            "id": "rs_1",
            "type": "reasoning",
            "encrypted_content": "ciphertext",
            "summary": [],
        },
        {
            "id": "fc_1",
            "type": "function_call",
            "call_id": "call_1",
            "name": "lookup",
            "arguments": '{"query":"loom"}',
            "status": "completed",
        },
    )
    request = ChatRequest(
        messages=(
            AIMessage(role=MessageRole.USER, content="Find Loom"),
            AIMessage(
                role=MessageRole.ASSISTANT,
                content="",
                tool_calls=(ToolCall(call_id="call_1", name="lookup", arguments={"query": "loom"}),),
                provider_state=state,
            ),
            AIMessage(
                role=MessageRole.TOOL,
                content='{"result":"ok"}',
                tool_call_id="call_1",
            ),
        ),
        tools=(_tool(),),
    )

    kwargs = backend._request_kwargs(request)

    assert kwargs["input"][1] == state[0]
    assert kwargs["input"][2] == state[1]
    assert kwargs["input"][3] == {
        "type": "function_call_output",
        "call_id": "call_1",
        "output": '{"result":"ok"}',
    }
    assert sum(item.get("type") == "function_call" for item in kwargs["input"]) == 1


def test_responses_legacy_assistant_history_falls_back_without_provider_state() -> None:
    connection = _connection()
    backend = OpenAIResponsesBackend(
        connection=connection,
        profile=_profile(connection),
        api_key="secret",
        client=FakeClient([]),
    )
    request = ChatRequest(
        messages=(
            AIMessage(role=MessageRole.USER, content="Run it"),
            AIMessage(
                role=MessageRole.ASSISTANT,
                content="I'll use the tool.",
                tool_calls=(ToolCall(call_id="call_old", name="lookup", arguments={"query": "old"}),),
            ),
            AIMessage(role=MessageRole.TOOL, content="done", tool_call_id="call_old"),
        ),
        tools=(_tool(),),
    )

    input_items = backend._request_kwargs(request)["input"]

    assert input_items[1] == {"role": "assistant", "content": "I'll use the tool."}
    assert input_items[2]["type"] == "function_call"
    assert input_items[2]["call_id"] == "call_old"
    assert input_items[3] == {
        "type": "function_call_output",
        "call_id": "call_old",
        "output": "done",
    }


def test_responses_structured_output_uses_text_format_json_schema() -> None:
    connection = _connection()
    response = SimpleNamespace(
        id="resp_json",
        status="completed",
        incomplete_details=None,
        output_text='{"answer":42}',
        output=[],
        usage=_usage(),
    )
    client = FakeClient([response])
    backend = OpenAIResponsesBackend(
        connection=connection,
        profile=_profile(connection),
        api_key="secret",
        client=client,
    )
    schema = {
        "type": "object",
        "properties": {"answer": {"type": "integer"}},
        "required": ["answer"],
        "additionalProperties": False,
    }

    payload = backend.complete_structured(
        StructuredRequest(
            chat=ChatRequest(messages=(AIMessage(role=MessageRole.USER, content="Answer"),)),
            json_schema=schema,
            schema_name="answer_schema",
            mode=StructuredOutputMode.JSON_SCHEMA,
        )
    )

    assert payload == {"answer": 42}
    assert client.responses.calls[0]["text"] == {
        "format": {
            "type": "json_schema",
            "name": "answer_schema",
            "strict": True,
            "schema": schema,
        }
    }


def test_responses_stream_normalizes_tool_deltas_and_keeps_private_state_internal() -> None:
    connection = _connection()
    profile = _profile(connection)
    final_response = SimpleNamespace(
        id="resp_stream",
        status="completed",
        incomplete_details=None,
        output_text="",
        usage=_usage(12, 5),
        output=[
            SimpleNamespace(
                id="rs_stream",
                type="reasoning",
                encrypted_content="encrypted-stream-state",
                summary=[],
            ),
            SimpleNamespace(
                id="fc_stream",
                type="function_call",
                call_id="call_stream",
                name="lookup",
                arguments='{"query":"stream"}',
                status="completed",
            ),
        ],
    )
    stream = iter(
        [
            SimpleNamespace(
                type="response.output_item.added",
                output_index=1,
                item=SimpleNamespace(
                    type="function_call",
                    call_id="call_stream",
                    name="lookup",
                    arguments="",
                ),
            ),
            SimpleNamespace(
                type="response.function_call_arguments.delta",
                output_index=1,
                delta='{"query":',
            ),
            SimpleNamespace(
                type="response.function_call_arguments.delta",
                output_index=1,
                delta='"stream"}',
            ),
            SimpleNamespace(type="response.completed", response=final_response),
        ]
    )
    client = FakeClient([stream])
    backend = OpenAIResponsesBackend(
        connection=connection,
        profile=profile,
        api_key="secret",
        client=client,
    )
    platform = StreamingAIPlatform(prefer_streaming=True)
    platform.register(profile, backend)
    public_events = []
    platform.subscribe_stream(public_events.append)

    result = platform.execute_chat(
        profile.profile_id,
        ChatRequest(
            messages=(AIMessage(role=MessageRole.USER, content="stream"),),
            tools=(_tool(),),
        ),
    )

    assert result.tool_calls == (
        ToolCall(call_id="call_stream", name="lookup", arguments={"query": "stream"}),
    )
    assert result.provider_state[0]["encrypted_content"] == "encrypted-stream-state"
    assert result.usage.total_tokens == 17
    assert all(not hasattr(event, "provider_state") for event in public_events)
    assert client.responses.calls[0]["stream"] is True


def test_runtime_routes_direct_openai_to_responses_and_compatible_to_chat() -> None:
    direct = _connection(ProviderAdapter.OPENAI)
    compatible = ProviderConnection(
        provider_id="test-relay",
        adapter=ProviderAdapter.OPENAI_COMPATIBLE,
        credential_ref=CredentialRef.runtime("relay-key"),
        base_url="https://relay.example.invalid/v1",
    )
    capabilities = frozenset(AGENT_FAST_ROLE.required_capabilities)
    configuration = AIConfiguration.build(
        roles=(AGENT_FAST_ROLE,),
        providers=(direct,),
        bindings=(
            ModelBinding(
                role_id=AGENT_FAST_ROLE.role_id,
                provider_id=direct.provider_id,
                model="gpt-5.6-sol",
                capabilities=capabilities,
            ),
        ),
    )
    direct_client = FakeClient([])
    platform = build_ai_platform(
        configuration,
        credential_resolver=CredentialResolver(runtime_lookup=lambda _alias: "secret"),
        client_factory=lambda *_args: direct_client,
    )
    _profile_value, direct_backend = platform._backend_for(AGENT_FAST_ROLE.role_id)

    relay_profile = ModelProfile(
        profile_id="relay-profile",
        provider=compatible.provider_id,
        model="deepseek-v4-pro",
        capabilities=capabilities,
    )
    relay_backend = OpenAIStreamingChatBackend(
        connection=compatible,
        profile=relay_profile,
        api_key="secret",
        client=object(),
    )

    assert isinstance(direct_backend, OpenAIResponsesBackend)
    assert isinstance(relay_backend, OpenAIStreamingChatBackend)
