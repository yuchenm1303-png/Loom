from types import SimpleNamespace

import pytest

from app.agent_runtime.execution_progress import unresolved_operation_loop


def event(kind, tool="browser_state", **data):
    return SimpleNamespace(kind=kind, data={"tool": tool, "data": data})


def unresolved(status="unknown", tool="browser_switch_tab"):
    return event("tool_failed", tool, failure_kind="browser_operation_unresolved",
        operation_id="original-op", execution_status=status)


def test_original_timeout_and_blocked_observation_are_not_two_attempts():
    events = [unresolved(), unresolved("not_executed", "browser_state")]
    assert unresolved_operation_loop(events) is None
    assert unresolved_operation_loop(events + [events[-1]] * 10) is None


@pytest.mark.parametrize("recovery", [
    event("tool_completed", effect="changed"),
    event("turn_diff_updated"),
])
def test_later_progress_settles_previous_failure_sequence(recovery):
    assert unresolved_operation_loop([unresolved(), unresolved(), recovery]) is None


def test_unchanged_observation_threshold_is_evaluated_after_recovery():
    unchanged = event("tool_completed", browser_id="b", effect="unchanged")
    changed = event("tool_completed", browser_id="b", effect="changed")
    assert unresolved_operation_loop([unchanged] * 3) == "unchanged:b"
    assert unresolved_operation_loop([unchanged] * 3 + [changed]) is None


def test_real_repeated_execution_failures_still_stop():
    assert unresolved_operation_loop([unresolved(), unresolved()]) == "original-op"


def test_invalid_requests_still_have_their_own_budget():
    invalid = event("tool_failed", "exec", invalid_request_fingerprint="bad-schema")
    assert unresolved_operation_loop([invalid] * 2) is None
    assert unresolved_operation_loop([invalid] * 3) == "schema:bad-schema"


def test_later_failure_after_progress_starts_a_new_sequence():
    changed = event("tool_completed", effect="changed")
    assert unresolved_operation_loop([unresolved()] * 2 + [changed, unresolved()]) is None


def test_turn_survives_timeout_then_blocked_recovery_observation(tmp_path):
    from app.ai import ModelResponse, ToolCall
    from app.agent_runtime import AgentStatus, AgentTool, ToolResult
    from app.agent_runtime.browser_session import BrowserOperationError
    from test_execution_efficiency import Scripted, make_runtime

    calls = []
    def switch(context, args):
        calls.append("switch")
        raise BrowserOperationError("original operation pending", operation_id="original-op", resource="b")
    def observe(context, args):
        calls.append("observe")
        if calls.count("observe") == 1:
            raise BrowserOperationError("no new input sent", operation_id="original-op",
                resource="b", status="not_executed")
        return ToolResult(True, "Fresh browser observation", data={"effect": "changed", "browser_id": "b"})

    platform = Scripted([
        ModelResponse(tool_calls=(ToolCall("switch", "browser_switch_tab", {}),)),
        ModelResponse(tool_calls=(ToolCall("observe-1", "browser_state", {}),)),
        ModelResponse(tool_calls=(ToolCall("observe-2", "browser_state", {}),)),
        ModelResponse(text="Recovered; existing work is preserved."),
    ])
    schema = {"type": "object"}
    runtime = make_runtime(tmp_path, platform, [
        AgentTool("browser_switch_tab", "switch", schema, switch),
        AgentTool("browser_state", "observe", schema, observe),
    ])
    try:
        session = runtime.create_session("agent.fast", workspace_dir=tmp_path)
        assert runtime.start_turn(session.session_id, "Inspect").status is AgentStatus.COMPLETED
        assert calls == ["switch", "observe", "observe"]
    finally:
        runtime.close()
