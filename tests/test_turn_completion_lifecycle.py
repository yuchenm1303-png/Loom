from app.ai import ModelResponse
from app.agent_runtime.context_runtime import ContextAgentRuntime
from app.agent_runtime.contracts import AgentStatus, AgentEventKind as Event
from app.agent_runtime.storage import FileAgentSessionStore
from app.agent_runtime.sandbox import SandboxManager, SandboxPolicy
from scripted_agent_platform import Scripted
import pytest


def make_runtime(tmp_path, responses, **options):
    platform = Scripted(responses)
    rt = ContextAgentRuntime(platform=platform, store=FileAgentSessionStore(tmp_path / "state"),
        sandbox_manager=SandboxManager(policy=SandboxPolicy.OFF), **options)
    session = rt.create_session("test", workspace_dir=tmp_path)
    return rt, session, platform


def test_long_history_followup_does_not_invoke_an_implicit_completion_reviewer(tmp_path):
    from app.ai import AIMessage, MessageRole
    rt, session, platform = make_runtime(tmp_path, [ModelResponse(text="Report saved; long-run coverage is still unverified."),
        ModelResponse(text="Not fully done: report exists, long-run coverage remains unverified.")])
    try:
        session.messages = [AIMessage(role=MessageRole.USER, content="Test everything"),
            AIMessage(role=MessageRole.ASSISTANT, content="Repeated old progress. " * 10000)]
        rt.store.save(session)
        first = rt.start_turn(session.session_id, "Report the actual results")
        second = rt.start_turn(session.session_id, "Done yet?")
        assert first.status is second.status is AgentStatus.COMPLETED
        assert "unverified" in second.final_text
        assert len(platform.requests) == 2
        assert all(r.purpose != "stop_review" for r in platform.requests)
        events = rt.store.events(session.session_id)
        assert not any(e.kind in {Event.TURN_STOP_REQUESTED, Event.TURN_FAILED, Event.LIMIT_REACHED} for e in events)
        endings = [e for e in events if e.kind is Event.TURN_COMPLETED]
        assert all("completion_check" not in e.data and "stop_decision" not in e.data for e in endings)
    finally:
        rt.close()






def test_explicit_provider_continuation_runs_again_without_a_completion_reviewer(tmp_path):
    rt, session, platform = make_runtime(tmp_path, [
        ModelResponse(text="Progress so far", finish_reason="stop", phase="commentary", end_turn=False),
        ModelResponse(text="Final delivery", finish_reason="stop", phase="final_answer", end_turn=True)])
    try:
        result = rt.start_turn(session.session_id, "Work")
        assert result.final_text == "Final delivery"
        assert len(platform.requests) == 2
        events = rt.store.events(session.session_id)
        assert len([e for e in events if e.kind is Event.TURN_COMPLETED]) == 1
        assert events[-1].data["execution_end_source"] == "provider_end_turn"
        assert events[-1].data["task_completion"] == "not_assessed"
    finally:
        rt.close()


def test_empty_native_continuation_is_preserved_and_samples_again(tmp_path):
    rt, session, platform = make_runtime(tmp_path, [
        ModelResponse(finish_reason="stop", end_turn=False),
        ModelResponse(text="Final delivery", finish_reason="stop", end_turn=True)])
    try:
        result = rt.start_turn(session.session_id, "Work")
        assert result.status is AgentStatus.COMPLETED
        assert result.final_text == "Final delivery"
        assert len(platform.requests) == 2
        events = rt.store.events(session.session_id)
        assert not any(e.kind in {Event.MODEL_RESPONSE_REJECTED, Event.TURN_FAILED} for e in events)
        assert len([e for e in events if e.kind is Event.TURN_COMPLETED]) == 1
    finally:
        rt.close()
