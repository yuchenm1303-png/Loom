from __future__ import annotations

import io
import json
from types import SimpleNamespace as NS
import urllib.error
import threading

import pytest

from app.ai.contracts import AIMessage, ChatRequest, MessageRole, StreamEvent, StreamEventKind, ToolChoice
from app.ai.errors import AIResponseError, AITransportError, AITruncatedToolCallError, AIQuotaExceeded
from app.ai.opencode_go_runtime import _OpenCodeGoResponsesBackend, _OpenCodeGoMessagesBackend
from app.ai.streaming_platform import _StreamAccumulator
from app.ai.responses_protocol import ResponsesToolDecoder
from app.ai.sse import json_sse_events
from app.ai.tool_protocol import parse_tool_call
from app.ai.execution_control import ExecutionControl, ModelCancelled, current_control


REQUEST = ChatRequest(messages=(AIMessage(role=MessageRole.USER, content="Check"),))


class Stream:
    closed = False
    def __init__(self, events):
        self.events = events
    def __iter__(self):
        return iter(self.events)
    def close(self):
        self.closed = True


def responses(events):
    backend = _OpenCodeGoResponsesBackend(profile=NS(model="muse-spark-1.3-contributor"),
                                         api_key="test-only-key", request_timeout_seconds=1)
    stream = Stream(events)
    backend.client = NS(responses=NS(create=lambda **kwargs: stream))
    return backend, stream


def messages(events):
    backend = _OpenCodeGoMessagesBackend(profile=NS(model="minimax-m3"),
                                        api_key="test-only-key", request_timeout_seconds=1)
    stream = Stream(line for event in events
                    for line in [("data: " + json.dumps(event, ensure_ascii=False)).encode(), b"\n"])
    backend._open = lambda *args, **kwargs: stream
    return backend, stream


def collect(backend):
    accumulator = _StreamAccumulator()
    for event in backend.stream(REQUEST):
        accumulator.consume(event)
    return accumulator.finalize(**{key: value for key, value in backend.last_stream_metadata().items()
                                   if key in {"usage", "response_id", "finish_reason", "chunk_count"}})


def call(arguments='{"value":"中文🚀"}', call_id="call-1", item_id="fc-1"):
    return NS(type="function_call", id=item_id, call_id=call_id, name="echo", arguments=arguments)


def terminal(output=(), status="completed", reason=None):
    return NS(type="response.incomplete" if status == "incomplete" else "response.completed",
              response=NS(id="resp-1", status=status, output=output,
                          incomplete_details=NS(reason=reason), usage=NS(input_tokens=10, output_tokens=4)))


def test_responses_done_and_final_snapshots_do_not_duplicate_arguments_or_text():
    item = call()
    backend, stream = responses([
        NS(type="response.output_item.added", output_index=1, item=call(arguments="")),
        NS(type="response.function_call_arguments.delta", output_index=1, item_id="fc-1", delta='{"value":'),
        NS(type="response.function_call_arguments.done", output_index=1, item_id="fc-1", arguments=item.arguments),
        NS(type="response.output_item.done", output_index=1, item=item),
        NS(type="response.output_text.delta", output_index=0, content_index=0, delta="Ready"),
        NS(type="response.output_text.done", output_index=0, content_index=0, text="Ready"),
        terminal([NS(type="message", content=[NS(type="output_text", text="Ready")]), item]),
    ])
    result = collect(backend)
    assert result.text == "Ready"
    assert len(result.tool_calls) == 1
    assert result.tool_calls[0].arguments == {"value": "中文🚀"}
    assert result.usage.input_tokens == 10
    assert stream.closed


def test_responses_terminal_snapshot_can_supply_complete_tool_and_text():
    backend, _ = responses([terminal([call(), NS(type="message", content=[NS(type="output_text", text="Ready")])])])
    result = collect(backend)
    assert result.text == "Ready"
    assert result.tool_calls[0].call_id == "call-1"


@pytest.mark.parametrize("reason,error", [("max_output_tokens", AITruncatedToolCallError),
                                        ("content_filter", AIResponseError)])
def test_responses_incomplete_is_not_a_connection_error(reason, error):
    backend, stream = responses([terminal([call('{"value":')], "incomplete", reason)])
    with pytest.raises(error):
        collect(backend)
    assert stream.closed


@pytest.mark.parametrize("events", [
    [NS(type="response.output_text.delta", delta="partial")],
    [],
])
def test_responses_eof_never_commits_partial_response(events):
    backend, stream = responses(events)
    with pytest.raises(AITransportError):
        collect(backend)
    assert stream.closed


@pytest.mark.parametrize("field,value", [("call_id", "different"), ("id", "other-item")])
def test_responses_rejects_cross_item_identity_collision(field, value):
    decoder = ResponsesToolDecoder()
    decoder.item(call(), 0, snapshot=True)
    other = call()
    setattr(other, field, value)
    with pytest.raises(AIResponseError):
        decoder.item(other, 1, snapshot=True)


def test_responses_rejects_conflicting_argument_snapshot():
    decoder = ResponsesToolDecoder()
    decoder.item(call(arguments=""), 0, snapshot=False)
    decoder.arguments(index=0, value='{"value":1}')
    with pytest.raises(AIResponseError):
        decoder.arguments(index=0, value='{"value":2}', snapshot=True)


@pytest.mark.parametrize("raw", ["[]", "null", "42", '"string"', "{"])
def test_every_protocol_uses_atomic_object_argument_validation(raw):
    with pytest.raises(AIResponseError):
        parse_tool_call("call-1", "echo", raw)


def test_accumulator_rejects_duplicate_call_id_across_indexes():
    accumulator = _StreamAccumulator()
    accumulator.consume(StreamEvent(kind=StreamEventKind.TOOL_CALL_DELTA,
                                    tool_call_index=0, tool_call_id="same", tool_name="echo"))
    with pytest.raises(AIResponseError):
        accumulator.consume(StreamEvent(kind=StreamEventKind.TOOL_CALL_DELTA,
                                        tool_call_index=1, tool_call_id="same", tool_name="echo"))


@pytest.mark.parametrize("stop", ["tool_use", "max_tokens"])
def test_messages_tool_and_output_limit_contract(stop):
    backend, stream = messages([
        {"type": "message_start", "message": {"id": "msg-1"}},
        {"type": "content_block_start", "index": 1,
         "content_block": {"type": "tool_use", "id": "call-1", "name": "echo", "input": {}}},
        {"type": "content_block_delta", "index": 1,
         "delta": {"type": "input_json_delta", "partial_json": '{"value":"中文🚀"}'}},
        {"type": "message_delta", "delta": {"stop_reason": stop}}, {"type": "message_stop"},
    ])
    if stop == "max_tokens":
        with pytest.raises(AITruncatedToolCallError):
            collect(backend)
    else:
        result = collect(backend)
        assert result.end_turn is False
        assert result.tool_calls[0].arguments == {"value": "中文🚀"}
    assert stream.closed


def test_messages_requires_terminal_marker_even_after_stop_reason():
    backend, stream = messages([{"type": "message_delta", "delta": {"stop_reason": "end_turn"}}])
    with pytest.raises(AITransportError):
        collect(backend)
    assert stream.closed


@pytest.mark.parametrize("code,retryable", [("invalid_request_error", False), ("overloaded_error", True)])
def test_messages_stream_errors_have_explicit_retry_contract(code, retryable):
    backend, stream = messages([{"type": "error", "error": {"type": code, "message": "failed"}}])
    with pytest.raises(AITransportError) as caught:
        collect(backend)
    assert caught.value.retryable is retryable
    assert stream.closed


@pytest.mark.parametrize("status,retryable", [(401, False), (402, False), (429, True), (500, True)])
def test_responses_http_rejections_preserve_retry_policy(status, retryable):
    backend, _ = responses([])
    class Rejected(Exception):
        status_code = status
    def reject(**kwargs):
        raise Rejected("rejected")
    backend.client.responses.create = reject
    with pytest.raises(AITransportError) as caught:
        list(backend.stream(REQUEST))
    assert caught.value.status_code == status
    assert caught.value.retryable is retryable


def test_sse_multiline_json_comments_and_crlf():
    assert list(json_sse_events([b": heartbeat\r\n", b"event: message_start\r\n",
        b'data: {"type":\r\n', b'data: "message_start"}\r\n', b"\r\n"])) == [{"type": "message_start"}]


@pytest.mark.parametrize("code", [401, 402, 425, 429, 529])
def test_messages_http_status_has_same_retry_policy_as_responses(monkeypatch, code):
    backend = _OpenCodeGoMessagesBackend(profile=NS(model="minimax-m3"),
                                        api_key="test-only-key", request_timeout_seconds=1)
    def reject(*args, **kwargs):
        raise urllib.error.HTTPError("https://example.invalid", code, "timeout", {}, io.BytesIO(b"{}"))
    monkeypatch.setattr("urllib.request.urlopen", reject)
    with pytest.raises(AITransportError) as caught:
        list(backend.stream(REQUEST))
    assert caught.value.status_code == code
    assert caught.value.retryable is (code in {425, 429, 529})


def test_messages_quota_is_not_retried(monkeypatch):
    backend = _OpenCodeGoMessagesBackend(profile=NS(model="minimax-m3"),
                                        api_key="test-only-key", request_timeout_seconds=1)
    def reject(*args, **kwargs):
        raise urllib.error.HTTPError("https://example.invalid", 429, "limited", {},
            io.BytesIO(b'{"error":{"code":"insufficient_quota"}}'))
    monkeypatch.setattr("urllib.request.urlopen", reject)
    with pytest.raises(AIQuotaExceeded) as caught:
        list(backend.stream(REQUEST))
    assert not caught.value.retryable


@pytest.mark.parametrize("factory", [responses, messages])
def test_cancelled_request_never_opens_connection(factory):
    backend, _ = factory([])
    control = ExecutionControl()
    control.cancel()
    token = current_control.set(control)
    try:
        with pytest.raises(ModelCancelled):
            list(backend.stream(REQUEST))
    finally:
        current_control.reset(token)


@pytest.mark.parametrize("factory", [responses, messages])
def test_closing_partial_stream_closes_transport(factory):
    events = ([NS(type="response.output_text.delta", delta="partial")]
              if factory is responses else
              [{"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": "partial"}}])
    backend, transport = factory(events)
    stream = backend.stream(REQUEST)
    assert next(stream).text_delta == "partial"
    stream.close()
    assert transport.closed


def test_messages_initial_tool_input_is_preserved():
    backend, _ = messages([
        {"type": "content_block_start", "index": 0,
         "content_block": {"type": "tool_use", "id": "call-1", "name": "echo", "input": {"value": 42}}},
        {"type": "message_delta", "delta": {"stop_reason": "tool_use"}}, {"type": "message_stop"},
    ])
    assert collect(backend).tool_calls[0].arguments == {"value": 42}


@pytest.mark.parametrize("raw", [[], None, 0])
def test_messages_nonobject_tool_input_is_rejected(raw):
    backend, transport = messages([{"type": "content_block_start", "index": 0,
        "content_block": {"type": "tool_use", "id": "call-1", "name": "echo", "input": raw}}])
    with pytest.raises(AIResponseError):
        collect(backend)
    assert transport.closed


def test_messages_tool_choice_none_does_not_expose_tools():
    from dataclasses import replace
    from app.ai.contracts import ToolDefinition
    backend, _ = messages([])
    request = replace(REQUEST, tool_choice=ToolChoice.NONE,
                      tools=(ToolDefinition("echo", "Echo", {"type": "object"}),))
    assert "tools" not in backend._payload(request, stream=True)


@pytest.mark.parametrize("streaming", [True, False])
def test_responses_permanent_status_overrides_misleading_message(streaming):
    backend, _ = responses([])
    class Rejected(Exception):
        status_code = 401
    def reject(**kwargs):
        raise Rejected("timeout contacting authentication service")
    backend.client.responses.create = reject
    with pytest.raises(AITransportError) as caught:
        list(backend.stream(REQUEST)) if streaming else backend.complete(REQUEST)
    assert caught.value.retryable is False


@pytest.mark.parametrize("arguments,error", [("[]", AIResponseError), ('{"x":', AIResponseError)])
def test_responses_nonstream_uses_same_tool_validation(arguments, error):
    backend, _ = responses([])
    backend.client.responses.create = lambda **kwargs: terminal([call(arguments)]).response
    with pytest.raises(error):
        backend.complete(REQUEST)


def test_chat_parallel_fragmented_calls_use_shared_atomic_contract():
    from app.ai.openai_streaming import OpenAIStreamingChatBackend
    from app.ai.provider_catalog import ProviderAdapter
    def chunk(calls=(), finish=None):
        return NS(id="chat-1", usage=None, choices=[NS(delta=NS(tool_calls=calls), finish_reason=finish)])
    def tool(index, call_id="", name="", arguments=""):
        return NS(index=index, id=call_id, function=NS(name=name, arguments=arguments))
    stream = Stream([
        chunk([tool(0, "a", "ec", '{"value":"'), tool(1, "b", "echo", '{"value":2}')]),
        chunk([tool(0, name="ho", arguments='中文🚀"}')]), chunk(finish="tool_calls"),
        NS(choices=[], usage=NS(prompt_tokens=10, completion_tokens=4)),
    ])
    backend = object.__new__(OpenAIStreamingChatBackend)
    backend._stream_local = threading.local()
    backend.connection = NS(adapter=ProviderAdapter.OPENAI_COMPATIBLE, provider_id="test")
    backend._request_kwargs = lambda request: {}
    backend._create_stream = lambda kwargs: stream
    result = collect(backend)
    assert [(call.call_id, call.name, call.arguments) for call in result.tool_calls] == [
        ("a", "echo", {"value": "中文🚀"}), ("b", "echo", {"value": 2}),
    ]
    assert result.usage.input_tokens == 10
    assert stream.closed
