from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from app.ai.contracts import (
    AIMessage,
    ChatRequest,
    MessageRole,
    ToolChoice,
    ToolDefinition,
    StreamEventKind,
)
from app.ai.reasoning import ReasoningKind, ReasoningRequest
from app.ai.opencode_go_runtime import (
    OPENCODE_GO_USER_AGENT,
    _OpenCodeGoChatBackend,
    _OpenCodeGoMessagesBackend,
    _OpenCodeGoResponsesBackend,
    opencode_go_protocol,
)


@pytest.mark.parametrize(
    ("model", "protocol"),
    [
        ("gpt-5.6-luna", "responses"),
        ("grok-4.7", "responses"),
        ("grok-4.6", "responses"),
        ("grok-4.5", "responses"),
        ("muse-spark-1.3-contributor", "responses"),
        ("minimax-m3", "messages"),
        ("minimax-m2.7", "messages"),
        ("qwen3.8-max", "messages"),
        ("qwen3.7-plus", "messages"),
        ("glm-5.3", "chat-completions"),
        ("kimi-k3", "chat-completions"),
        ("deepseek-v4-pro", "chat-completions"),
        ("mimo-v2.5", "chat-completions"),
    ],
)
def test_opencode_go_protocol_routes_model_families(model: str, protocol: str) -> None:
    assert opencode_go_protocol(model) == protocol


def _request(reasoning: ReasoningRequest | None = None) -> ChatRequest:
    return ChatRequest(
        messages=(AIMessage(role=MessageRole.USER, content="hello"),),
        tools=(
            ToolDefinition(
                name="read_file",
                description="Read a file",
                input_schema={"type": "object", "properties": {"path": {"type": "string"}}},
            ),
        ),
        tool_choice=ToolChoice.AUTO,
        reasoning=reasoning,
        session_id="thread-stable-id",
    )


def test_opencode_chat_backend_sends_coding_agent_identity() -> None:
    backend = object.__new__(_OpenCodeGoChatBackend)
    backend.connection = SimpleNamespace(adapter=SimpleNamespace(value="openai-compatible"))
    # OpenAI runtime compares adapter identity, so use the real enum without
    # constructing a live SDK client.
    from app.ai.provider_catalog import ProviderAdapter

    backend.connection = SimpleNamespace(adapter=ProviderAdapter.OPENAI_COMPATIBLE)
    backend.profile = SimpleNamespace(model="glm-5.3")
    backend.request_timeout_seconds = 120.0

    kwargs = backend._request_kwargs(_request())

    assert kwargs["extra_headers"]["User-Agent"] == OPENCODE_GO_USER_AGENT
    assert kwargs["extra_headers"]["x-opencode-session"] == "thread-stable-id"


def test_opencode_messages_backend_maps_tools_and_session_header() -> None:
    backend = object.__new__(_OpenCodeGoMessagesBackend)
    backend.profile = SimpleNamespace(model="minimax-m3")
    backend.api_key = "test-only-key"
    backend.timeout = 120.0

    request = _request()
    headers = backend._headers(request)
    payload = backend._payload(request, stream=True)

    assert headers["User-Agent"] == OPENCODE_GO_USER_AGENT
    assert headers["x-opencode-session"] == "thread-stable-id"
    assert headers["Authorization"] == "Bearer test-only-key"
    assert payload["model"] == "minimax-m3"
    assert payload["stream"] is True
    assert payload["tools"][0]["name"] == "read_file"
    assert payload["tool_choice"] == {"type": "auto"}


def test_opencode_responses_backend_maps_tools_and_session_header() -> None:
    backend = object.__new__(_OpenCodeGoResponsesBackend)
    backend.profile = SimpleNamespace(model="gpt-5.6-luna")

    kwargs = backend._kwargs(_request())

    assert kwargs["model"] == "gpt-5.6-luna"
    assert kwargs["extra_headers"]["User-Agent"] == OPENCODE_GO_USER_AGENT
    assert kwargs["extra_headers"]["x-opencode-session"] == "thread-stable-id"
    assert kwargs["tools"][0]["name"] == "read_file"
    assert kwargs["input"][0]["role"] == "user"



def test_opencode_responses_sends_luna_reasoning_effort() -> None:
    backend = object.__new__(_OpenCodeGoResponsesBackend)
    backend.profile = SimpleNamespace(model="gpt-5.6-luna")

    kwargs = backend._kwargs(
        _request(ReasoningRequest(ReasoningKind.OPENAI_EFFORT, "xhigh"))
    )

    assert kwargs["reasoning"] == {"effort": "xhigh", "summary": "auto"}


def test_opencode_chat_sends_model_effort_to_compatible_wire() -> None:
    from app.ai.provider_catalog import ProviderAdapter

    backend = object.__new__(_OpenCodeGoChatBackend)
    backend.connection = SimpleNamespace(adapter=ProviderAdapter.OPENAI_COMPATIBLE)
    backend.profile = SimpleNamespace(model="deepseek-v4-flash")
    backend.request_timeout_seconds = 120.0

    kwargs = backend._request_kwargs(
        _request(ReasoningRequest(ReasoningKind.OPENAI_EFFORT, "max"))
    )

    assert kwargs["extra_body"]["reasoning_effort"] == "max"


def test_opencode_messages_sends_minimax_native_thinking_toggle() -> None:
    backend = object.__new__(_OpenCodeGoMessagesBackend)
    backend.profile = SimpleNamespace(model="minimax-m3")
    backend.api_key = "test-only-key"
    backend.timeout = 120.0

    payload = backend._payload(
        _request(ReasoningRequest(ReasoningKind.MINIMAX_THINKING, "adaptive")),
        stream=True,
    )

    assert payload["thinking"] == {"type": "adaptive"}


def test_opencode_messages_sends_qwen38_effort() -> None:
    backend = object.__new__(_OpenCodeGoMessagesBackend)
    backend.profile = SimpleNamespace(model="qwen3.8-max")
    backend.api_key = "test-only-key"
    backend.timeout = 120.0

    payload = backend._payload(
        _request(ReasoningRequest(ReasoningKind.OPENAI_EFFORT, "xhigh")),
        stream=True,
    )

    assert payload["thinking"] == {"type": "enabled"}
    assert payload["output_config"] == {"effort": "xhigh"}


def test_opencode_messages_sends_qwen_budget_presets() -> None:
    backend = object.__new__(_OpenCodeGoMessagesBackend)
    backend.profile = SimpleNamespace(model="qwen3.7-plus")
    backend.api_key = "test-only-key"
    backend.timeout = 120.0

    high = backend._payload(
        _request(ReasoningRequest(ReasoningKind.THINKING_BUDGET, "high")),
        stream=True,
    )
    maximum = backend._payload(
        _request(ReasoningRequest(ReasoningKind.THINKING_BUDGET, "max")),
        stream=True,
    )

    assert high["thinking"] == {"type": "enabled", "budget_tokens": 32_768}
    assert maximum["thinking"] == {"type": "enabled", "budget_tokens": 65_535}



class _FakeMessagesStream:
    def __init__(self, events):
        self._lines = [
            ("data: " + json.dumps(event) + "\n").encode("utf-8")
            for event in events
        ]
        self.closed = False

    def __iter__(self):
        return iter(self._lines)

    def close(self):
        self.closed = True


def test_opencode_responses_streams_reasoning_summary_separately() -> None:
    backend = _OpenCodeGoResponsesBackend(
        profile=SimpleNamespace(model="gpt-5.6-luna"),
        api_key="test-only-key",
        request_timeout_seconds=1.0,
    )
    events = [
        SimpleNamespace(
            type="response.reasoning_summary_text.delta",
            delta="Checked the repository. ",
        ),
        SimpleNamespace(
            type="response.reasoning_summary_text.delta",
            delta="Found the relevant path.",
        ),
        SimpleNamespace(type="response.output_text.delta", delta="Done."),
        SimpleNamespace(
            type="response.completed",
            response=SimpleNamespace(
                id="resp-1",
                status="completed",
                usage=SimpleNamespace(input_tokens=7, output_tokens=5),
            ),
        ),
    ]
    backend.client = SimpleNamespace(
        responses=SimpleNamespace(create=lambda **_kwargs: iter(events))
    )

    streamed = list(backend.stream(_request()))

    assert [
        event.reasoning_delta
        for event in streamed
        if event.kind is StreamEventKind.REASONING_DELTA
    ] == ["Checked the repository. ", "Found the relevant path."]
    assert [
        event.text_delta
        for event in streamed
        if event.kind is StreamEventKind.TEXT_DELTA
    ] == ["Done."]


def test_opencode_messages_streams_thinking_delta_separately() -> None:
    backend = _OpenCodeGoMessagesBackend(
        profile=SimpleNamespace(model="minimax-m3"),
        api_key="test-only-key",
        request_timeout_seconds=1.0,
    )
    response = _FakeMessagesStream(
        [
            {
                "type": "message_start",
                "message": {
                    "id": "msg-1",
                    "usage": {"input_tokens": 4, "output_tokens": 0},
                },
            },
            {
                "type": "content_block_start",
                "index": 0,
                "content_block": {"type": "thinking", "thinking": ""},
            },
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "thinking_delta", "thinking": "Inspecting files. "},
            },
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "thinking_delta", "thinking": "Root cause found."},
            },
            {
                "type": "content_block_start",
                "index": 1,
                "content_block": {"type": "text", "text": ""},
            },
            {
                "type": "content_block_delta",
                "index": 1,
                "delta": {"type": "text_delta", "text": "Fixed."},
            },
            {
                "type": "message_delta",
                "delta": {"stop_reason": "end_turn"},
                "usage": {"output_tokens": 8},
            },
            {"type": "message_stop"},
        ]
    )
    backend._open = lambda _request, *, stream: response

    streamed = list(backend.stream(_request()))

    assert [
        event.reasoning_delta
        for event in streamed
        if event.kind is StreamEventKind.REASONING_DELTA
    ] == ["Inspecting files. ", "Root cause found."]
    assert [
        event.text_delta
        for event in streamed
        if event.kind is StreamEventKind.TEXT_DELTA
    ] == ["Fixed."]
    assert response.closed is True
