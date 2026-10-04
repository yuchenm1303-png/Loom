from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from app.ai import AIMessage, ChatRequest, ImagePart, MessageRole, ModelResponse, TextPart, ToolCall
from app.agent_runtime import AgentStatus
from app.agent_runtime.contracts import AgentLimits, AgentEventKind as Event
from app.agent_runtime.response_language import infer_user_language
from app.agent_runtime.runtime import DEFAULT_AGENT_SYSTEM_PROMPT, _DEFAULT_AGENT_SYSTEM_PROMPT_V7
from app.agent_runtime.task_plan import current_plan
from app.agent_runtime.turn_stop import STOP_TOOL, stop_review_messages, StopReviewLimitReached
from app.agent_runtime.tools import AgentTool, ToolRegistry, ToolResult
from test_turn_stop import runtime, decision

pytestmark = pytest.mark.real_stop_hook


def test_stop_budget_survives_successful_tool_batches(tmp_path):
    tools = ToolRegistry()
    tools.register(AgentTool("probe", "observe", {"type": "object"}, lambda c, a: ToolResult(True, "observed")))
    responses = []
    for index in range(3):
        responses.extend([ModelResponse(tool_calls=(ToolCall(str(index), "probe", {}),)),
                          ModelResponse(text="Report coming"), decision("continue")])
    rt, session, platform = runtime(tmp_path, responses, tools=tools, default_permission_mode="full-access")
    try:
        result = rt.start_turn(session.session_id, "Run the checks and give a report")
        assert result.status is AgentStatus.LIMIT_REACHED
        assert result.final_text == ""
        events = rt.store.events(session.session_id)
        assert sum(e.kind is Event.TOOL_COMPLETED for e in events) == 3
        assert sum(e.kind is Event.TURN_STOP_CHECKED for e in events) == 3
        assert not any(e.kind is Event.TURN_COMPLETED for e in events)
        assert len(platform.requests) == 9
    finally:
        rt.close()


def test_assessor_excludes_actor_promises_reasoning_and_visual_transport(tmp_path):
    rt, session, platform = runtime(tmp_path, [ModelResponse(text="Report", reasoning="PRIVATE_ACTOR_REASONING"), decision()])
    original = rt._prepare_model_request
    def prepare(session, step, token):
        messages, extra = original(session, step, token)
        messages.extend([
            AIMessage(role=MessageRole.ASSISTANT, content="SELF_IMPOSED_EXTRA_AUDIT"),
            AIMessage(role=MessageRole.USER, name="loom_tool_observation", content="TRANSPORT_IS_NOT_USER_INTENT"),
            AIMessage(role=MessageRole.SYSTEM, name="loom_terminal_recovery", content="STALE_STOP_FEEDBACK"),
        ])
        return messages, extra
    rt._prepare_model_request = prepare
    try:
        assert rt.start_turn(session.session_id, "Produce the requested report").status is AgentStatus.COMPLETED
        context = str(platform.requests[-1].messages)
        assert "Produce the requested report" in context
        assert "Report" in context
        assert all(value not in context for value in ("SELF_IMPOSED_EXTRA_AUDIT", "PRIVATE_ACTOR_REASONING",
                                                      "TRANSPORT_IS_NOT_USER_INTENT", "STALE_STOP_FEEDBACK"))
    finally:
        rt.close()


def test_plan_is_durable_and_reinjected_after_history_loss(tmp_path):
    plan = [{"step": "Run checks", "status": "completed", "evidence": "test-output.txt: 12 passed"},
            {"step": "Write report", "status": "in_progress"}]
    rt, session, platform = runtime(tmp_path, [
        ModelResponse(tool_calls=(ToolCall("plan", "update_plan", {"plan": plan}),)),
        ModelResponse(text="Report with test results"), decision(),
    ], default_permission_mode="full-access")
    try:
        assert rt.start_turn(session.session_id, "Run checks and write a report").status is AgentStatus.COMPLETED
        loaded = rt.store.load(session.session_id)
        loaded.messages.clear()
        step = rt._build_step_context(loaded, next_model_step=True)
        messages, _ = rt._prepare_model_request(loaded, step, None)
        assert any(m.name == "loom_task_plan" and "12 passed" in m.content for m in messages)
        assert current_plan(rt.store.events(session.session_id), loaded.current_turn_id)["plan"] == plan
        loaded.current_turn_id = "another-turn"
        assert current_plan(rt.store.events(session.session_id), loaded.current_turn_id) is None
    finally:
        rt.close()


@pytest.mark.parametrize("plan", [
    [{"step": "Check", "status": "in_progress"}, {"step": "Report", "status": "in_progress"}],
    [{"step": "Check", "status": "completed"}, {"step": "Report", "status": "pending"}],
    [{"step": "Check", "status": "blocked"}, {"step": "Report", "status": "pending"}],
])
def test_invalid_plan_never_replaces_durable_state(tmp_path, plan):
    rt, session, _ = runtime(tmp_path, [
        ModelResponse(tool_calls=(ToolCall("plan", "update_plan", {"plan": plan}),)),
        ModelResponse(text="Specific failed plan result"), decision("blocked"),
    ], default_permission_mode="full-access")
    try:
        rt.start_turn(session.session_id, "Run checks and report")
        assert current_plan(rt.store.events(session.session_id), rt.store.load(session.session_id).current_turn_id) is None
        assert any(e.kind is Event.TOOL_FAILED for e in rt.store.events(session.session_id))
    finally:
        rt.close()


def test_visual_observations_do_not_change_user_language():
    assert infer_user_language([
        AIMessage(role=MessageRole.USER, content="请帮我完成检查"),
        AIMessage(role=MessageRole.USER, name="loom_tool_observation", content="Current foreground desktop observation")
    ]) == "zh"


def test_continuation_reuses_previous_turn_results(tmp_path):
    tools = ToolRegistry()
    tools.register(AgentTool("probe", "observe", {"type": "object"}, lambda c, a: ToolResult(True, "CHECK_ALREADY_PASSED")))
    rt, session, platform = runtime(tmp_path, [
        ModelResponse(tool_calls=(ToolCall("prior-proof", "probe", {}),)),
        ModelResponse(text="Check passed; report remains"), decision("blocked"),
        ModelResponse(text="Report with previous results"), decision(),
    ], tools=tools, default_permission_mode="full-access")
    try:
        rt.start_turn(session.session_id, "Run checks and write the report")
        assert rt.start_turn(session.session_id, "Continue with the report").status is AgentStatus.COMPLETED
        payload = json.loads(platform.requests[-1].messages[-1].content)
        assert "CHECK_ALREADY_PASSED" in str(payload["prior_tool_context"])
        assert payload["prior_turn_results"][-1]["answer"] == "Check passed; report remains"
    finally:
        rt.close()


def test_late_user_guidance_wins_over_completion_budget(tmp_path):
    rt, session, platform = runtime(tmp_path, [
        ModelResponse(text="Old candidate"), decision("continue"),
        ModelResponse(text="Updated result"), decision(),
    ], limits=AgentLimits(max_stop_continuations=1))
    original = rt._record
    sent = False
    def record(session, kind, **kwargs):
        nonlocal sent
        result = original(session, kind, **kwargs)
        if kind is Event.TURN_STOP_CHECKED and not sent:
            sent = True
            rt.steer(session.session_id, "Report the current results only", turn_id=session.current_turn_id)
        return result
    rt._record = record
    try:
        result = rt.start_turn(session.session_id, "Run checks and report")
        assert result.status is AgentStatus.COMPLETED
        assert result.final_text == "Updated result"
        assert "Report the current results only" in str(platform.requests[2].messages)
    finally:
        rt.close()


def test_review_bounds_evidence_without_losing_call_identity_or_result_tails():
    from app.agent_runtime.context_budget import estimate_tokens
    events = [SimpleNamespace(turn_id="turn", kind=Event.TOOL_COMPLETED, data={
        "call_id": f"proof-{index}", "tool": "exec", "ok": False,
        "content": "start\n" + "verbose command output\n" * 4000 + "TAIL_FAILURE_EVIDENCE",
        "data": {"exit_code": 1}}) for index in range(100)]
    rt = SimpleNamespace(store=SimpleNamespace(events=lambda session: events), limits=AgentLimits())
    session = SimpleNamespace(session_id="session", current_turn_id="turn")
    request = ChatRequest(messages=(AIMessage(role=MessageRole.USER, content="Report the failures"),))
    messages = stop_review_messages(rt, session, request, ModelResponse(text="Failure report"))
    payload = json.loads(messages[-1].content)
    assert len(payload["execution_evidence"]) == 100
    assert all(item["content"].endswith("TAIL_FAILURE_EVIDENCE") for item in payload["execution_evidence"])
    assert payload["execution_evidence"][-1]["call_id"] == "proof-99"
    assert estimate_tokens(messages, (STOP_TOOL,)) < 14_000
    # A declared small context cannot be replaced by a guessed larger window.
    with pytest.raises(StopReviewLimitReached):
        stop_review_messages(rt, session, request, ModelResponse(text="Failure report"),
            context_limits=SimpleNamespace(window_known=True, input_budget_tokens=100, safety_tokens=0))


def test_assessment_uses_transient_dom_and_image_as_evidence_not_human_intent():
    rt = SimpleNamespace(store=SimpleNamespace(events=lambda session: []), limits=AgentLimits())
    session = SimpleNamespace(session_id="session", current_turn_id="turn")
    image = ImagePart("data:image/png;base64,UE5H")
    request = ChatRequest(messages=(
        AIMessage(role=MessageRole.USER, content="What is on the page?"),
        AIMessage(role=MessageRole.TOOL, name="browser_state", tool_call_id="screen",
                  content=(TextPart("Page metadata"), TextPart("TRANSIENT_DOM_EVIDENCE"))),
        AIMessage(role=MessageRole.USER, name="loom_tool_observation", content=(TextPart("Tool visual attachment"), image)),
    ))
    messages = stop_review_messages(rt, session, request, ModelResponse(text="Page answer"))
    payload = json.loads(messages[-1].content[0].text)
    assert payload["user_context"] == ["What is on the page?"]
    assert payload["transient_tool_observations"] == [{"call_id": "screen", "tool": "browser_state", "observation": "TRANSIENT_DOM_EVIDENCE"}]
    assert image in messages[-1].content


def test_existing_default_prompt_upgrades_but_custom_prompt_survives(tmp_path):
    rt, session, _ = runtime(tmp_path, [])
    try:
        session.system_prompt = _DEFAULT_AGENT_SYSTEM_PROMPT_V7
        session.system_prompt_version = 7
        rt.store.save(session)
        assert rt.get_session(session.session_id).system_prompt == DEFAULT_AGENT_SYSTEM_PROMPT
        session.system_prompt = "Custom developer prompt"
        rt.store.save(session)
        assert rt.get_session(session.session_id).system_prompt == "Custom developer prompt"
    finally:
        rt.close()


@pytest.mark.parametrize("limit", [0, -1, 11])
def test_stop_continuation_budget_is_finite(limit):
    with pytest.raises(ValueError):
        AgentLimits(max_stop_continuations=limit)
