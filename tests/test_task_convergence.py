from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from app.ai import AIMessage, ChatRequest, ImagePart, MessageRole, ModelResponse, TextPart, ToolCall
from app.agent_runtime import AgentStatus
from app.agent_runtime.contracts import AgentLimits, AgentEventKind as Event, ToolEffect
from app.agent_runtime.response_language import infer_user_language
from app.agent_runtime.runtime import DEFAULT_AGENT_SYSTEM_PROMPT, _DEFAULT_AGENT_SYSTEM_PROMPT_V7
from app.agent_runtime.task_plan import current_plan
from app.agent_runtime.tools import AgentTool, ToolRegistry, ToolResult
from scripted_agent_platform import runtime









def test_plan_is_durable_and_reinjected_after_history_loss(tmp_path):
    (tmp_path / "test-output.txt").write_text("12 passed", encoding="utf-8")
    plan = [{"step": "Run checks", "status": "completed", "outcome": "passed", "evidence": "test-output.txt: 12 passed", "evidence_refs": [{"path": "test-output.txt"}]},
            {"step": "Write report", "status": "in_progress"}]
    rt, session, platform = runtime(tmp_path, [
        ModelResponse(tool_calls=(ToolCall("plan", "update_plan", {"plan": plan}),)),
        ModelResponse(text="Report with test results"),
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
        ModelResponse(text="Specific failed plan result"),
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
