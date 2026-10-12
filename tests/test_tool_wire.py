from dataclasses import replace
import re

import pytest

from app.ai import (AIMessage, AIPlatform, ChatRequest, MessageRole, ModelCapability,
                    ModelProfile, ModelResponse, StreamEvent, StreamEventKind,
                    StreamingAIPlatform, ToolCall, ToolDefinition)
from app.ai.tool_wire import ToolWireCodec
from app.ai.errors import AIResponseError


def request(name="mcp.local.status"):
    return ChatRequest(messages=(
        AIMessage(role=MessageRole.USER, content="Use the tool"),
        AIMessage(role=MessageRole.ASSISTANT, content="", tool_calls=(ToolCall("old", name, {}),)),
        AIMessage(role=MessageRole.TOOL, content="ok", name=name, tool_call_id="old"),
    ), tools=(ToolDefinition(name, "test", {"type": "object"}),))


@pytest.mark.parametrize("name", ["mcp.local.status", "mcp.server.tool", "中文工具", "x" * 200, "a/b c:tool", "safe_tool"])
def test_definition_history_and_result_use_one_reversible_identifier(name):
    original = request(name)
    codec = ToolWireCodec(original)
    wire = codec.encode(original)
    alias = wire.tools[0].name
    assert re.fullmatch(r"[A-Za-z0-9_-]{1,64}", alias)
    assert wire.messages[1].tool_calls[0].name == alias
    assert wire.messages[2].name == alias
    assert wire.messages[2].tool_call_id == "old"
    assert original.tools[0].name == name
    assert codec.decode(ModelResponse(text="", tool_calls=(ToolCall("new", alias, {}),))).tool_calls[0].name == name
    assert ToolWireCodec(original).forward == codec.forward


def test_collisions_reserved_aliases_and_removed_history_tools():
    original = request("a.b")
    alias = ToolWireCodec(original).forward["a.b"]
    original = replace(original, tools=original.tools + (
        ToolDefinition("a_b", "test", {"type": "object"}),
        ToolDefinition(alias, "test", {"type": "object"}),
    ))
    codec = ToolWireCodec(original)
    assert len(set(codec.forward.values())) == 3
    assert codec.forward[alias] == alias
    without_tools = replace(original, tools=())
    assert ToolWireCodec(without_tools).encode(without_tools).messages[1].tool_calls[0].name != "a.b"


class Backend:
    def __init__(self, mode="native"):
        self.mode = mode

    def complete(self, wire):
        alias = wire.tools[0].name
        assert re.fullmatch(r"[A-Za-z0-9_-]{1,64}", alias)
        if self.mode == "dsml":
            return ModelResponse(text=f'<｜DSML｜function_calls><｜DSML｜invoke name="{alias}"></｜DSML｜invoke></｜DSML｜function_calls>', finish_reason="stop")
        return ModelResponse(text="", tool_calls=(ToolCall("new", alias, {}),), finish_reason="tool_calls")

    def stream(self, wire):
        alias = wire.tools[0].name
        if self.mode == "dsml":
            text = self.complete(wire).text
            for char in text:
                yield StreamEvent(kind=StreamEventKind.TEXT_DELTA, text_delta=char)
        else:
            for index, char in enumerate(alias):
                yield StreamEvent(kind=StreamEventKind.TOOL_CALL_DELTA, tool_call_index=0,
                                  tool_call_id="new" if index == 0 else "", tool_name=char)
            yield StreamEvent(kind=StreamEventKind.TOOL_CALL_DELTA, tool_call_index=0, arguments_delta="{}")
        yield StreamEvent(kind=StreamEventKind.COMPLETED, finish_reason="tool_calls")


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("mode", ["native", "dsml"])
def test_platform_normalizes_before_dispatch_for_native_and_textual_calls(streaming, mode):
    platform = StreamingAIPlatform() if streaming else AIPlatform()
    profile = ModelProfile(profile_id="test", provider="test", model="test",
                           capabilities=frozenset({ModelCapability.TEXT, ModelCapability.TOOL_CALLING, ModelCapability.STREAMING}))
    platform.register(profile, Backend(mode))
    result = platform.execute_chat("test", request())
    assert result.tool_calls[0].name == "mcp.local.status"
    assert result.tool_calls[0].arguments == {}


def test_interleaved_split_names_and_repeated_snapshots():
    original = request()
    codec = ToolWireCodec(original)
    alias = codec.forward[original.tools[0].name]
    decoder = codec.stream_decoder()
    emitted = []
    for index in [0, 1]:
        for fragment in [alias[:20], alias[:30], alias[30:], alias]:
            event = decoder.decode(StreamEvent(kind=StreamEventKind.TOOL_CALL_DELTA,
                                               tool_call_index=index, tool_name=fragment))
            if event.tool_name:
                emitted.append((index, event.tool_name))
    assert emitted == [(0, "mcp.local.status"), (1, "mcp.local.status")]


def test_undeclared_wire_tool_still_rejected():
    class Unknown(Backend):
        def complete(self, wire):
            return ModelResponse(text="", tool_calls=(ToolCall("id", "unknown_tool", {}),), finish_reason="tool_calls")
    platform = AIPlatform()
    platform.register(ModelProfile(profile_id="test", provider="test", model="test",
        capabilities=frozenset({ModelCapability.TEXT, ModelCapability.TOOL_CALLING})), Unknown())
    with pytest.raises(AIResponseError, match="undeclared"):
        platform.execute_chat("test", request())


@pytest.mark.parametrize("protocol", ["chat", "responses", "messages"])
def test_real_transport_serializers_use_legal_definition_and_replay_names(protocol):
    from types import SimpleNamespace
    from app.ai.openai_runtime import OpenAIChatBackend
    from app.ai.opencode_go_runtime import _OpenCodeGoResponsesBackend, _OpenCodeGoMessagesBackend
    from app.ai.provider_catalog import ProviderAdapter
    wire = ToolWireCodec(request()).encode(request())
    alias = wire.tools[0].name
    backend_type = {"chat": OpenAIChatBackend, "responses": _OpenCodeGoResponsesBackend,
                    "messages": _OpenCodeGoMessagesBackend}[protocol]
    backend = object.__new__(backend_type)
    backend.profile = SimpleNamespace(model="test")
    backend.connection = SimpleNamespace(adapter=ProviderAdapter.OPENAI_COMPATIBLE)
    backend.request_timeout_seconds = 30
    if protocol == "chat":
        payload = backend._request_kwargs(wire)
        assert payload["tools"][0]["function"]["name"] == alias
        assert payload["messages"][1]["tool_calls"][0]["function"]["name"] == alias
    elif protocol == "responses":
        payload = backend._kwargs(wire)
        assert payload["tools"][0]["name"] == alias
        assert next(item for item in payload["input"] if item.get("type") == "function_call")["name"] == alias
    else:
        payload = backend._payload(wire, stream=True)
        assert payload["tools"][0]["name"] == alias
        assert payload["messages"][1]["content"][0]["name"] == alias
    assert re.fullmatch(r"[A-Za-z0-9_-]{1,64}", alias)


def test_structured_output_schema_identifier_is_normalized_at_the_same_boundary():
    from app.ai import StructuredRequest
    class Structured:
        def complete_structured(self, packet):
            assert re.fullmatch(r"[A-Za-z0-9_-]{1,64}", packet.schema_name)
            assert re.fullmatch(r"[A-Za-z0-9_-]{1,64}", packet.chat.tools[0].name)
            return {"ok": True}
    platform = AIPlatform()
    platform.register(ModelProfile(profile_id="test", provider="test", model="test",
        capabilities=frozenset({ModelCapability.TEXT, ModelCapability.TOOL_CALLING, ModelCapability.STRUCTURED_OUTPUT})), Structured())
    packet = StructuredRequest(chat=request(), json_schema={"type": "object"}, schema_name="中文.schema")
    assert platform.execute_structured_chat("test", packet) == {"ok": True}
    assert packet.schema_name == "中文.schema"


def test_streaming_ids_without_indexes_and_ambiguous_safe_prefixes():
    original = request()
    original = replace(original, tools=original.tools + tuple(
        ToolDefinition(name, "test", {"type": "object"}) for name in ("read", "read_file")))
    decoder = ToolWireCodec(original).stream_decoder()
    for call_id in ("one", "two"):
        name = decoder.decode(StreamEvent(kind=StreamEventKind.TOOL_CALL_DELTA,
                                         tool_call_id=call_id, tool_name="read"))
        assert name.tool_name == ""
        args = decoder.decode(StreamEvent(kind=StreamEventKind.TOOL_CALL_DELTA,
                                         tool_call_id=call_id, arguments_delta="{}"))
        assert args.tool_name == "read"
