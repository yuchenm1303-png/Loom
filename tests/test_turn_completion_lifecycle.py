from app.ai import ModelResponse
from app.agent_runtime.context_runtime import ContextAgentRuntime
from app.agent_runtime.contracts import AgentStatus, AgentEventKind as Event
from app.agent_runtime.storage import FileAgentSessionStore
from app.agent_runtime.sandbox import SandboxManager, SandboxPolicy
from app.agent_runtime.turn_stop import StopReviewLimitReached
from test_turn_stop import Scripted
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
        assert all(e.data["completion_check"] == "not_configured" and e.data["stop_decision"] is None for e in endings)
    finally:
        rt.close()


@pytest.mark.parametrize("error", [StopReviewLimitReached("evidence too large"), TimeoutError("check timed out"),
                                  ValueError("invalid check output")])
def test_optional_check_failure_preserves_answer_without_a_success_verdict(tmp_path, error):
    def hook(*args):
        raise error
    rt, session, platform = make_runtime(tmp_path, [ModelResponse(text="Some checks remain unverified.")], stop_hook=hook)
    try:
        result = rt.start_turn(session.session_id, "Give current results")
        assert result.status is AgentStatus.COMPLETED
        assert result.final_text == "Some checks remain unverified."
        events = rt.store.events(session.session_id)
        assert events[-1].data["completion_check"] == "unavailable"
        assert events[-1].data["stop_decision"] is None
        assert any(e.kind is Event.TURN_STOP_CHECKED and e.data.get("answer_preserved") for e in events)
        assert not any(e.kind in {Event.LIMIT_REACHED, Event.TURN_FAILED} for e in events)
    finally:
        rt.close()


def test_malformed_optional_check_output_is_unavailable(tmp_path):
    rt, session, platform = make_runtime(tmp_path, [ModelResponse(text="Current results.")],
                                        stop_hook=lambda *args: {"outcome": "completed"})
    try:
        result = rt.start_turn(session.session_id, "Report")
        assert result.status is AgentStatus.COMPLETED
        assert result.final_text == "Current results."
        assert rt.store.events(session.session_id)[-1].data["completion_check"] == "unavailable"
    finally:
        rt.close()
