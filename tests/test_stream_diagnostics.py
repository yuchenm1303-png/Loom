import json
from dataclasses import replace
from types import SimpleNamespace as NS

import pytest

from app.ai import AGENT_FAST_ROLE, StreamEventKind
from app.ai.streaming_platform import StreamingAIPlatform
from app.ai.stream_diagnostics import ChatStreamDiagnostics
from app.ai.errors import AIResponseError, AITruncatedToolCallError
from test_provider_streaming import RecordingCompletions, _streaming_backend, _profile, _request


def chunk(text="", calls=(), finish=None, reasoning=None):
    return NS(id="response", usage=None, choices=[NS(
        delta=NS(content=text, reasoning_content=reasoning, tool_calls=calls), finish_reason=finish)])


@pytest.mark.parametrize("ending", ["tool_calls", "length"])
def test_rejected_tool_retains_safe_diagnostics_and_finish_reason(ending):
    secret = "credential-never-log"
    parts = ['{"command":"' + secret + '"', ',"wait":']
    recorder = RecordingCompletions([
        chunk(calls=[NS(index=0, id="call", function=NS(name="exec", arguments=parts[0]))]),
        chunk(calls=[NS(index=0, id="", function=NS(name="", arguments=parts[1]))], finish=ending),
    ])
    platform = StreamingAIPlatform(prefer_streaming=True)
    platform.register(_profile(), _streaming_backend(recorder))
    with pytest.raises(AIResponseError) as caught:
        platform.execute_chat(AGENT_FAST_ROLE.role_id, _request())
    error = caught.value
    assert isinstance(error, AITruncatedToolCallError) == (ending == "length")
    assert error.finish_reason == ending
    assert error.argument_chars == len("".join(parts))
    detail = error.tool_argument_diagnostics
    assert detail["argument_fragments"] == 2
    assert detail["max_fragment_chars"] == max(map(len, parts))
    assert detail["tool_count"] == 1
    if ending == "tool_calls":
        assert detail["json_error_position"] == len("".join(parts))
        assert detail["json_error_at_end"] is True
    assert error.stream_diagnostics["tool_argument_fragments"] == 2
    assert error.response_id == "response"
    assert secret not in json.dumps(detail)
    assert secret not in json.dumps(error.stream_diagnostics)


def test_json_diagnostic_reports_interior_error_without_parameter_content():
    from app.ai.tool_protocol import parse_tool_call
    raw = '{"command":"secret", "wait":True}'
    with pytest.raises(AIResponseError) as caught:
        parse_tool_call("call", "exec", raw, finish_reason="tool_calls")
    detail = caught.value.tool_argument_diagnostics
    assert detail["json_error_position"] == raw.index("True")
    assert detail["json_error_at_end"] is False
    assert "secret" not in json.dumps(detail)


def test_rejected_stream_diagnostics_reach_production_events(tmp_path):
    from app.agent_runtime import AgentRuntime, FileAgentSessionStore, SandboxManager, SandboxPolicy
    from app.agent_runtime.builtin_tools import builtin_read_only_tools
    from app.ai import ModelResponse
    recorder = RecordingCompletions([chunk(calls=[NS(
        index=0, id="call", function=NS(name="exec", arguments='{"command":"private",'))],
        finish="tool_calls")])
    streaming = StreamingAIPlatform(prefer_streaming=True)
    streaming.register(_profile(), _streaming_backend(recorder))

    class OnceThenRecover:
        count = 0

        def execute_chat(self, profile, request):
            self.count += 1
            if self.count == 1:
                return streaming.execute_chat(AGENT_FAST_ROLE.role_id, request)
            return ModelResponse(text="Recovered", finish_reason="stop")

    store = FileAgentSessionStore(tmp_path / "sessions")
    runtime = AgentRuntime(platform=OnceThenRecover(), store=store, tools=builtin_read_only_tools(),
                           sandbox_manager=SandboxManager(policy=SandboxPolicy.OFF))
    try:
        session = runtime.create_session("agent.fast", workspace_dir=tmp_path)
        runtime.start_turn(session.session_id, "Inspect")
        events = store.events(session.session_id)
        rejected = next(e.data for e in events if e.kind.value == "model_response_rejected")
        assert rejected["argument_chars"] == len('{"command":"private",')
        assert rejected["tool_argument_diagnostics"]["argument_fragments"] == 1
        assert rejected["stream_diagnostics"]["tool_argument_fragments"] == 1
        assert rejected["response_id"] == "response"
        assert rejected["finish_reason"] == "tool_calls"
        assert "private" not in json.dumps(rejected)
        assert not any(e.kind.value == "tool_started" for e in events)
    finally:
        runtime.close()


@pytest.mark.parametrize("newline_count", [4972, 5058])
def test_failure_shape_preserves_wire_text_and_stop_without_inventing_tools(newline_count):
    chunks = [chunk("Inspect the window.")] + [chunk("\n") for _ in range(newline_count)]
    chunks += [chunk(finish="stop"), NS(choices=[], usage=NS(prompt_tokens=8, completion_tokens=5136, total_tokens=5144))]
    recorder = RecordingCompletions(chunks)
    platform = StreamingAIPlatform(prefer_streaming=True)
    platform.register(_profile(), _streaming_backend(recorder))
    result = platform.execute_chat(AGENT_FAST_ROLE.role_id, replace(_request(), max_output_tokens=None))
    assert result.text == "Inspect the window." + "\n" * newline_count
    assert result.finish_reason == "stop"
    assert result.tool_calls == ()
    assert len(recorder.calls) == 1
    assert "max_tokens" not in recorder.calls[0]
    diagnostics = result.stream_diagnostics
    assert diagnostics["requested_max_output_tokens"] is None
    assert diagnostics["raw_text"] == diagnostics["normalized_text"]
    assert diagnostics["raw_text"]["newlines"] == newline_count
    assert diagnostics["raw_text"]["max_whitespace_run"] == newline_count
    assert diagnostics["tool_fragments"] == 0
    assert diagnostics["finish_markers"] == 1
    assert diagnostics["usage_chunks"] == 1
    assert diagnostics["chunks"] == len(chunks)


def test_tool_after_whitespace_is_not_suppressed_and_diagnostics_do_not_store_content():
    secret = "credential-do-not-log"
    call = lambda args: NS(index=0, id="call", function=NS(name="echo", arguments=args))
    recorder = RecordingCompletions([
        chunk("\n" * 5058, reasoning=secret),
        chunk(calls=[call('{"value":')]),
        chunk(calls=[NS(index=0, id="", function=NS(name="", arguments=json.dumps(secret) + "}"))], finish="tool_calls"),
    ])
    platform = StreamingAIPlatform(prefer_streaming=True)
    platform.register(_profile(), _streaming_backend(recorder))
    result = platform.execute_chat(AGENT_FAST_ROLE.role_id, replace(_request(), max_output_tokens=8192))
    assert result.tool_calls[0].arguments == {"value": secret}
    assert result.stream_diagnostics["tool_argument_fragments"] == 2
    assert result.stream_diagnostics["requested_max_output_tokens"] == recorder.calls[0]["max_tokens"] == 8192
    assert secret not in json.dumps(result.stream_diagnostics)
    assert "echo" not in json.dumps(result.stream_diagnostics)


def test_cumulative_snapshots_are_distinguishable_from_wire_deltas():
    recorder = RecordingCompletions([chunk("A\n"), chunk("A\n\n", finish="stop")])
    backend = _streaming_backend(recorder)
    events = list(backend.stream(_request()))
    assert "".join(e.text_delta for e in events if e.kind is StreamEventKind.TEXT_DELTA) == "A\n\n"
    metrics = backend.last_stream_metadata()["stream_diagnostics"]
    assert metrics["raw_text"]["newlines"] == 3
    assert metrics["normalized_text"]["newlines"] == 2


def test_shape_metadata_is_bounded_and_exposes_ignored_choice_count():
    diagnostics = ChatStreamDiagnostics(None)
    item = chunk("secret")
    item.choices.append(NS(delta=NS(content="other secret"), finish_reason=None))
    for _ in range(10000):
        diagnostics.observe_chunk(item)
    metrics = diagnostics.snapshot()
    assert metrics["multiple_choice_chunks"] == 10000
    assert len(json.dumps(metrics)) < 1000
    assert "secret" not in json.dumps(metrics)


def test_diagnostics_and_actual_request_budget_reach_durable_events(tmp_path):
    from app.agent_runtime import AgentRuntime, FileAgentSessionStore, SandboxManager, SandboxPolicy
    from app.agent_runtime.builtin_tools import builtin_read_only_tools
    from app.ai import ModelResponse
    from test_agent_request_layout import ScriptedPlatform
    metrics = ChatStreamDiagnostics(None).snapshot()
    platform = ScriptedPlatform([ModelResponse(text="Result", finish_reason="stop", stream_diagnostics=metrics)])
    store = FileAgentSessionStore(tmp_path / "sessions")
    runtime = AgentRuntime(platform=platform, store=store, tools=builtin_read_only_tools(),
                           sandbox_manager=SandboxManager(policy=SandboxPolicy.OFF))
    try:
        session = runtime.create_session("agent.fast", workspace_dir=tmp_path)
        runtime.start_turn(session.session_id, "Inspect")
        events = store.events(session.session_id)
        requested = next(e for e in events if e.kind.value == "model_requested")
        response = next(e for e in events if e.kind.value == "model_response")
        assert requested.data["requested_max_output_tokens"] == platform.requests[0].max_output_tokens
        assert response.data["stream_diagnostics"] == metrics
    finally:
        runtime.close()
