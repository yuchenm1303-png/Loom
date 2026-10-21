"""The messages go to the SDK as extra body, not through its typed transform (150-300 ms per step on a long conversation).

The request on the wire must be the one the transform produced, for plain and for streaming requests, with tools,
tool calls, reasoning content, images and an extra body that is already there.
"""
from __future__ import annotations

import http.server
import json
import threading

import pytest

from app.ai import (
    AIMessage, ChatRequest, CredentialRef, ImagePart, MessageRole, ModelCapability, ModelProfile, ProviderAdapter,
    ProviderConnection, TextPart, ToolCall, ToolDefinition,
)
from app.ai.openai_runtime import OpenAIChatBackend, _tool_payload
from app.ai.reasoning import ReasoningKind, ReasoningRequest


class _Capture(http.server.BaseHTTPRequestHandler):
    bodies: list[bytes] = []

    def log_message(self, *args):
        pass

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("content-length", "0")))
        type(self).bodies.append(body)
        if json.loads(body).get("stream"):
            chunk = {"id": "x", "object": "chat.completion.chunk", "created": 0, "model": "m", "choices": [{"index": 0, "delta": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}]}
            payload = ("data: " + json.dumps(chunk) + "\n\ndata: [DONE]\n\n").encode()
            content_type = "text/event-stream"
        else:
            payload = json.dumps({"id": "x", "object": "chat.completion", "created": 0, "model": "m",
                                  "choices": [{"index": 0, "message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}],
                                  "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}}).encode()
            content_type = "application/json"
        self.send_response(200)
        self.send_header("content-type", content_type)
        self.send_header("content-length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


@pytest.fixture()
def server():
    _Capture.bodies = []
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Capture)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}/v1"
    httpd.shutdown()


def _backend(base_url: str) -> OpenAIChatBackend:
    connection = ProviderConnection(provider_id="p", adapter=ProviderAdapter.OPENAI_COMPATIBLE,
                                    credential_ref=CredentialRef.runtime("k"), base_url=base_url, display_name="P")
    profile = ModelProfile(profile_id="p1", provider="p", model="m", capabilities=frozenset({ModelCapability.TEXT}))
    return OpenAIChatBackend(connection=connection, profile=profile, api_key="secret")


def _request(**extra) -> ChatRequest:
    tool = ToolDefinition(name="exec", description="run a command", input_schema={"type": "object", "properties": {"argv": {"type": "array", "items": {"type": "string"}}}, "required": ["argv"]})
    messages = (
        AIMessage(MessageRole.USER, "first question ✓ \"quoted\"\nsecond line"),
        AIMessage(MessageRole.ASSISTANT, "", tool_calls=(ToolCall("call-1", "exec", {"argv": ["echo", "1"]}),), reasoning="because"),
        AIMessage(MessageRole.TOOL, "result", tool_call_id="call-1"),
        AIMessage(MessageRole.USER, (TextPart("look at this"), ImagePart("data:image/png;base64,AAAA"))),
    )
    return ChatRequest(messages=messages, tools=(tool,), max_output_tokens=512, **extra)


def _plain_body(backend: OpenAIChatBackend, request: ChatRequest) -> dict:
    """What the SDK sends for the keyword arguments exactly as built, through its typed transform."""
    before = len(_Capture.bodies)
    kwargs = backend._request_kwargs(request)
    backend.client.chat.completions.create(**kwargs)
    return json.loads(_Capture.bodies[before])


def test_the_body_on_the_wire_is_the_one_the_sdk_transform_produces(server):
    backend = _backend(server)
    request = _request()
    expected = _plain_body(backend, request)
    before = len(_Capture.bodies)
    backend.complete(request)
    sent = json.loads(_Capture.bodies[before])
    assert sent == expected
    assert [m["role"] for m in sent["messages"]] == ["user", "assistant", "tool", "user"]
    assert list(sent) == list(expected)  # same keys, same order


def test_a_streaming_request_carries_the_same_messages(server):
    backend = _backend(server)
    request = _request()
    expected = _plain_body(backend, request)["messages"]
    before = len(_Capture.bodies)
    list(backend.stream(request))
    sent = json.loads(_Capture.bodies[before])
    assert sent["messages"] == expected
    assert sent["stream"] is True


def test_an_extra_body_that_is_already_there_survives(server):
    backend = _backend(server)
    request = _request(reasoning=ReasoningRequest(kind=ReasoningKind.OPENAI_EFFORT, value="high"))
    expected = _plain_body(backend, request)
    before = len(_Capture.bodies)
    backend.complete(request)
    sent = json.loads(_Capture.bodies[before])
    assert sent == expected and sent["reasoning_effort"] == "high"


def test_a_client_handed_in_sees_the_arguments_as_built():
    seen = {}

    class Client:
        class chat:
            class completions:
                @staticmethod
                def create(**kwargs):
                    seen.update(kwargs)
                    raise RuntimeError("stop here")

    connection = ProviderConnection(provider_id="p", adapter=ProviderAdapter.OPENAI_COMPATIBLE,
                                    credential_ref=CredentialRef.runtime("k"), base_url="http://127.0.0.1:9/v1", display_name="P")
    profile = ModelProfile(profile_id="p1", provider="p", model="m", capabilities=frozenset({ModelCapability.TEXT}))
    backend = OpenAIChatBackend(connection=connection, profile=profile, api_key="secret", client=Client())
    with pytest.raises(Exception):
        backend.complete(_request())
    assert len(seen["messages"]) == 4 and "extra_body" not in seen


def test_the_messages_leave_the_typed_arguments_empty_for_the_sdk_client():
    """That is the whole saving: the SDK transforms the argument, not the extra body."""
    connection = ProviderConnection(provider_id="p", adapter=ProviderAdapter.OPENAI_COMPATIBLE,
                                    credential_ref=CredentialRef.runtime("k"), base_url="http://127.0.0.1:9/v1", display_name="P")
    profile = ModelProfile(profile_id="p1", provider="p", model="m", capabilities=frozenset({ModelCapability.TEXT}))
    backend = OpenAIChatBackend(connection=connection, profile=profile, api_key="secret")
    kwargs = backend._request_kwargs(_request(reasoning=ReasoningRequest(kind=ReasoningKind.OPENAI_EFFORT, value="high")))
    wire = backend._wire_kwargs(kwargs)
    assert wire["messages"] == [] and wire["extra_body"]["messages"] == kwargs["messages"]
    assert wire["extra_body"]["reasoning_effort"] == "high" and "messages" not in (kwargs.get("extra_body") or {})
    assert kwargs["messages"], "the caller's arguments are not edited"
