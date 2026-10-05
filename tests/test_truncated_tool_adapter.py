from types import SimpleNamespace as NS

import pytest

from app.ai.errors import AIResponseError
from app.ai.openai_runtime import _parse_tool_calls
from app.ai.streaming_platform import _StreamAccumulator
from app.ai.contracts import StreamEvent, StreamEventKind


@pytest.mark.parametrize("streaming", [False, True])
def test_length_cutoff_is_typed_before_tool_json_parsing(streaming):
    raw = '{"text":"unfinished'
    if streaming:
        accumulator = _StreamAccumulator()
        accumulator.consume(StreamEvent(kind=StreamEventKind.TOOL_CALL_DELTA,
            tool_call_index=0, tool_call_id="write", tool_name="write_workspace_text", arguments_delta=raw))
        accumulator.consume(StreamEvent(kind=StreamEventKind.COMPLETED, finish_reason="length"))
        invoke = accumulator.finalize
    else:
        message = NS(tool_calls=[NS(id="write", function=NS(name="write_workspace_text", arguments=raw))])
        invoke = lambda: _parse_tool_calls(message, finish_reason="length")
    with pytest.raises(AIResponseError) as caught:
        invoke()
    assert type(caught.value).__name__ == "AITruncatedToolCallError"
    assert caught.value.finish_reason == "length"
    assert caught.value.tool_name == "write_workspace_text"
    assert caught.value.argument_chars == len(raw)


def test_complete_finish_with_bad_json_remains_format_error():
    message = NS(tool_calls=[NS(id="write", function=NS(name="write_workspace_text", arguments='{'))])
    with pytest.raises(AIResponseError) as caught:
        _parse_tool_calls(message)
    assert type(caught.value) is AIResponseError


def test_production_runner_retries_truncation_without_executing_partial_call(tmp_path):
    from app.agent_runtime import AgentRuntime, AgentStatus, FileAgentSessionStore, SandboxManager, SandboxPolicy
    from app.ai import ModelResponse
    from app.ai.errors import AITruncatedToolCallError

    class Platform:
        requests = []

        def execute_chat(self, profile, request):
            self.requests.append(request)
            if len(self.requests) == 1:
                raise AITruncatedToolCallError(finish_reason="length", tool_name="write_workspace_text", argument_chars=19)
            return ModelResponse(text="Recovered")

    platform = Platform()
    runtime = AgentRuntime(platform=platform, store=FileAgentSessionStore(tmp_path / "state"),
                           sandbox_manager=SandboxManager(policy=SandboxPolicy.OFF))
    try:
        session = runtime.create_session("agent.fast", workspace_dir=tmp_path)
        result = runtime.start_turn(session.session_id, "Write")
        assert result.status is AgentStatus.COMPLETED
        assert any("multiple smaller tool calls" in str(m.content) for m in platform.requests[1].messages)
        events = runtime.store.events(session.session_id)
        rejected = next(e for e in events if e.data.get("reason") == "truncated_tool_call")
        assert rejected.data["finish_reason"] == "length"
        assert rejected.data["truncated_tool_name"] == "write_workspace_text"
        assert rejected.data["argument_chars"] == 19
        assert not any(e.kind.value == "tool_started" for e in events)
    finally:
        runtime.close()
