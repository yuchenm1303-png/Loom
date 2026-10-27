from dataclasses import replace
import json

import pytest

from app.ai import ModelResponse, ToolCall
from app.ai.errors import AIConfigurationError, AIResponseError
from app.ai.response_protocol import normalize_response
from app.ai.tool_protocol import parse_tool_call
from app.agent_runtime.tools import ToolRequestError, validate_tool_arguments
from test_response_protocol import REQUEST


def test_undeclared_tool_diagnosis_retains_name_complete_catalog_and_response_identity():
    tools = tuple(replace(REQUEST.tools[0], name=f"tool_{i}") for i in range(20))
    request = replace(REQUEST, tools=tools)
    response = ModelResponse(tool_calls=(ToolCall("one", "browser_eval", {"secret": "private-value"}),),
        finish_reason="tool_calls", response_id="reply", stream_diagnostics={"chunk_count": 3})
    with pytest.raises(AIResponseError, match="undeclared") as caught:
        normalize_response(request, response)
    error = caught.value
    assert error.tool_name == "browser_eval"
    assert error.response_id == "reply"
    assert error.stream_diagnostics == {"chunk_count": 3}
    detail = error.tool_argument_diagnostics
    assert detail["tool_name"] == "browser_eval"
    assert detail["issues"][0]["available_tools"] == [tool.name for tool in tools]
    assert "private-value" not in json.dumps(detail)


@pytest.mark.parametrize("arguments,rule,path", [
    ({}, "required", "$.argv"),
    ({"argv": "private-value"}, "type", "$.argv"),
    ({"argv": [42]}, "type", "$.argv[0]"),
    ({"argv": [], "private-key": "private-value"}, "additionalProperties", "$"),
])
def test_provider_boundary_and_dispatch_share_value_free_diagnosis(arguments, rule, path):
    response = ModelResponse(tool_calls=(ToolCall("one", "exec", arguments),), finish_reason="tool_calls", response_id="reply")
    with pytest.raises(AIResponseError) as caught:
        normalize_response(REQUEST, response)
    error = caught.value
    detail = error.tool_argument_diagnostics["issues"][0]
    assert detail["validation_rule"] == rule
    assert detail["argument_path"] == path
    assert error.tool_name == "exec"
    assert error.response_id == "reply"
    assert error.finish_reason == "tool_calls"
    assert "private" not in str(error) + json.dumps(error.tool_argument_diagnostics)
    with pytest.raises(ToolRequestError) as dispatch:
        validate_tool_arguments(REQUEST.tools[0].input_schema, arguments)
    assert dispatch.value.details["validation_rule"] == rule
    assert dispatch.value.details["argument_path"] == path


@pytest.mark.parametrize("raw", [
    '{"argv":[],"argv":["private-value"]}',
    '{"argv":[1e999]}', '{"argv":[NaN]}', '{"argv":[Infinity]}', '{"argv":[-Infinity]}',
    '{"argv":["unescaped "quote""]}', '["not-an-object"]',
])
def test_ambiguous_json_is_not_silently_repaired_or_executed(raw):
    with pytest.raises(AIResponseError) as caught:
        parse_tool_call("one", "exec", raw)
    assert caught.value.tool_name == "exec"
    assert "private-value" not in json.dumps(caught.value.tool_argument_diagnostics)


def test_nested_and_escaped_command_content_remains_exact():
    arguments = {"argv": ["powershell", "-Command", 'Write-Output "C:\\new\\file"; $x = "中文\n<tool_call>"']}
    call = parse_tool_call("one", "exec", json.dumps(arguments, ensure_ascii=False))
    assert call.arguments == arguments
    assert normalize_response(REQUEST, ModelResponse(tool_calls=(call,))).tool_calls == (call,)


def test_invalid_declaration_is_a_configuration_error_not_a_model_repair():
    broken = replace(REQUEST, tools=(replace(REQUEST.tools[0], input_schema={"type": "invented"}),))
    with pytest.raises(AIConfigurationError):
        normalize_response(broken, ModelResponse(tool_calls=(ToolCall("one", "exec", {}),)))


@pytest.mark.parametrize("undeclared", [False, True])
def test_schema_repair_feedback_is_actionable_and_a_valid_predecessor_keeps_its_progress(tmp_path, undeclared):
    from app.agent_runtime import AgentRuntime, AgentStatus, AgentTool, FileAgentSessionStore, ToolRegistry, ToolResult, SandboxManager, SandboxPolicy
    from app.ai import MessageRole
    calls, requests = [], []
    schema = {"type": "object", "properties": {"value": {"type": "string"}}, "required": ["value"], "additionalProperties": False}
    tool = AgentTool(name="record_once", description="Record once", input_schema=schema,
        handler=lambda context, arguments: (calls.append(arguments["value"]) or ToolResult(ok=True, content="Saved")))
    class Platform:
        def execute_chat(self, profile, request):
            requests.append(request)
            if len(requests) == 1:
                response = ModelResponse(tool_calls=(ToolCall("valid-before-the-fault", "record_once", {"value": "kept"}),
                    ToolCall("invalid", "unknown_record" if undeclared else "record_once", {"value": ["private-value"]})), finish_reason="tool_calls")
            elif len(requests) == 2:
                response = ModelResponse(tool_calls=(ToolCall("corrected", "record_once", {"value": "once"}),), finish_reason="tool_calls")
            else:
                response = ModelResponse(text="Done", finish_reason="stop")
            return normalize_response(request, response)
    runtime = AgentRuntime(platform=Platform(), store=FileAgentSessionStore(tmp_path / "state"),
        tools=ToolRegistry((tool,)), sandbox_manager=SandboxManager(policy=SandboxPolicy.OFF))
    try:
        session = runtime.create_session("agent.fast", workspace_dir=tmp_path)
        assert runtime.start_turn(session.session_id, "Record").status is AgentStatus.COMPLETED
        # A valid call ahead of the fault runs once and is answered; only the faulty call is sent back for repair,
        # and the model is not asked to replay the member that succeeded.
        assert calls == ["kept", "once"]
        # Each call of the batch is answered by its own correlated result.
        results = {m.tool_call_id: m for m in requests[1].messages if m.role is MessageRole.TOOL}
        assert set(results) == {"valid-before-the-fault", "invalid"}
        assert "Saved" in results["valid-before-the-fault"].content
        feedback = json.loads(results["invalid"].content)
        assert feedback["data"]["execution_status"] == "not_executed"
        if undeclared:
            assert feedback["data"]["error_code"] == "tool_not_available"
            assert "unknown_record" in feedback["content"]
        else:
            assert feedback["data"]["argument_path"] == "$.value" and feedback["data"]["expected_type"] == "string"
        assert "private-value" not in results["invalid"].content
        events = runtime.store.events(session.session_id)
        assert not [e for e in events if e.kind.value == "model_response_rejected"]
        assert {e.data["call_id"] for e in events if e.kind.value == "tool_failed"} == {"invalid"}
    finally:
        runtime.close()


@pytest.mark.parametrize("protocol", ["chat", "responses", "messages"])
@pytest.mark.parametrize("bad", [False, True])
def test_all_stream_protocols_share_atomic_json_validation(protocol, bad):
    from types import SimpleNamespace as NS
    from app.ai import StreamEvent, StreamEventKind
    from app.ai.streaming_platform import _StreamAccumulator
    from app.ai.responses_protocol import ResponsesToolDecoder
    from test_protocol_stream_contracts import messages, collect
    arguments = {"argv": ["powershell", "-Command", 'Write-Output "中文\nC:\\temp"']}
    raw = json.dumps(arguments, ensure_ascii=False)
    if bad:
        raw = '{"argv": [], "argv": ["private-value"]}'
    def assemble():
        if protocol == "messages":
            backend, stream = messages([
                {"type": "content_block_start", "index": 0, "content_block": {"type": "tool_use", "id": "one", "name": "exec", "input": {}}},
                *[{"type": "content_block_delta", "index": 0, "delta": {"type": "input_json_delta", "partial_json": char}} for char in raw],
                {"type": "message_delta", "delta": {"stop_reason": "tool_use"}},
                {"type": "message_stop"},
            ])
            return collect(backend)
        accumulator = _StreamAccumulator()
        if protocol == "responses":
            decoder = ResponsesToolDecoder()
            events = decoder.item(NS(id="fc", call_id="one", name="exec", arguments=""), 0, snapshot=False)
            for char in raw:
                events += decoder.arguments(index=0, value=char)
            events += decoder.item(NS(id="fc", call_id="one", name="exec", arguments=raw), 0, snapshot=True)
        else:
            events = [StreamEvent(kind=StreamEventKind.TOOL_CALL_DELTA, tool_call_index=0, tool_call_id="one", tool_name="exec")]
            events += [StreamEvent(kind=StreamEventKind.TOOL_CALL_DELTA, tool_call_index=0, arguments_delta=char) for char in raw]
        for event in events:
            accumulator.consume(event)
        accumulator.consume(StreamEvent(kind=StreamEventKind.COMPLETED, finish_reason="tool_calls"))
        return accumulator.finalize()
    if bad:
        with pytest.raises(AIResponseError):
            assemble()
    else:
        result = normalize_response(REQUEST, assemble())
        assert result.tool_calls[0].arguments == arguments
        assert len(result.tool_calls) == 1


def test_sse_and_direct_dictionary_inputs_cannot_hide_ambiguous_numbers_or_keys():
    from app.ai.sse import json_sse_events
    from app.ai.tool_schema import argument_issues
    with pytest.raises(AIResponseError):
        list(json_sse_events([b'data: {"input":{"argv":[],"argv":["private-value"]}}', b'']))
    assert argument_issues({"type": "object"}, {"number": float("nan")})[0]["validation_rule"] == "json"


def test_composite_schema_reports_branch_fields_without_rejected_values():
    from app.ai.tool_schema import argument_issues
    schema = {"type": "object", "properties": {"value": {"anyOf": [{"type": "string"}, {"type": "integer"}]}}}
    issues = argument_issues(schema, {"value": ["private-value"]})
    assert [issue["validation_rule"] for issue in issues] == ["anyOf", "type", "type"]
    assert all(issue["argument_path"] == "$.value" for issue in issues)
    assert "private-value" not in json.dumps(issues)
