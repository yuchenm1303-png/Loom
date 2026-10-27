import pytest
from app.ai import AIMessage, MessageRole, ModelContextLimits, ModelResponse, ToolCall
from app.agent_runtime.contracts import AgentLimits
from app.agent_runtime.context_budget import prepare_context
from app.agent_runtime.context_limits import resolve_context_limits
from app.agent_runtime.context_report import limits_record
from test_context_runtime_v2 import Session, Step, Token
from test_working_context_limits import context_runtime


def test_actual_runtime_defaults_use_declared_million_token_window():
    runtime = context_runtime(ModelContextLimits(context_window_tokens=1_000_000))
    runtime.limits = AgentLimits()
    resolved = resolve_context_limits(runtime, Session([]))
    report = limits_record(resolved.as_dict())
    assert resolved.working_context_tokens is None
    assert report["autoCompactTokens"] > 800_000
    assert report["inputBudgetTokens"] > 900_000


@pytest.mark.parametrize("working", [None, 8000])
def test_soft_budget_compacts_observations_instead_of_hiding_crossing(working):
    runtime = context_runtime(ModelContextLimits(context_window_tokens=100_000,
        working_context_tokens=working, output_reserve_tokens=1000),
        responses=[ModelResponse(text="Summary preserving observed evidence and the user task.")])
    history = [AIMessage(role=MessageRole.USER, content="Inspect all evidence.")]
    for index in range(20):
        call_id = f"call-{index}"
        history.extend([
            AIMessage(role=MessageRole.ASSISTANT, content="", tool_calls=(ToolCall(call_id, "read_file", {}),)),
            AIMessage(role=MessageRole.TOOL, tool_call_id=call_id, name="read_file",
                content=f"evidence {index}: " + "observation " * 400),
        ])
    session = Session(history)
    _, metadata = prepare_context(runtime, session, Step(), Token())
    if working is None:
        assert not runtime.model_executor.requests
        assert not metadata.get("auto_compacted")
        assert metadata["tool_outputs_collapsed"] == 0
        return
    assert metadata["auto_compacted"] is True
    summary_messages = runtime.model_executor.requests[0][1].messages
    for original in history:
        if original.role is MessageRole.TOOL:
            assert original in summary_messages
    assert runtime.commits[0]["archived"] == tuple(history)
