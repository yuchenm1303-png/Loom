from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.ai.contracts import (
    AIMessage,
    ChatRequest,
    MessageRole,
    ToolChoice,
    ToolDefinition,
)
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


def _request() -> ChatRequest:
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
