"""Tool-call contract: faults answer their calls, streams and history stay provider-valid."""
from __future__ import annotations

import json
import re
from types import SimpleNamespace

import pytest

from app.agent_runtime import (
    AgentRuntime,
    AgentStatus,
    AgentTool,
    FileAgentSessionStore,
    PermissionMode,
    SandboxManager,
    SandboxPolicy,
    ToolExposure,
    ToolRegistry,
    ToolResult,
    ToolSearchRuntime,
)
from app.agent_runtime.history import repair_tool_history
from app.ai import (
    AGENT_FAST_ROLE,
    AIMessage,
    AIPlatform,
    ChatRequest,
    ModelCapability,
    ModelProfile,
    ModelResponse,
    MessageRole,
    StreamEvent,
    StreamEventKind,
    StreamingAIPlatform,
    ToolCall,
    ToolDefinition,
)
from app.ai.errors import AIResponseError, AIToolContractError
from app.ai.response_protocol import normalize_response
from app.ai.streaming_platform import _StreamAccumulator
from app.ai.tool_declarations import chat_completions_tool, messages_tool, responses_tool
from app.ai.tool_wire import ToolWireCodec

SCHEMA = {"type": "object", "properties": {"value": {"type": "string"}}, "required": ["value"], "additionalProperties": False}
WIRE_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")


# --- streamed tool calls -------------------------------------------------------

def _delta(index, *, call_id="", name="", arguments=""):
    return StreamEvent(kind=StreamEventKind.TOOL_CALL_DELTA, tool_call_index=index,
                       tool_call_id=call_id, tool_name=name, arguments_delta=arguments)


def _accumulate(events, declared=frozenset()):
    accumulator = _StreamAccumulator(declared_names=declared)
    for event in events:
        accumulator.consume(event)
    accumulator.consume(StreamEvent(kind=StreamEventKind.COMPLETED, finish_reason="tool_calls"))
    return accumulator.finalize()


def test_name_snapshots_resolve_to_the_declared_tool_instead_of_doubling():
    events = [_delta(0, call_id="c1", name="browser"), _delta(0, name="browser_eval", arguments="{}")]
    assert [call.name for call in _accumulate(events, {"browser_eval"}).tool_calls] == ["browser_eval"]
    # Without declarations, a growing snapshot is still read as a snapshot.
    assert [call.name for call in _accumulate(events).tool_calls] == ["browser_eval"]


def test_fragments_that_form_a_declared_name_concatenate():
    events = [_delta(0, call_id="c1", name="mcp_"), _delta(0, name="mcp_x", arguments="{}")]
    assert [call.name for call in _accumulate(events, {"mcp_mcp_x"}).tool_calls] == ["mcp_mcp_x"]


def test_a_repeated_complete_argument_object_is_one_object_and_other_repeats_are_rejected():
    collapsed = _accumulate([_delta(0, call_id="c1", name="read", arguments='{"p":1}{"p":1}')])
    assert collapsed.tool_calls[0].arguments == {"p": 1}
    with pytest.raises(AIResponseError):
        _accumulate([_delta(0, call_id="c1", name="read", arguments='{"p":1}{"p":2}')])


def test_a_call_id_reused_at_another_index_stays_a_protocol_violation():
    events = [_delta(0, call_id="same", name="read", arguments="{}"), _delta(1, call_id="same", name="read", arguments="{}")]
    with pytest.raises(AIResponseError, match="reused call id"):
        _accumulate(events)


def test_streaming_platform_resolves_snapshot_names_against_the_request_declarations():
    class Backend:
        def stream(self, wire):
            alias = wire.tools[0].name
            yield _delta(0, call_id="c1", name=alias[:6])
            yield _delta(0, name=alias, arguments='{"value":"x"}')
            yield StreamEvent(kind=StreamEventKind.COMPLETED, finish_reason="tool_calls")

    platform = StreamingAIPlatform(prefer_streaming=True)
    platform.register(ModelProfile(profile_id="test", provider="test", model="test",
                                   capabilities=frozenset({ModelCapability.TEXT, ModelCapability.TOOL_CALLING,
                                                           ModelCapability.STREAMING})), Backend())
    request = ChatRequest(messages=(AIMessage(role=MessageRole.USER, content="go"),),
                          tools=(ToolDefinition("mcp.local.status", "status", SCHEMA),))
    result = platform.execute_chat("test", request)
    assert [(call.name, call.arguments) for call in result.tool_calls] == [("mcp.local.status", {"value": "x"})]


# --- wire identifiers -----------------------------------------------------------

def _assistant(*calls):
    return AIMessage(role=MessageRole.ASSISTANT, content="", tool_calls=tuple(calls))


def _call(call_id, name="read", arguments=None):
    return ToolCall(call_id, name, arguments or {})


def _output(call_id, content="ok", name="read"):
    return AIMessage(role=MessageRole.TOOL, content=content, name=name, tool_call_id=call_id)


def _user(text):
    return AIMessage(role=MessageRole.USER, content=text)


def _shape(messages):
    return [(m.role.value, [c.call_id for c in m.tool_calls] if m.tool_calls else (m.tool_call_id or "-"))
            for m in messages]


def _transport_payload(protocol, wire):
    from app.ai.openai_runtime import OpenAIChatBackend
    from app.ai.opencode_go_runtime import _OpenCodeGoMessagesBackend, _OpenCodeGoResponsesBackend
    from app.ai.provider_catalog import ProviderAdapter
    backend_type = {"chat": OpenAIChatBackend, "responses": _OpenCodeGoResponsesBackend,
                    "messages": _OpenCodeGoMessagesBackend}[protocol]
    backend = object.__new__(backend_type)
    backend.profile = SimpleNamespace(model="test")
    backend.connection = SimpleNamespace(adapter=ProviderAdapter.OPENAI_COMPATIBLE)
    backend.request_timeout_seconds = 30
    if protocol == "chat":
        return json.dumps(backend._request_kwargs(wire)["messages"])
    if protocol == "responses":
        return json.dumps(backend._input(wire))
    return json.dumps(backend._messages(wire)[1])


@pytest.mark.parametrize("protocol", ["chat", "responses", "messages"])
def test_unsafe_call_ids_are_aliased_identically_on_every_transport(protocol):
    original = ChatRequest(messages=(_user("go"), _assistant(_call("functions.read:0")),
                                     _output("functions.read:0")),
                           tools=(ToolDefinition("read", "read", SCHEMA),))
    wire = ToolWireCodec(original).encode(original)
    alias = wire.messages[1].tool_calls[0].call_id
    assert WIRE_ID.fullmatch(alias)
    assert wire.messages[2].tool_call_id == alias
    payload = _transport_payload(protocol, wire)
    assert "functions.read:0" not in payload and alias in payload


def test_a_call_id_alias_decodes_back_to_the_internal_id():
    original = ChatRequest(messages=(_user("go"), _assistant(_call("functions.read:0")),
                                     _output("functions.read:0")),
                           tools=(ToolDefinition("read", "read", SCHEMA),))
    codec = ToolWireCodec(original)
    alias = codec.encode(original).messages[1].tool_calls[0].call_id
    decoded = codec.decode(ModelResponse(tool_calls=(ToolCall(alias, "read", {}),)))
    assert decoded.tool_calls[0].call_id == "functions.read:0"


def test_vendor_declarations_share_one_name_description_and_schema():
    tool = ToolDefinition("read", "Read a file", {"type": "object", "properties": {"path": {"type": "string"}}})
    chat = chat_completions_tool(tool)["function"]
    responses = responses_tool(tool)
    messages = messages_tool(tool)
    assert (chat["name"], chat["description"], chat["parameters"]) == (
        responses["name"], responses["description"], responses["parameters"]) == (
        messages["name"], messages["description"], messages["input_schema"]) == (
        "read", "Read a file", tool.input_schema)


# --- history replay -------------------------------------------------------------

def test_reused_call_ids_across_turns_each_keep_their_own_output():
    messages = [_user("one"), _assistant(_call("call_0")), _output("call_0", "r1"),
                _user("two"), _assistant(_call("call_0", arguments={"p": 2})), _output("call_0", "r2")]
    repaired = repair_tool_history(messages)
    assert not repaired.changed
    assert _shape(repaired.messages) == _shape(messages)
    assert repaired.messages[5].content == "r2"


def test_an_interrupted_call_does_not_take_a_later_reused_id_answer():
    messages = [_user("one"), _assistant(_call("call_0")), _user("two"),
                _assistant(_call("call_0")), _output("call_0", "r2")]
    repaired = repair_tool_history(messages)
    assert _shape(repaired.messages) == [("user", "-"), ("assistant", ["call_0"]), ("tool", "call_0"),
                                         ("user", "-"), ("assistant", ["call_0"]), ("tool", "call_0")]
    assert "outcome_unknown" in repaired.messages[2].content
    assert repaired.messages[5].content == "r2"
    assert repaired.inserted_aborted_outputs == 1


def test_an_output_after_an_intervening_message_moves_next_to_its_call():
    repaired = repair_tool_history([_user("go"), _assistant(_call("a")), _user("steer"), _output("a", "late")])
    assert _shape(repaired.messages) == [("user", "-"), ("assistant", ["a"]), ("tool", "a"), ("user", "-")]
    assert repaired.messages[2].content == "late"


def test_unanswered_calls_are_filled_in_call_order_and_orphans_or_second_answers_are_dropped():
    messages = [_user("go"), _assistant(_call("a"), _call("b")), _output("b", "only-b"),
                _output("zz", "orphan"), _output("b", "second")]
    repaired = repair_tool_history(messages)
    assert _shape(repaired.messages) == [("user", "-"), ("assistant", ["a", "b"]), ("tool", "a"), ("tool", "b")]
    assert repaired.messages[3].content == "only-b"
    assert (repaired.inserted_aborted_outputs, repaired.removed_orphan_outputs,
            repaired.removed_duplicate_outputs) == (1, 1, 1)


def test_a_clean_history_is_returned_unchanged():
    messages = [_user("go"), _assistant(_call("a"), _call("b")), _output("a"), _output("b"), _user("next")]
    repaired = repair_tool_history(messages)
    assert repaired.messages == tuple(messages)
    assert not repaired.changed


def test_messages_protocol_answers_one_turn_of_calls_in_a_single_user_turn():
    from app.ai.opencode_go_runtime import _OpenCodeGoMessagesBackend
    request = ChatRequest(messages=(_user("go"), _assistant(_call("a"), _call("b")),
                                    _output("a", "one"), _output("b", "two"), _user("next")))
    _system, messages = object.__new__(_OpenCodeGoMessagesBackend)._messages(request)
    assert [m["role"] for m in messages] == ["user", "assistant", "user", "user"]
    assert [block["tool_use_id"] for block in messages[2]["content"]] == ["a", "b"]


# --- platform boundary ----------------------------------------------------------

def test_contract_error_carries_the_response_and_call_ids_in_internal_names():
    class Backend:
        def complete(self, wire):
            return ModelResponse(text="", tool_calls=(ToolCall("functions.read:0", "other.tool", {}),),
                                 finish_reason="tool_calls")

    platform = AIPlatform()
    platform.register(ModelProfile(profile_id="test", provider="test", model="test",
                                   capabilities=frozenset({ModelCapability.TEXT, ModelCapability.TOOL_CALLING})),
                      Backend())
    request = ChatRequest(messages=(_user("go"),), tools=(ToolDefinition("a.b", "t", SCHEMA),))
    with pytest.raises(AIToolContractError) as caught:
        platform.execute_chat("test", request)
    assert isinstance(caught.value, AIResponseError)
    assert caught.value.response.tool_calls[0].name == "other.tool"
    assert caught.value.invalid_call_ids == ("functions.read:0",)


# --- runtime recovery -----------------------------------------------------------

class _Scripted:
    def __init__(self, responses, *, through_platform=False):
        self.responses = list(responses)
        self.requests = []
        self.through_platform = through_platform

    def execute_chat(self, _profile_id, request):
        self.requests.append(request)
        if not self.responses:
            raise AssertionError("scripted platform ran out of responses")
        response = self.responses.pop(0)
        return normalize_response(request, response) if self.through_platform else response


def _record(calls, *, name="record_once", exposure=ToolExposure.DIRECT):
    return AgentTool(name=name, description="Record a value", input_schema=SCHEMA, exposure=exposure,
                     handler=lambda context, arguments: (calls.append(arguments["value"])
                                                         or ToolResult(ok=True, content="Saved")))


def _runtime(tmp_path, platform, tools, *, search=False):
    store = FileAgentSessionStore(tmp_path / "state")
    if search:
        return ToolSearchRuntime(platform=platform, store=store, tools=ToolRegistry(tools), mcp_servers=(),
                                 auto_configure_browser=False, auto_configure_web_search=False)
    return AgentRuntime(platform=platform, store=store, tools=ToolRegistry(tools),
                        sandbox_manager=SandboxManager(policy=SandboxPolicy.OFF))


def _tool_names(request):
    return {tool.name for tool in request.tools}


@pytest.mark.parametrize("through_platform", [False, True])
def test_undeclared_calls_are_answered_before_the_recovery_budget_is_exhausted(tmp_path, through_platform):
    calls = []
    responses = [ModelResponse(tool_calls=(ToolCall(f"c{i}", "unknown_record", {"value": "x"}),),
                               finish_reason="tool_calls") for i in range(2)]
    responses.append(ModelResponse(text="Done", finish_reason="stop"))
    platform = _Scripted(responses, through_platform=through_platform)
    runtime = _runtime(tmp_path, platform, (_record(calls),))
    try:
        session = runtime.create_session("agent.fast", workspace_dir=tmp_path)
        assert runtime.start_turn(session.session_id, "Record").status is AgentStatus.COMPLETED
        events = runtime.store.events(session.session_id)
        assert calls == []
        assert not [e for e in events if e.kind.value == "model_response_rejected"]
        assert sum(e.kind.value == "tool_failed" for e in events) == 2
    finally:
        runtime.close()


@pytest.mark.parametrize("through_platform", [False, True])
def test_a_model_that_never_stops_issuing_faulty_calls_ends_the_turn_as_a_bounded_failure(tmp_path, through_platform):
    calls = []
    responses = [ModelResponse(tool_calls=(ToolCall(f"c{i}", "unknown_record", {"value": "x"}),),
                               finish_reason="tool_calls") for i in range(10)]
    platform = _Scripted(responses, through_platform=through_platform)
    runtime = _runtime(tmp_path, platform, (_record(calls),))
    try:
        session = runtime.create_session("agent.fast", workspace_dir=tmp_path)
        result = runtime.start_turn(session.session_id, "Record")
        assert result.status is AgentStatus.LIMIT_REACHED
        assert "no progress" in result.error
        assert calls == [] and len(platform.requests) == 3
    finally:
        runtime.close()


def test_a_repeated_call_in_one_batch_runs_once_and_is_answered_as_a_repeat(tmp_path):
    calls = []
    platform = _Scripted([
        ModelResponse(tool_calls=(ToolCall("first", "record_once", {"value": "one"}),
                                  ToolCall("again", "record_once", {"value": "one"})), finish_reason="tool_calls"),
        ModelResponse(text="Done", finish_reason="stop"),
    ])
    runtime = _runtime(tmp_path, platform, (_record(calls),))
    try:
        session = runtime.create_session("agent.fast", workspace_dir=tmp_path)
        assert runtime.start_turn(session.session_id, "Record").status is AgentStatus.COMPLETED
        assert calls == ["one"]
        again = next(m for m in platform.requests[1].messages
                     if m.role is MessageRole.TOOL and m.tool_call_id == "again")
        assert "not executed again" in again.content and "first" in again.content
        # Every result of the batch directly follows its assistant message, which is what providers require.
        request_messages = platform.requests[1].messages
        assistant_at = next(i for i, m in enumerate(request_messages) if m.tool_calls)
        assert {m.tool_call_id for m in request_messages[assistant_at + 1:assistant_at + 3]} == {"first", "again"}
        durable = {m.tool_call_id for m in runtime.store.load(session.session_id).messages if m.role is MessageRole.TOOL}
        assert durable == {"first", "again"}
    finally:
        runtime.close()


def test_a_deferred_tool_called_without_a_search_executes_in_derived_dispatch_step(tmp_path):
    calls = []
    platform = _Scripted([
        ModelResponse(tool_calls=(ToolCall("direct", "mcp.github.create_issue", {"value": "bug"}),),
                      finish_reason="tool_calls"),
        ModelResponse(text="Done", finish_reason="stop"),
    ])
    runtime = _runtime(tmp_path, platform,
                       (_record(calls, name="mcp.github.create_issue", exposure=ToolExposure.DEFERRED),),
                       search=True)
    try:
        session = runtime.create_session(AGENT_FAST_ROLE.role_id, workspace_dir=tmp_path,
                                         permission_mode=PermissionMode.FULL_ACCESS)
        assert runtime.start_turn(session.session_id, "Create an issue.").status is AgentStatus.COMPLETED
        assert calls == ["bug"]
        assert "mcp.github.create_issue" not in _tool_names(platform.requests[0])
        assert "mcp.github.create_issue" in _tool_names(platform.requests[1])
        assert not any(e.kind.value == "tool_failed" for e in runtime.store.events(session.session_id))
        assert len(platform.requests) == 2
    finally:
        runtime.close()


def test_a_later_turn_that_calls_a_deferred_tool_without_searching_recovers_inside_the_turn(tmp_path):
    calls = []
    platform = _Scripted([
        ModelResponse(tool_calls=(ToolCall("search", "tool_search", {"query": "github create issue", "limit": 1}),),
                      finish_reason="tool_calls"),
        ModelResponse(tool_calls=(ToolCall("first", "mcp.github.create_issue", {"value": "one"}),),
                      finish_reason="tool_calls"),
        ModelResponse(text="first done", finish_reason="stop"),
        ModelResponse(tool_calls=(ToolCall("second-direct", "mcp.github.create_issue", {"value": "two"}),),
                      finish_reason="tool_calls"),
        ModelResponse(text="second done", finish_reason="stop"),
    ])
    runtime = _runtime(tmp_path, platform,
                       (_record(calls, name="mcp.github.create_issue", exposure=ToolExposure.DEFERRED),),
                       search=True)
    try:
        session = runtime.create_session(AGENT_FAST_ROLE.role_id, workspace_dir=tmp_path,
                                         permission_mode=PermissionMode.FULL_ACCESS)
        assert runtime.start_turn(session.session_id, "Create one.").status is AgentStatus.COMPLETED
        assert runtime.start_turn(session.session_id, "Create two.").status is AgentStatus.COMPLETED
        assert calls == ["one", "two"]
        assert "mcp.github.create_issue" not in _tool_names(platform.requests[3])
        assert "mcp.github.create_issue" in _tool_names(platform.requests[4])
    finally:
        runtime.close()
