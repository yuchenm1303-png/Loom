"""Exercise the production context-managed path, not only the core runtime."""
import pytest

from app.ai import MessageRole, ModelResponse, ToolCall
from app.agent_runtime.contracts import AgentStatus, AgentEventKind as Event
from app.agent_runtime.context_runtime import ContextAgentRuntime
from app.agent_runtime.sandbox import SandboxManager, SandboxPolicy
from app.agent_runtime.storage import FileAgentSessionStore
from app.agent_runtime.tools import AgentTool, ToolRegistry, ToolResult
from test_turn_stop import Scripted, decision

pytestmark = pytest.mark.real_stop_hook


def test_continuation_plan_and_exact_evidence_survive_model_requested_rollover(tmp_path):
    calls = []
    def probe(context, arguments):
        calls.append(arguments["id"])
        return ToolResult(True, "PROOF_" + arguments["id"])

    plan = [{"step": "Run checks", "status": "completed", "evidence": "PROOF_first"},
            {"step": "Write report", "status": "in_progress"}]
    platform = Scripted([
        ModelResponse(tool_calls=(ToolCall("first", "probe", {"id": "first"}), ToolCall("plan", "update_plan", {"plan": plan}))),
        ModelResponse(text="The report comes next."),
        decision("continue", remaining_tasks=["REPORT_DELIVERABLE"], next_action="Write the requested report"),
        ModelResponse(tool_calls=(ToolCall("rollover", "new_context", {}), ToolCall("second", "probe", {"id": "second"}))),
        ModelResponse(text="Checks first and second passed. Report remains. Keep their proof references."),
        ModelResponse(tool_calls=(ToolCall("third", "probe", {"id": "third"}),)),
        ModelResponse(text="Report: all three checks passed."), decision(),
    ])
    rt = ContextAgentRuntime(platform=platform, store=FileAgentSessionStore(tmp_path / "state"),
        tools=ToolRegistry((AgentTool("probe", "check", {"type": "object", "properties": {
            "id": {"type": "string"}}, "required": ["id"]}, probe),)),
        sandbox_manager=SandboxManager(policy=SandboxPolicy.OFF))
    session = rt.create_session("test", workspace_dir=tmp_path, permission_mode="full-access")
    try:
        result = rt.start_turn(session.session_id, "Run checks and produce the report")
        assert result.status is AgentStatus.COMPLETED
        assert calls == ["first", "second", "third"]
        actor_requests = [r for r in platform.requests if r.purpose != "stop_review" and r.tools]
        for request in actor_requests[1:]:
            task_plan = next(m for m in request.messages if m.name == "loom_task_plan")
            assert "PROOF_first" in task_plan.content
        for request in actor_requests[2:]:
            continuation = [m for m in request.messages if m.name == "loom_turn_continuation"]
            assert len(continuation) == 1
            assert "REPORT_DELIVERABLE" in continuation[0].content
        checkpoints = rt.list_context_checkpoints(session.session_id)
        assert len(checkpoints) == 1
        archived = checkpoints[0].archived_messages
        assert "PROOF_first" in str(archived) and "PROOF_second" in str(archived)
        events = rt.store.events(session.session_id)
        checkpoint = next(e for e in events if e.kind is Event.CONTEXT_CHECKPOINTED)
        later_requests = [e for e in events if e.kind is Event.MODEL_REQUESTED and e.created_at >= checkpoint.created_at]
        assert any(e.data.get("model_requested_rollover") is True for e in later_requests)
        assert not any(e.kind in {Event.TURN_FAILED, Event.LIMIT_REACHED} for e in events)
    finally:
        rt.close()


def test_user_steering_supersedes_previous_assessment_on_context_path(tmp_path):
    platform = Scripted([ModelResponse(text="Old scope"), decision("continue", remaining_tasks=["OLD_SCOPE"]),
                         ModelResponse(text="Current results only"), decision()])
    rt = ContextAgentRuntime(platform=platform, store=FileAgentSessionStore(tmp_path / "state"),
        sandbox_manager=SandboxManager(policy=SandboxPolicy.OFF))
    session = rt.create_session("test", workspace_dir=tmp_path)
    original = rt._record
    def record(session, kind, **kwargs):
        event = original(session, kind, **kwargs)
        if kind is Event.TURN_STOP_CHECKED and kwargs["data"].get("outcome") == "continue":
            rt.steer(session.session_id, "Report current results only", turn_id=session.current_turn_id)
        return event
    rt._record = record
    try:
        assert rt.start_turn(session.session_id, "Run everything").status is AgentStatus.COMPLETED
        updated = platform.requests[2]
        assert not any(m.name == "loom_turn_continuation" for m in updated.messages)
        assert any(m.role is MessageRole.USER and m.content == "Report current results only" for m in updated.messages)
    finally:
        rt.close()
