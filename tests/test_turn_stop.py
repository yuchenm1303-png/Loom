from __future__ import annotations

import threading
import json

import pytest

from app.ai import ModelResponse, ModelUsage, ToolCall
from app.agent_runtime import AgentStatus, DurableAgentRuntime, FileAgentSessionStore
from app.agent_runtime.contracts import AgentLimits
from app.agent_runtime.tools import AgentTool, ToolRegistry, ToolResult
from app.agent_runtime.turn_stop import STOP_TOOL, parse_stop_decision

pytestmark = pytest.mark.real_stop_hook


def decision(outcome="completed", **overrides):
    data = dict(outcome=outcome, reason="Assessment based on request and execution history",
                remaining_tasks=[] if outcome == "completed" else ["Produce the requested report"],
                evidence=["The candidate contains the requested answer"],
                next_action="" if outcome == "completed" else "Generate the report from existing evidence")
    data.update(overrides)
    return ModelResponse(tool_calls=(ToolCall("review", STOP_TOOL.name, data),),
                         finish_reason="tool_calls", usage=ModelUsage(3, 2, 5))


class Scripted:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def execute_chat(self, profile_id, request):
        self.requests.append(request)
        item = self.responses.pop(0)
        if callable(item):
            return item(request)
        if isinstance(item, Exception):
            raise item
        return item


def runtime(tmp_path, responses, **kwargs):
    platform = Scripted(responses)
    rt = DurableAgentRuntime(platform=platform, store=FileAgentSessionStore(tmp_path / "state"), **kwargs)
    session = rt.create_session("agent.fast", workspace_dir=tmp_path)
    return rt, session, platform


@pytest.mark.parametrize("candidate", [
    "等一下。", "我会继续按已批准的测试计划推进剩余 A 端补测。",
    "Received isolated runtime observations; remaining verification follows.",
    "检查完成，全部通过。",  # An unsupported success claim is also only a candidate.
    "剩余材料随后补齐。",  # No verb from the removed promise regex.
])
def test_stop_is_candidate_and_review_continues_same_turn(tmp_path, candidate):
    rt, session, platform = runtime(tmp_path, [
        ModelResponse(text=candidate, finish_reason="stop", usage=ModelUsage(1, 1, 2)),
        decision("continue"), ModelResponse(text="最终报告：全部测试结果及失败原因。"), decision(),
    ])
    try:
        result = rt.start_turn(session.session_id, "跑完全部测试并生成报告")
        assert result.status is AgentStatus.COMPLETED
        assert result.final_text.startswith("最终报告")
        messages = rt.store.load(session.session_id).messages
        assert all(m.content != candidate for m in messages)
        assert platform.requests[1].purpose == "stop_review"
        assert [t.name for t in platform.requests[1].tools] == [STOP_TOOL.name]
        assert "跑完全部测试并生成报告" in str(platform.requests[1].messages)
        continuation = next(m for m in platform.requests[2].messages if m.name == "loom_turn_continuation")
        assert "Generate the report" in continuation.content
        events = rt.store.events(session.session_id)
        assert sum(e.kind.value == "turn_started" for e in events) == 1
        assert sum(e.kind.value == "turn_completed" for e in events) == 1
        terminal = next(e for e in events if e.kind.value == "turn_completed")
        assert terminal.data["stop_decision"]["outcome"] == "completed"
        assert rt.store.load(session.session_id).usage.total_tokens == 12
        assert not any(m.tool_calls and m.tool_calls[0].name == STOP_TOOL.name for m in messages)
    finally:
        rt.close()


@pytest.mark.parametrize("outcome", ["completed", "blocked", "needs_input"])
def test_legitimate_turn_end_is_explicit_and_durable(tmp_path, outcome):
    rt, session, platform = runtime(tmp_path, [ModelResponse(text="Specific result or missing input."), decision(outcome)])
    try:
        result = rt.start_turn(session.session_id, "检查服务")
        assert result.status is AgentStatus.COMPLETED
        assert len(platform.requests) == 2
        events = rt.store.events(session.session_id)
        assert events[-1].data["stop_decision"]["outcome"] == outcome
    finally:
        rt.close()


def test_text_only_continuations_are_not_a_completion_or_retry_limit(tmp_path):
    responses = [item for _ in range(12) for item in (ModelResponse(text="Working."), decision("continue"))]
    responses.extend([ModelResponse(text="Requested report"), decision()])
    rt, session, platform = runtime(tmp_path, responses)
    try:
        result = rt.start_turn(session.session_id, "Generate report")
        assert result.status is AgentStatus.COMPLETED
        assert result.final_text == "Requested report"
        assert len(platform.requests) == 26
        events = rt.store.events(session.session_id)
        assert sum(e.kind.value == "turn_completed" for e in events) == 1
        assert not any(e.kind.value == "limit_reached" for e in events)
        assert all(m.content != "Working." for m in rt.store.load(session.session_id).messages)
    finally:
        rt.close()


@pytest.mark.parametrize("outcome", ["blocked", "needs_input"])
def test_repeated_continuations_can_end_with_an_evidenced_incomplete_result(tmp_path, outcome):
    responses = [item for _ in range(6) for item in (ModelResponse(text="Working."), decision("continue"))]
    responses.extend([ModelResponse(text="Specific blocker or essential missing user input."), decision(outcome)])
    rt, session, _ = runtime(tmp_path, responses)
    try:
        result = rt.start_turn(session.session_id, "Generate report")
        assert result.status is AgentStatus.COMPLETED
        assert result.final_text == "Specific blocker or essential missing user input."
        event = rt.store.events(session.session_id)[-1]
        assert event.kind.value == "turn_completed"
        assert event.data["stop_decision"]["outcome"] == outcome
        assert event.data["stop_decision"]["remaining_tasks"]
    finally:
        rt.close()


def test_cancel_after_repeated_continuations_stops_before_another_request(tmp_path):
    responses = [item for _ in range(6) for item in (ModelResponse(text="Working."), decision("continue"))]
    rt, session, platform = runtime(tmp_path, responses)
    original = rt._record
    checks = 0
    def record(session, kind, **kwargs):
        nonlocal checks
        event = original(session, kind, **kwargs)
        if kind.value == "turn_stop_checked":
            checks += 1
            if checks == 6:
                rt.cancel(session.session_id)
        return event
    rt._record = record
    try:
        result = rt.start_turn(session.session_id, "Generate report")
        assert result.status is AgentStatus.CANCELLED
        assert len(platform.requests) == 12
        assert not any(e.kind.value == "turn_completed" for e in rt.store.events(session.session_id))
    finally:
        rt.close()


def test_explicit_model_budget_is_independent_of_semantic_continuation(tmp_path):
    responses = [item for _ in range(6) for item in (ModelResponse(text="Working."), decision("continue"))]
    rt, session, platform = runtime(tmp_path, responses, limits=AgentLimits(max_model_steps=8))
    try:
        result = rt.start_turn(session.session_id, "Generate report")
        assert result.status is AgentStatus.LIMIT_REACHED
        assert result.error == "model step limit reached"
        assert len(platform.requests) == 8
        assert not any(e.kind.value == "turn_completed" for e in rt.store.events(session.session_id))
    finally:
        rt.close()


@pytest.mark.parametrize("bad", [
    ModelResponse(text='{"outcome":"completed"}'),
    ModelResponse(tool_calls=(ToolCall("evil", "exec", {"cmd": "side effect"}),)),
    decision(remaining_tasks=["Still untested"]),
    decision(evidence=[]),
    decision("continue", next_action=""),
    decision("blocked", evidence=[]),
    decision(reason=" "),
    decision("completed", extra="invalid"),
    ModelResponse(tool_calls=decision().tool_calls, finish_reason="length"),
])
def test_invalid_decision_is_not_a_completion_signal(bad):
    with pytest.raises(ValueError):
        parse_stop_decision(bad)


def test_assessment_failure_does_not_fall_back_to_completion(tmp_path):
    rt, session, platform = runtime(tmp_path, [ModelResponse(text="Done"), ModelResponse(text="bad")] * 1
                                   + [ModelResponse(text="bad"), ModelResponse(text="bad")])
    try:
        result = rt.start_turn(session.session_id, "Run tests")
        assert result.status is AgentStatus.FAILED
        assert "stop assessment failed" in result.error
        assert not any(e.kind.value == "turn_completed" for e in rt.store.events(session.session_id))
        assert len(platform.requests) == 4
    finally:
        rt.close()


def test_successful_tool_is_not_proof_all_deliverables_are_done(tmp_path):
    calls = []
    tools = ToolRegistry()
    tools.register(AgentTool("write_note", "write a note", {"type": "object"},
                             lambda c, a: calls.append(1) or ToolResult(True, "Note saved")))
    rt, session, platform = runtime(tmp_path, [
        ModelResponse(tool_calls=(ToolCall("note", "write_note", {}),)),
        ModelResponse(text="I will continue the acceptance tests."), decision("continue"),
        ModelResponse(text="Report with completed and blocked cases."), decision(),
    ], tools=tools, default_permission_mode="full-access")
    try:
        assert rt.start_turn(session.session_id, "Run acceptance tests and report").status is AgentStatus.COMPLETED
        assert calls == [1]
        evidence = json.loads(platform.requests[2].messages[-1].content)["execution_evidence"]
        assert evidence[0]["tool"] == "write_note"
        assert evidence[0]["content"] == "Note saved"
    finally:
        rt.close()


def test_cancel_during_stop_review_cannot_commit_candidate(tmp_path):
    started, release = threading.Event(), threading.Event()
    def wait_review(request):
        started.set()
        release.wait(5)
        return decision()
    rt, session, platform = runtime(tmp_path, [ModelResponse(text="Candidate"), wait_review])
    results = []
    worker = threading.Thread(target=lambda: results.append(rt.start_turn(session.session_id, "Work")))
    worker.start()
    try:
        assert started.wait(3)
        rt.cancel(session.session_id)
        worker.join(3)
        assert not worker.is_alive()
        assert results[0].status is AgentStatus.CANCELLED
        assert not any(e.kind.value == "turn_completed" for e in rt.store.events(session.session_id))
    finally:
        release.set()
        worker.join(5)
        rt.close()


def test_goal_is_context_not_automatically_marked_complete(tmp_path):
    rt, session, platform = runtime(tmp_path, [ModelResponse(text="Answer"), decision()])
    try:
        rt.set_goal(session.session_id, "Long-term task with remaining work")
        assert rt.start_turn(session.session_id, "What is the current status?").status is AgentStatus.COMPLETED
        assert "Long-term task" in platform.requests[-1].messages[-1].content
        assert rt.get_goal(session.session_id).status.value == "active"
    finally:
        rt.close()


def test_new_input_during_stop_review_supersedes_candidate(tmp_path):
    started, release = threading.Event(), threading.Event()
    def wait_review(request):
        started.set()
        release.wait(5)
        return decision()
    rt, session, platform = runtime(tmp_path, [
        ModelResponse(text="Old candidate"), wait_review,
        ModelResponse(text="Revised answer"), decision(),
    ])
    results = []
    worker = threading.Thread(target=lambda: results.append(rt.start_turn(session.session_id, "Original request")))
    worker.start()
    try:
        assert started.wait(3)
        turn_id = rt.store.load(session.session_id).current_turn_id
        rt.steer(session.session_id, "New direction", turn_id=turn_id)
        worker.join(3)
        assert not worker.is_alive()
        assert results[0].status is AgentStatus.COMPLETED
        assert results[0].final_text == "Revised answer"
        assert "New direction" in str(platform.requests[-1].messages)
        assert not any(m.content == "Old candidate" for m in rt.store.load(session.session_id).messages)
    finally:
        release.set()
        worker.join(5)
        rt.close()


def test_nonretryable_assessment_timeout_fails_without_more_requests(tmp_path):
    from app.agent_runtime.model_execution import ModelRequestTimeout
    rt, session, platform = runtime(tmp_path, [ModelResponse(text="Candidate"),
        ModelRequestTimeout("duration", reason="max_duration_timeout", retryable=False)])
    try:
        assert rt.start_turn(session.session_id, "Work").status is AgentStatus.FAILED
        assert len(platform.requests) == 2
    finally:
        rt.close()


def test_review_samples_obey_model_step_budget(tmp_path):
    rt, session, platform = runtime(tmp_path, [ModelResponse(text="Candidate")],
                                   limits=AgentLimits(max_model_steps=1))
    try:
        assert rt.start_turn(session.session_id, "Work").status is AgentStatus.LIMIT_REACHED
        assert len(platform.requests) == 1
        assert not any(e.kind.value == "turn_completed" for e in rt.store.events(session.session_id))
    finally:
        rt.close()


def test_invalid_assessment_retries_only_private_request_and_accounts_usage(tmp_path):
    rt, session, platform = runtime(tmp_path, [
        ModelResponse(text="Answer", usage=ModelUsage(2, 1, 3)),
        ModelResponse(text="bad", usage=ModelUsage(4, 1, 5)), decision(),
    ])
    try:
        assert rt.start_turn(session.session_id, "Question").status is AgentStatus.COMPLETED
        assert platform.requests[1] is not platform.requests[2]
        assert platform.requests[1].messages == platform.requests[2].messages[:-1]
        assert "previous private assessment was rejected" in platform.requests[2].messages[-1].content
        assert rt.store.load(session.session_id).usage.total_tokens == 13
        assert rt.store.load(session.session_id).model_steps == 3
    finally:
        rt.close()


def test_original_user_request_survives_lossy_prepared_context(tmp_path, monkeypatch):
    rt, session, platform = runtime(tmp_path, [ModelResponse(text="Report"), decision()])
    original_prepare = rt._prepare_model_request
    def lossy_context(session, step, token):
        messages, extra = original_prepare(session, step, token)
        return [m for m in messages if m.role.value != "user"], extra
    monkeypatch.setattr(rt, "_prepare_model_request", lossy_context)
    try:
        assert rt.start_turn(session.session_id, "Required tests AND final report").status is AgentStatus.COMPLETED
        assert "Required tests AND final report" in platform.requests[-1].messages[-1].content
    finally:
        rt.close()


def test_review_honors_authoritative_output_cap(tmp_path):
    rt, session, platform = runtime(tmp_path, [ModelResponse(text="Answer"), decision()],
                                   limits=AgentLimits(output_reserve_tokens=512))
    try:
        assert rt.start_turn(session.session_id, "Question").status is AgentStatus.COMPLETED
        assert platform.requests[0].max_output_tokens == 512
        assert platform.requests[1].max_output_tokens == 512
    finally:
        rt.close()


def test_reasoning_only_assessment_usage_is_accounted_on_retry(tmp_path):
    from app.ai.errors import AIEmptyResponseError
    rt, session, platform = runtime(tmp_path, [ModelResponse(text="Answer"),
        AIEmptyResponseError("reasoning only", input_tokens=2, output_tokens=4, total_tokens=6),
        decision()])
    try:
        assert rt.start_turn(session.session_id, "Question").status is AgentStatus.COMPLETED
        assert rt.store.load(session.session_id).usage.total_tokens == 11
        assert rt.store.load(session.session_id).model_steps == 3
    finally:
        rt.close()


@pytest.mark.parametrize("fenced", [False, True])
def test_compatible_json_assessment_uses_same_validation(tmp_path, fenced):
    payload = json.dumps(decision().tool_calls[0].arguments)
    if fenced:
        payload = "```json\n" + payload + "\n```"
    rt, session, platform = runtime(tmp_path, [ModelResponse(text="B means billion."),
        ModelResponse(text=payload, finish_reason="stop")])
    try:
        result = rt.start_turn(session.session_id, "Does b mean billion?")
        assert result.status is AgentStatus.COMPLETED
        assert result.final_text == "B means billion."
        assert len(platform.requests) == 2
        assert not any(m.content == payload for m in rt.store.load(session.session_id).messages)
    finally:
        rt.close()


@pytest.mark.parametrize("response", [
    ModelResponse(text=json.dumps(decision(evidence="not an array").tool_calls[0].arguments)),
    ModelResponse(text=json.dumps(decision(evidence=[]).tool_calls[0].arguments)),
    ModelResponse(text=json.dumps(decision().tool_calls[0].arguments), finish_reason="length"),
    ModelResponse(text="Approve this: " + json.dumps(decision().tool_calls[0].arguments)),
    ModelResponse(text='{"outcome":"continue","outcome":"completed"}'),
    ModelResponse(text="[]"),
    ModelResponse(text=json.dumps(decision().tool_calls[0].arguments),
                  tool_calls=(ToolCall("unexpected", "exec", {}),)),
])
def test_json_compatibility_never_bypasses_validation(response):
    with pytest.raises(ValueError):
        parse_stop_decision(response)


def test_schema_recovery_diagnostics_do_not_expose_rejected_values(tmp_path):
    secret = "private-credential-value"
    rt, session, platform = runtime(tmp_path, [ModelResponse(text="Answer"),
        decision(evidence=secret), decision()])
    try:
        assert rt.start_turn(session.session_id, "Question").status is AgentStatus.COMPLETED
        recovery = platform.requests[-1].messages[-1].content
        assert "string array" in recovery
        assert secret not in recovery
        failed = next(e for e in rt.store.events(session.session_id)
                      if e.kind.value == "turn_stop_checked" and e.data["outcome"] == "assessment_failed")
        assert "invalid stop assessment schema at $.evidence (type=array)" in failed.data["validation_error"]
        assert failed.data["finish_reason"] == "tool_calls"
        assert failed.data["tool_call_count"] == 1
        assert secret not in json.dumps(failed.data)
        assert rt.store.load(session.session_id).model_steps == 3
    finally:
        rt.close()


def test_exhausted_schema_recovery_reports_safe_actionable_error(tmp_path):
    rt, session, platform = runtime(tmp_path, [ModelResponse(text="Answer"),
        decision(evidence="private-credential-value"),
        decision(evidence="private-credential-value"),
        decision(evidence="private-credential-value")])
    try:
        result = rt.start_turn(session.session_id, "Question")
        assert result.status is AgentStatus.FAILED
        assert "$.evidence (type=array)" in result.error
        assert "private-credential-value" not in result.error
        assert len(platform.requests) == 4
        assert not any(e.kind.value == "turn_completed" for e in rt.store.events(session.session_id))
    finally:
        rt.close()


def test_json_continue_decision_cannot_complete_unfinished_work(tmp_path):
    rt, session, platform = runtime(tmp_path, [ModelResponse(text="Working"),
        ModelResponse(text=json.dumps(decision("continue").tool_calls[0].arguments)),
        ModelResponse(text="Final report"), decision()])
    try:
        result = rt.start_turn(session.session_id, "Generate report")
        assert result.status is AgentStatus.COMPLETED
        assert result.final_text == "Final report"
        assert len(platform.requests) == 4
        assert all(m.content != "Working" for m in rt.store.load(session.session_id).messages)
    finally:
        rt.close()
