from types import SimpleNamespace

import pytest

from app.ai.contracts import AIMessage, ChatRequest, MessageRole, ToolChoice, ToolDefinition
from app.ai.openai_runtime import OpenAIChatBackend
from app.ai.opencode_go_runtime import _OpenCodeGoMessagesBackend, _OpenCodeGoResponsesBackend
from app.ai.provider_catalog import ProviderAdapter


@pytest.mark.parametrize("protocol", ["openai", "compatible", "responses", "messages"])
@pytest.mark.parametrize("choice,with_tools", [
    (ToolChoice.NONE, False), (ToolChoice.NONE, True),
    (ToolChoice.AUTO, False), (ToolChoice.AUTO, True), (ToolChoice.REQUIRED, True),
])
def test_tool_policy_preserves_intent_across_protocols(protocol, choice, with_tools):
    request = ChatRequest(
        messages=(AIMessage(role=MessageRole.USER, content="summarize"),),
        tools=(ToolDefinition(name="read_file", description="Read", input_schema={"type": "object"}),) if with_tools else (),
        tool_choice=choice,
    )
    if protocol in {"openai", "compatible"}:
        backend = object.__new__(OpenAIChatBackend)
        backend.connection = SimpleNamespace(adapter=ProviderAdapter.OPENAI if protocol == "openai" else ProviderAdapter.OPENAI_COMPATIBLE)
        backend.profile = SimpleNamespace(model="future-model")
        backend.request_timeout_seconds = 30
        payload = backend._request_kwargs(request)
    elif protocol == "responses":
        backend = object.__new__(_OpenCodeGoResponsesBackend)
        backend.profile = SimpleNamespace(model="future-model")
        payload = backend._kwargs(request)
    else:
        backend = object.__new__(_OpenCodeGoMessagesBackend)
        backend.profile = SimpleNamespace(model="future-model")
        payload = backend._payload(request, stream=False)
    if choice is ToolChoice.NONE or not with_tools:
        assert "tools" not in payload
        assert "tool_choice" not in payload
    else:
        assert len(payload["tools"]) == 1
        expected = {"type": "any" if choice is ToolChoice.REQUIRED else "auto"} if protocol == "messages" else choice.value
        assert payload["tool_choice"] == expected
