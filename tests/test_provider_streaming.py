from __future__ import annotations

import time
from pathlib import Path
from types import SimpleNamespace

import httpx
import openai
import pytest

from app.agent_runtime import (
    AgentStatus,
    AgentEventKind,
    AgentStreamEvent,
    AgentStreamEventKind,
    FileAgentSessionStore,
    PermissionMode,
    StreamingAgentRuntime,
    ToolRegistry,
)
from app.app_server_streaming import StreamingLoomAppServerService, StreamingLoomRpcController
from app.web_ui_streaming import StreamingLoomWebService
from app.ai import (
    AGENT_FAST_ROLE,
    AIMessage,
    ChatRequest,
    CredentialRef,
    ImagePart,
    MessageRole,
    ModelCapability,
    ModelProfile,
    ModelResponse,
    ModelUsage,
    ProviderAdapter,
    ProviderConnection,
    StreamEvent,
    StreamEventKind,
    ToolDefinition,
)
from app.ai.openai_streaming import OpenAIStreamingChatBackend
from app.ai.openai_runtime import _message_payload, _retryable_provider_error
from app.ai.errors import AIEmptyResponseError, AITransportError
from app.ai.streaming_platform import (
    ProviderStreamEvent,
    ProviderStreamEventKind,
    StreamingAIPlatform,
)
from app.agent_runtime.streaming_runtime import _ModelStreamContext


class FakeStreamBackend:
    def __init__(self) -> None:
        self.complete_calls = 0
        self.stream_calls = 0

    def complete(self, _request):
        self.complete_calls += 1
        return ModelResponse(text="legacy")

    def stream(self, _request):
        self.stream_calls += 1
        yield StreamEvent(kind=StreamEventKind.TEXT_DELTA, text_delta="Hel")
        yield StreamEvent(kind=StreamEventKind.TEXT_DELTA, text_delta="lo")
        yield StreamEvent(
            kind=StreamEventKind.TOOL_CALL_DELTA,
            tool_call_index=0,
            tool_call_id="call-1",
            tool_name="echo",
            arguments_delta='{"value":',
        )
        yield StreamEvent(
            kind=StreamEventKind.TOOL_CALL_DELTA,
            tool_call_index=0,
            arguments_delta='"ok"}',
        )
        yield StreamEvent(kind=StreamEventKind.COMPLETED, finish_reason="tool_calls")

    def last_stream_metadata(self):
        return {
            "usage": ModelUsage(input_tokens=7, output_tokens=4, total_tokens=11),
            "response_id": "resp-1",
            "finish_reason": "tool_calls",
        }


class NormalizedStreamingPlatform:
    """Small Runtime-facing platform that emits provider-normalized deltas."""

    def __init__(self) -> None:
        self.listeners = []
        self.streaming_enabled = False

    def enable_streaming(self):
        self.streaming_enabled = True

    def subscribe_stream(self, listener):
        self.listeners.append(listener)

    def execute_chat(self, profile_id, _request):
        assert self.streaming_enabled is True
        for listener in tuple(self.listeners):
            listener(
                ProviderStreamEvent(
                    profile_id=profile_id,
                    kind=ProviderStreamEventKind.TEXT_DELTA,
                    text_delta="Hel",
                )
            )
            listener(
                ProviderStreamEvent(
                    profile_id=profile_id,
                    kind=ProviderStreamEventKind.TEXT_DELTA,
                    text_delta="lo",
                )
            )
            listener(
                ProviderStreamEvent(
                    profile_id=profile_id,
                    kind=ProviderStreamEventKind.COMPLETED,
                    finish_reason="stop",
                    response_id="resp-runtime",
                    usage=ModelUsage(input_tokens=5, output_tokens=2, total_tokens=7),
                )
            )
        return ModelResponse(
            text="Hello",
            finish_reason="stop",
            response_id="resp-runtime",
            usage=ModelUsage(input_tokens=5, output_tokens=2, total_tokens=7),
        )


class RecordingCompletions:
    def __init__(self, chunks) -> None:
        self.chunks = list(chunks)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(dict(kwargs))
        return iter(self.chunks)


class TransparentPlatformWrapper:
    """Mirror Loom's transparent runtime platform adapters."""

    def __init__(self, delegate) -> None:
        self._delegate = delegate

    def __getattr__(self, name):
        return getattr(self._delegate, name)


class ProviderFailure(Exception):
    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code


class FailingCompletions:
    def __init__(self, error: Exception) -> None:
        self.error = error
        self.calls = 0

    def create(self, **_kwargs):
        self.calls += 1
        raise self.error


class DisconnectingCompletions:
    """A stream that dies part-way through its body, as a dropped SSE response does."""

    def __init__(self, chunks, error: BaseException) -> None:
        self.chunks = list(chunks)
        self.error = error
        self.calls = 0

    def create(self, **_kwargs):
        self.calls += 1

        def stream():
            for chunk in self.chunks:
                yield chunk
            raise self.error

        return stream()


def _profile() -> ModelProfile:
    return ModelProfile(
        profile_id=AGENT_FAST_ROLE.role_id,
        provider="test-provider",
        model="test-model",
        capabilities=frozenset(
            {
                ModelCapability.TEXT,
                ModelCapability.TOOL_CALLING,
                ModelCapability.STREAMING,
            }
        ),
    )


def _request() -> ChatRequest:
    return ChatRequest(
        messages=(AIMessage(role=MessageRole.USER, content="hello"),),
        tools=(
            ToolDefinition(
                name="echo",
                description="echo text",
                input_schema={
                    "type": "object",
                    "properties": {"value": {"type": "string"}},
                    "required": ["value"],
                },
            ),
        ),
    )


def test_openai_native_payload_preserves_auto_image_detail():
    message = AIMessage(
        role=MessageRole.USER,
        content=(ImagePart("data:image/png;base64,AA"),),
    )

    payload = _message_payload(message)

    assert payload["content"][0]["image_url"] == {
        "url": "data:image/png;base64,AA",
        "detail": "auto",
    }


def test_compatible_provider_omits_auto_but_preserves_explicit_image_detail():
    connection = ProviderConnection(
        provider_id="test-provider",
        adapter=ProviderAdapter.OPENAI_COMPATIBLE,
        credential_ref=CredentialRef.runtime("test-key"),
        base_url="https://example.invalid/v1",
    )
    backend = OpenAIStreamingChatBackend(
        connection=connection,
        profile=_profile(),
        api_key="secret-for-test-only",
        client=SimpleNamespace(chat=SimpleNamespace(completions=RecordingCompletions([]))),
    )
    request = ChatRequest(messages=(AIMessage(
        role=MessageRole.USER,
        content=(
            ImagePart("data:image/png;base64,AA"),
            ImagePart("data:image/png;base64,BB", detail="high"),
        ),
    ),))

    payload = backend._request_kwargs(request)["messages"][0]["content"]

    assert payload[0]["image_url"] == {"url": "data:image/png;base64,AA"}
    assert payload[1]["image_url"] == {
        "url": "data:image/png;base64,BB",
        "detail": "high",
    }


def test_text_only_request_sends_explicit_tool_choice_none_to_compatible_provider():
    backend = OpenAIStreamingChatBackend(
        connection=ProviderConnection(
            provider_id="test-provider",
            adapter=ProviderAdapter.OPENAI_COMPATIBLE,
            credential_ref=CredentialRef.runtime("test-key"),
            base_url="https://example.invalid/v1",
        ),
        profile=_profile(),
        api_key="secret-for-test-only",
        client=SimpleNamespace(chat=SimpleNamespace(completions=RecordingCompletions([]))),
    )
    request = ChatRequest(
        messages=(AIMessage(role=MessageRole.USER, content="summarize"),),
        tools=(),
        tool_choice="none",
    )

    kwargs = backend._request_kwargs(request)

    assert "tools" not in kwargs
    assert kwargs["tool_choice"] == "none"


def test_permanent_provider_rejection_preserves_non_retryable_classification():
    completions = FailingCompletions(ProviderFailure(402, "Insufficient Balance"))
    backend = OpenAIStreamingChatBackend(
        connection=ProviderConnection(
            provider_id="test-provider",
            adapter=ProviderAdapter.OPENAI_COMPATIBLE,
            credential_ref=CredentialRef.runtime("test-key"),
            base_url="https://example.invalid/v1",
        ),
        profile=_profile(),
        api_key="secret-for-test-only",
        client=SimpleNamespace(chat=SimpleNamespace(completions=completions)),
    )

    with pytest.raises(AITransportError) as failure:
        backend.complete(_request())

    assert failure.value.retryable is False
    assert completions.calls == 1


def _streaming_backend(completions) -> OpenAIStreamingChatBackend:
    return OpenAIStreamingChatBackend(
        connection=ProviderConnection(
            provider_id="test-provider",
            adapter=ProviderAdapter.OPENAI_COMPATIBLE,
            credential_ref=CredentialRef.runtime("test-key"),
            base_url="https://example.invalid/v1",
        ),
        profile=_profile(),
        api_key="secret-for-test-only",
        client=SimpleNamespace(chat=SimpleNamespace(completions=completions)),
    )


def test_peer_closing_the_stream_mid_body_is_retryable():
    # The exact failure a relay produces when it drops a long answer: no status
    # code, no error body, and a message that matches none of the retryable
    # fragments. Classified by text it reads as a permanent rejection and ends
    # the turn, which is how one blip used to kill a 90-minute run.
    completions = DisconnectingCompletions(
        [
            SimpleNamespace(
                id="resp-dropped",
                usage=None,
                choices=[
                    SimpleNamespace(
                        delta=SimpleNamespace(content="half an ans", reasoning_content=None, tool_calls=[]),
                        finish_reason=None,
                    )
                ],
            )
        ],
        httpx.RemoteProtocolError(
            "peer closed connection without sending complete message body "
            "(incomplete chunked read)"
        ),
    )

    with pytest.raises(AITransportError) as failure:
        list(_streaming_backend(completions).stream(_request()))

    assert failure.value.retryable is True
    assert "RemoteProtocolError" in str(failure.value)


def test_sdk_wrapped_connection_failure_is_retryable():
    # The SDK hides the httpx failure inside its own type, so the classification
    # has to follow the explicit cause chain to find the transport underneath.
    http_request = httpx.Request("POST", "https://example.invalid/v1/chat/completions")
    try:
        try:
            raise httpx.ConnectError("connection reset by peer", request=http_request)
        except httpx.ConnectError as cause:
            raise openai.APIConnectionError(request=http_request) from cause
    except openai.APIConnectionError as exc:
        wrapped = exc

    assert _retryable_provider_error(wrapped) is True


def test_locally_malformed_request_is_not_retryable():
    # Loom built this request badly; resending builds it badly again.
    assert _retryable_provider_error(httpx.LocalProtocolError("Illegal header value")) is False
    assert _retryable_provider_error(httpx.UnsupportedProtocol("Request URL has no scheme")) is False


def _runtime(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = FileAgentSessionStore(tmp_path / "home")
    platform = NormalizedStreamingPlatform()
    runtime = StreamingAgentRuntime(
        platform=platform,
        store=store,
        tools=ToolRegistry(),
        mcp_servers=(),
        auto_configure_browser=False,
        auto_configure_web_search=False,
    )
    return runtime, store, platform, workspace


def test_streaming_runtime_subscribes_once_through_transparent_wrappers(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = FileAgentSessionStore(tmp_path / "home")
    base = NormalizedStreamingPlatform()
    first = TransparentPlatformWrapper(base)
    runtime = StreamingAgentRuntime(
        platform=first,
        store=store,
        tools=ToolRegistry(),
        mcp_servers=(),
        auto_configure_browser=False,
        auto_configure_web_search=False,
    )
    observed = []
    runtime.subscribe_stream(observed.append)

    # BrowserRuntime adds another transparent adapter after StreamingAgentRuntime
    # has already subscribed the underlying platform. Reconfiguring through the
    # new wrapper must not register the same provider listener a second time.
    second = TransparentPlatformWrapper(first)
    runtime._configure_streaming_platform(second)
    assert len(base.listeners) == 1

    binding = runtime._stream_context.set(
        _ModelStreamContext("session", "turn", "step", AGENT_FAST_ROLE.role_id)
    )
    try:
        base.execute_chat(AGENT_FAST_ROLE.role_id, _request())
    finally:
        runtime._stream_context.reset(binding)
        runtime.close()

    assert [
        event.data["delta"]
        for event in observed
        if event.kind is AgentStreamEventKind.ASSISTANT_TEXT_DELTA
    ] == ["Hel", "lo"]


def test_internal_model_scope_never_emits_user_facing_stream(tmp_path):
    runtime, _store, platform, _workspace = _runtime(tmp_path)
    observed = []
    runtime.subscribe_stream(observed.append)
    stale = _ModelStreamContext("session", "turn", "step", AGENT_FAST_ROLE.role_id)
    binding = runtime._stream_context.set(stale)
    try:
        with runtime._internal_model_stream_scope():
            platform.execute_chat(AGENT_FAST_ROLE.role_id, _request())
    finally:
        runtime._stream_context.reset(binding)
        runtime.close()

    assert observed == []


def _wait_idle(service: StreamingLoomAppServerService, session_id: str, timeout: float = 2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not service._is_active(session_id):
            return
        time.sleep(0.01)
    raise AssertionError("streaming app-server operation did not become idle")


def test_streaming_platform_accumulates_text_tool_arguments_and_metadata():
    backend = FakeStreamBackend()
    platform = StreamingAIPlatform(prefer_streaming=True)
    platform.register(_profile(), backend)
    observed = []
    platform.subscribe_stream(observed.append)

    result = platform.execute_chat(AGENT_FAST_ROLE.role_id, _request())

    assert result.text == "Hello"
    assert result.tool_calls[0].call_id == "call-1"
    assert result.tool_calls[0].name == "echo"
    assert result.tool_calls[0].arguments == {"value": "ok"}
    assert result.usage.total_tokens == 11
    assert result.response_id == "resp-1"
    assert result.finish_reason == "tool_calls"
    assert [event.text_delta for event in observed if event.kind is ProviderStreamEventKind.TEXT_DELTA] == [
        "Hel",
        "lo",
    ]
    assert observed[-1].kind is ProviderStreamEventKind.COMPLETED
    assert backend.stream_calls == 1
    assert backend.complete_calls == 0


def test_streaming_platform_opt_out_keeps_legacy_completion_path():
    backend = FakeStreamBackend()
    platform = StreamingAIPlatform(prefer_streaming=False)
    platform.register(_profile(), backend)

    result = platform.execute_chat(AGENT_FAST_ROLE.role_id, _request())

    assert result.text == "legacy"
    assert backend.complete_calls == 1
    assert backend.stream_calls == 0


def test_openai_streaming_backend_requests_usage_and_never_emits_reasoning_content():
    chunks = [
        SimpleNamespace(
            id="resp-openai",
            usage=None,
            choices=[
                SimpleNamespace(
                    delta=SimpleNamespace(content="Hel", reasoning_content="private", tool_calls=[]),
                    finish_reason=None,
                )
            ],
        ),
        SimpleNamespace(
            id="resp-openai",
            usage=None,
            choices=[
                SimpleNamespace(
                    delta=SimpleNamespace(content="lo", reasoning_content="still-private", tool_calls=[]),
                    finish_reason="stop",
                )
            ],
        ),
        SimpleNamespace(
            id="resp-openai",
            choices=[],
            usage=SimpleNamespace(prompt_tokens=8, completion_tokens=2, total_tokens=10),
        ),
    ]
    completions = RecordingCompletions(chunks)
    client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    connection = ProviderConnection(
        provider_id="test-provider",
        adapter=ProviderAdapter.OPENAI_COMPATIBLE,
        credential_ref=CredentialRef.runtime("test-key"),
        base_url="https://example.invalid/v1",
    )
    backend = OpenAIStreamingChatBackend(
        connection=connection,
        profile=_profile(),
        api_key="secret-for-test-only",
        client=client,
    )

    events = list(backend.stream(_request()))

    assert [event.text_delta for event in events if event.kind is StreamEventKind.TEXT_DELTA] == [
        "Hel",
        "lo",
    ]
    assert all("private" not in event.text_delta for event in events)
    assert events[-1].kind is StreamEventKind.COMPLETED
    assert completions.calls[0]["stream"] is True
    assert completions.calls[0]["stream_options"] == {"include_usage": True}
    reasoning_deltas = [
        event.reasoning_delta
        for event in events
        if event.kind is StreamEventKind.REASONING_DELTA
    ]
    assert reasoning_deltas == ["private", "still-private"]
    metadata = backend.last_stream_metadata()
    assert metadata["usage"].total_tokens == 10
    assert metadata["response_id"] == "resp-openai"
    assert metadata["finish_reason"] == "stop"


def test_openai_compatible_cumulative_snapshots_are_normalized_to_real_deltas():
    chunks = [
        SimpleNamespace(
            id="resp-cumulative",
            usage=None,
            choices=[SimpleNamespace(
                delta=SimpleNamespace(content="403", reasoning_content="私", tool_calls=[]),
                finish_reason=None,
            )],
        ),
        SimpleNamespace(
            id="resp-cumulative",
            usage=None,
            choices=[SimpleNamespace(
                delta=SimpleNamespace(content="403 出来了", reasoning_content="私有", tool_calls=[]),
                finish_reason=None,
            )],
        ),
        SimpleNamespace(
            id="resp-cumulative",
            usage=None,
            choices=[SimpleNamespace(
                delta=SimpleNamespace(
                    content="403 出来了 —— 认证信息",
                    reasoning_content="私有思考",
                    tool_calls=[],
                ),
                finish_reason="stop",
            )],
        ),
        SimpleNamespace(
            id="resp-cumulative",
            choices=[],
            usage=SimpleNamespace(prompt_tokens=8, completion_tokens=5, total_tokens=13),
        ),
    ]
    completions = RecordingCompletions(chunks)
    backend = OpenAIStreamingChatBackend(
        connection=ProviderConnection(
            provider_id="test-provider",
            adapter=ProviderAdapter.OPENAI_COMPATIBLE,
            credential_ref=CredentialRef.runtime("test-key"),
            base_url="https://example.invalid/v1",
        ),
        profile=_profile(),
        api_key="secret-for-test-only",
        client=SimpleNamespace(chat=SimpleNamespace(completions=completions)),
    )

    events = list(backend.stream(_request()))

    text_deltas = [
        event.text_delta
        for event in events
        if event.kind is StreamEventKind.TEXT_DELTA
    ]
    assert text_deltas == ["403", " 出来了", " —— 认证信息"]
    assert "".join(text_deltas) == "403 出来了 —— 认证信息"
    reasoning_deltas = [
        event.reasoning_delta
        for event in events
        if event.kind is StreamEventKind.REASONING_DELTA
    ]
    assert reasoning_deltas == ["私", "有", "思考"]
    assert "".join(reasoning_deltas) == "私有思考"
    metadata = backend.last_stream_metadata()
    assert metadata["reasoning"] == "私有思考"
    assert metadata["reasoning_char_count"] == len("私有思考")


def test_openai_compatible_equal_repeated_deltas_are_preserved():
    chunks = [
        SimpleNamespace(
            id="resp-repeat",
            usage=None,
            choices=[SimpleNamespace(
                delta=SimpleNamespace(content="哈", reasoning_content=None, tool_calls=[]),
                finish_reason=None,
            )],
        ),
        SimpleNamespace(
            id="resp-repeat",
            usage=None,
            choices=[SimpleNamespace(
                delta=SimpleNamespace(content="哈", reasoning_content=None, tool_calls=[]),
                finish_reason="stop",
            )],
        ),
    ]
    completions = RecordingCompletions(chunks)
    backend = OpenAIStreamingChatBackend(
        connection=ProviderConnection(
            provider_id="test-provider",
            adapter=ProviderAdapter.OPENAI_COMPATIBLE,
            credential_ref=CredentialRef.runtime("test-key"),
            base_url="https://example.invalid/v1",
        ),
        profile=_profile(),
        api_key="secret-for-test-only",
        client=SimpleNamespace(chat=SimpleNamespace(completions=completions)),
    )

    events = list(backend.stream(_request()))

    assert [
        event.text_delta
        for event in events
        if event.kind is StreamEventKind.TEXT_DELTA
    ] == ["哈", "哈"]

def test_reasoning_only_stream_is_classified_without_exposing_reasoning():
    chunks = [
        SimpleNamespace(
            id="resp-reasoning-only",
            usage=None,
            choices=[SimpleNamespace(
                delta=SimpleNamespace(content=None, reasoning_content="private chain", tool_calls=[]),
                finish_reason="stop",
            )],
        ),
        SimpleNamespace(
            id="resp-reasoning-only",
            choices=[],
            usage=SimpleNamespace(prompt_tokens=9, completion_tokens=3, total_tokens=12),
        ),
    ]
    completions = RecordingCompletions(chunks)
    backend = OpenAIStreamingChatBackend(
        connection=ProviderConnection(
            provider_id="test-provider",
            adapter=ProviderAdapter.OPENAI_COMPATIBLE,
            credential_ref=CredentialRef.runtime("test-key"),
            base_url="https://example.invalid/v1",
        ),
        profile=_profile(),
        api_key="secret-for-test-only",
        client=SimpleNamespace(chat=SimpleNamespace(completions=completions)),
    )
    platform = StreamingAIPlatform(prefer_streaming=True)
    platform.register(_profile(), backend)

    try:
        platform.execute_chat(AGENT_FAST_ROLE.role_id, _request())
    except AIEmptyResponseError as exc:
        assert exc.reasoning_char_count == len("private chain")
        assert exc.chunk_count == 2
        assert exc.finish_reason == "stop"
        assert exc.response_id == "resp-reasoning-only"
        assert exc.total_tokens == 12
        assert "private chain" not in str(exc)
    else:  # pragma: no cover - protects the privacy boundary explicitly
        raise AssertionError("reasoning-only completion must not become a public response")


def test_runtime_stream_bus_is_transient_and_final_message_is_atomic(tmp_path: Path):
    runtime, store, _platform, workspace = _runtime(tmp_path)
    observed = []
    runtime.subscribe_stream(observed.append)
    session = runtime.create_session(
        AGENT_FAST_ROLE.role_id,
        workspace_dir=workspace,
        permission_mode=PermissionMode.WORKSPACE,
    )

    result = runtime.start_turn(session.session_id, "say hello")

    assert result.status is AgentStatus.COMPLETED
    assert result.final_text == "Hello"
    deltas = [event for event in observed if event.kind is AgentStreamEventKind.ASSISTANT_TEXT_DELTA]
    assert [event.data["delta"] for event in deltas] == ["Hel", "lo"]
    assert all(event.session_id == session.session_id for event in observed)
    assert all(event.turn_id == result.turn_id for event in observed)
    assert all(event.step_id for event in observed)
    loaded = store.load(session.session_id)
    assistant_messages = [message for message in loaded.messages if message.role is MessageRole.ASSISTANT]
    assert len(assistant_messages) == 1
    assert assistant_messages[0].content == "Hello"
    assert all("assistant_text_delta" not in event.kind.value for event in store.events(session.session_id))
    runtime.close()


def test_checkpoint_context_buffers_provider_text_until_sanitized_commit(tmp_path: Path):
    runtime, _store, platform, workspace = _runtime(tmp_path)
    observed = []
    runtime.subscribe_stream(observed.append)
    session = runtime.create_session(
        AGENT_FAST_ROLE.role_id,
        workspace_dir=workspace,
        permission_mode=PermissionMode.WORKSPACE,
    )
    session.messages.append(AIMessage(
        role=MessageRole.SYSTEM,
        name="loom_compaction",
        content="private checkpoint summary",
    ))
    session.current_turn_id = "turn"
    runtime._record(session, AgentEventKind.MODEL_REQUESTED, data={
        "step_id": "step",
        "profile_id": AGENT_FAST_ROLE.role_id,
        "attempt": 0,
    })

    platform.execute_chat(AGENT_FAST_ROLE.role_id, _request())

    assert not any(
        event.kind is AgentStreamEventKind.ASSISTANT_TEXT_DELTA
        for event in observed
    )
    runtime.close()


def test_app_server_emits_multiple_real_text_deltas_without_duplicate_final_chunk(tmp_path: Path):
    runtime, store, _platform, workspace = _runtime(tmp_path)
    service = StreamingLoomAppServerService(
        runtime=runtime,
        store=store,
        model="test-model",
        default_workspace=workspace,
        default_permission_mode=PermissionMode.WORKSPACE,
    )
    observed = []
    service.subscribe_notifications(lambda method, params: observed.append((method, params)))
    created = service.thread_start(
        {"workspace": str(workspace), "permissionMode": PermissionMode.WORKSPACE.value}
    )
    session_id = created["thread"]["id"]

    started = service.turn_start({"threadId": session_id, "input": "say hello"})
    turn_id = started["turn"]["id"]
    _wait_idle(service, session_id)

    text_deltas = [
        params["delta"]["text"]
        for method, params in observed
        if method == "item/delta" and "text" in params.get("delta", {})
    ]
    assert text_deltas == ["Hel", "lo"]
    completed = [
        params["item"]
        for method, params in observed
        if method == "item/completed" and params.get("item", {}).get("type") == "assistant_message"
    ]
    assert len(completed) == 1
    assert completed[0]["id"].startswith("assistant:step:")
    assert completed[0]["text"] == "Hello"

    read = service.thread_read({"threadId": session_id})
    turn = next(item for item in read["turns"] if item["id"] == turn_id)
    assistant = next(item for item in turn["items"] if item["type"] == "assistant_message")
    assert assistant["id"] == completed[0]["id"]
    assert assistant["text"] == "Hello"

    controller = StreamingLoomRpcController(service)
    initialized = controller.handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": 1, "clientInfo": {"name": "test"}},
        }
    )
    assert initialized["result"]["capabilities"]["providerStreaming"] is True
    assert initialized["result"]["capabilities"]["runtimeStream"]["privateReasoning"] is False
    runtime.close()


def test_web_snapshot_exposes_transient_partial_assistant_without_persisting_it(tmp_path: Path):
    runtime, store, _platform, workspace = _runtime(tmp_path)
    service = StreamingLoomWebService(
        runtime=runtime,
        store=store,
        model="test-model",
        default_workspace=workspace,
        default_permission_mode=PermissionMode.WORKSPACE,
    )
    session = runtime.create_session(
        AGENT_FAST_ROLE.role_id,
        workspace_dir=workspace,
        permission_mode=PermissionMode.WORKSPACE,
    )
    with service._guard:
        service._active_sessions.add(session.session_id)
    service._on_runtime_stream(
        AgentStreamEvent(
            session_id=session.session_id,
            turn_id="turn-1",
            step_id="step-1",
            kind=AgentStreamEventKind.ASSISTANT_TEXT_DELTA,
            created_at="2026-09-05T00:00:00.000+00:00",
            data={"delta": "partial"},
        )
    )

    snapshot = service.snapshot(session.session_id)

    assert snapshot["streaming"]["enabled"] is True
    assert snapshot["streaming"]["active"] is True
    assert snapshot["messages"][-1]["content"] == "partial"
    assert snapshot["messages"][-1]["streaming"] is True
    assert "#stream-1" in snapshot["session"]["updated_at"]
    persisted = store.load(session.session_id)
    assert persisted.messages == []
    with service._guard:
        service._active_sessions.discard(session.session_id)
    runtime.close()



def test_streaming_platform_accumulates_visible_reasoning_separately_from_answer():
    chunks = [
        SimpleNamespace(
            id="resp-reasoning",
            usage=None,
            choices=[
                SimpleNamespace(
                    delta=SimpleNamespace(
                        content=None,
                        reasoning_content="Inspecting ",
                        tool_calls=[],
                    ),
                    finish_reason=None,
                )
            ],
        ),
        SimpleNamespace(
            id="resp-reasoning",
            usage=None,
            choices=[
                SimpleNamespace(
                    delta=SimpleNamespace(
                        content="Done.",
                        reasoning_content="the code.",
                        tool_calls=[],
                    ),
                    finish_reason="stop",
                )
            ],
        ),
    ]
    backend = _streaming_backend(RecordingCompletions(chunks))
    platform = StreamingAIPlatform(prefer_streaming=True)
    platform.register(_profile(), backend)
    observed = []
    platform.subscribe_stream(observed.append)

    result = platform.execute_chat(AGENT_FAST_ROLE.role_id, _request())

    assert result.text == "Done."
    assert result.visible_reasoning == "Inspecting the code."
    assert [
        event.reasoning_delta
        for event in observed
        if event.kind is ProviderStreamEventKind.REASONING_DELTA
    ] == ["Inspecting ", "the code."]
    assert [
        event.text_delta
        for event in observed
        if event.kind is ProviderStreamEventKind.TEXT_DELTA
    ] == ["Done."]



def test_official_openai_replay_reasoning_is_not_published_as_visible_reasoning():
    chunks = [
        SimpleNamespace(
            id="resp-openai-private",
            usage=None,
            choices=[
                SimpleNamespace(
                    delta=SimpleNamespace(
                        content="Answer.",
                        reasoning_content="replay-only state",
                        tool_calls=[],
                    ),
                    finish_reason="stop",
                )
            ],
        )
    ]
    completions = RecordingCompletions(chunks)
    backend = OpenAIStreamingChatBackend(
        connection=ProviderConnection(
            provider_id="test-provider",
            adapter=ProviderAdapter.OPENAI,
            credential_ref=CredentialRef.runtime("test-key"),
        ),
        profile=_profile(),
        api_key="secret-for-test-only",
        client=SimpleNamespace(chat=SimpleNamespace(completions=completions)),
    )

    events = list(backend.stream(_request()))

    assert not any(event.kind is StreamEventKind.REASONING_DELTA for event in events)
    assert backend.last_stream_metadata()["reasoning"] == "replay-only state"
