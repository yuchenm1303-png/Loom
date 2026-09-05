from __future__ import annotations

import time
from pathlib import Path
from types import SimpleNamespace

from app.agent_runtime import (
    AgentStatus,
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
from app.ai.streaming_platform import (
    ProviderStreamEvent,
    ProviderStreamEventKind,
    StreamingAIPlatform,
)


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
    metadata = backend.last_stream_metadata()
    assert metadata["usage"].total_tokens == 10
    assert metadata["response_id"] == "resp-openai"
    assert metadata["finish_reason"] == "stop"


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
