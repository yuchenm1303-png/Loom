from dataclasses import replace
import pytest

from app.ai.contracts import AIMessage, ChatRequest, MessageRole, ModelResponse, ToolCall, ToolChoice, ToolDefinition
from app.ai.errors import AIResponseError, AITruncatedToolCallError
from app.ai.response_protocol import normalize_response, ToolTextBoundary


REQUEST = ChatRequest(messages=(AIMessage(role=MessageRole.USER, content="do the task"),), tools=(
    ToolDefinition(name="exec", description="Execute", input_schema={"type": "object", "properties": {"argv": {"type": "array", "items": {"type": "string"}}}, "required": ["argv"], "additionalProperties": False}),
))
WIRE = '<|DSML|tool_calls><|DSML|invoke name="exec"><|DSML|parameter name="argv" string="false">["cmd", "/c", "echo 中文 & test"]</|DSML|parameter></|DSML|invoke></|DSML|tool_calls>'


@pytest.mark.parametrize("delimiter", ["|", "｜"])
def test_wire_codec_preserves_arguments_and_parallel_calls(delimiter):
    wire = WIRE.replace("|", delimiter)
    body = wire[wire.index("<" + delimiter + "DSML" + delimiter + "invoke"):wire.rindex("</" + delimiter + "DSML" + delimiter + "tool_calls>")]
    wire = wire.replace(body, body + body)
    response = normalize_response(REQUEST, ModelResponse(text="Checking tools. " + wire, finish_reason="stop"))
    assert response.text == "Checking tools."
    assert len(response.tool_calls) == 2
    assert response.tool_calls[0].arguments == {"argv": ["cmd", "/c", "echo 中文 & test"]}
    assert len({call.call_id for call in response.tool_calls}) == 2


def test_every_fragment_boundary_keeps_protocol_private():
    text = "Checking tools. " + WIRE
    for split in range(len(text) + 1):
        boundary = ToolTextBoundary(True)
        public = boundary.feed(text[:split]) + boundary.feed(text[split:]) + boundary.finish()
        assert public == "Checking tools. "
    boundary = ToolTextBoundary(True)
    assert "".join(boundary.feed(char) for char in text) + boundary.finish() == "Checking tools. "


def test_normal_text_and_non_agent_protocol_documentation_are_preserved():
    boundary = ToolTextBoundary(True)
    text = "a < b and <strong>text</strong>"
    assert "".join(boundary.feed(char) for char in text) + boundary.finish() == text
    request = replace(REQUEST, tool_choice=ToolChoice.NONE)
    assert normalize_response(request, ModelResponse(text=WIRE)).text == WIRE


@pytest.mark.parametrize("wire", [
    WIRE[:-1], WIRE.replace('string="false"', 'string="invalid"'),
    WIRE.replace('["cmd", "/c", "echo 中文 & test"]', 'invalid JSON'),
    WIRE.replace('name="exec"', 'name="unknown"'),
    WIRE.replace('name="argv"', 'name="wrong"'),
    WIRE.replace('</|DSML|parameter>', '</|DSML|invoke>'),
    WIRE + WIRE, WIRE + " trailing text",
    '<|DSML', '<tool_call>{"name":"exec"}</tool_call>',
])
def test_malformed_wire_is_rejected_atomically(wire):
    with pytest.raises(AIResponseError):
        normalize_response(REQUEST, ModelResponse(text=wire))


def test_native_and_textual_calls_cannot_be_executed_twice():
    with pytest.raises(AIResponseError, match="mixes"):
        normalize_response(REQUEST, ModelResponse(text=WIRE, tool_calls=(ToolCall("id", "exec", {"argv": []}),)))


@pytest.mark.parametrize("finish", ["length", "max_tokens"])
def test_truncated_calls_never_execute(finish):
    with pytest.raises(AITruncatedToolCallError):
        normalize_response(REQUEST, ModelResponse(text=WIRE, finish_reason=finish))


def test_native_calls_follow_same_declaration_and_completion_contract():
    good = ModelResponse(tool_calls=(ToolCall("id", "exec", {"argv": []}),), finish_reason="tool_calls")
    assert normalize_response(REQUEST, good) == good
    for response in [ModelResponse(finish_reason="tool_calls"), replace(good, tool_calls=(ToolCall("id", "unknown", {}),)), replace(good, tool_calls=(ToolCall("id", "exec", {"argv": "bad"}),))]:
        with pytest.raises(AIResponseError):
            normalize_response(REQUEST, response)
    with pytest.raises(AIResponseError):
        normalize_response(replace(REQUEST, tool_choice=ToolChoice.NONE), good)
    with pytest.raises(AIResponseError):
        normalize_response(replace(REQUEST, tool_choice=ToolChoice.REQUIRED), ModelResponse(text="Done"))


def test_legacy_chat_fields_share_native_validation():
    from types import SimpleNamespace as NS
    from app.ai.openai_runtime import _parse_tool_calls
    from app.ai.tool_protocol import chat_tool_fragments
    function = NS(name="exec", arguments='{"argv": ["echo", "ok"]}')
    message = NS(function_call=function)
    calls = _parse_tool_calls(message, finish_reason="function_call")
    assert normalize_response(REQUEST, ModelResponse(tool_calls=calls, finish_reason="function_call")).tool_calls[0].arguments == {"argv": ["echo", "ok"]}
    assert chat_tool_fragments(NS(function_call=NS(arguments="{}")), legacy_id="stable")[0].id == "stable"
    with pytest.raises(AIResponseError, match="mixes"):
        chat_tool_fragments(NS(tool_calls=[NS(function=function)], function_call=function), legacy_id="stable")


@pytest.mark.parametrize("streaming", [False, True])
def test_platform_boundary_decodes_before_history_or_dispatch(streaming):
    from app.ai import ModelProfile, ModelCapability, StreamEvent, StreamEventKind
    from app.ai.streaming_platform import StreamingAIPlatform, ProviderStreamEventKind
    class Backend:
        def complete(self, request):
            return ModelResponse(text="Checking. " + WIRE, finish_reason="stop")
        def stream(self, request):
            for char in "Checking. " + WIRE:
                yield StreamEvent(kind=StreamEventKind.TEXT_DELTA, text_delta=char)
            yield StreamEvent(kind=StreamEventKind.COMPLETED, finish_reason="stop")
    platform = StreamingAIPlatform(prefer_streaming=streaming)
    profile = ModelProfile(profile_id="test", provider="arbitrary", model="arbitrary", capabilities=frozenset({ModelCapability.TEXT, ModelCapability.TOOL_CALLING, ModelCapability.STREAMING}))
    platform.register(profile, Backend())
    events = []
    platform.subscribe_stream(events.append)
    response = platform.execute_chat("test", REQUEST)
    assert response.text == "Checking."
    assert response.tool_calls[0].name == "exec"
    assert not any("DSML" in event.text_delta for event in events)
    assert not streaming or events[-1].kind is ProviderStreamEventKind.COMPLETED
