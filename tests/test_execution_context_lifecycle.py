"""Exercise the production context-managed path, not only the core runtime."""
import pytest

from app.ai import MessageRole, ModelResponse, ToolCall
from app.agent_runtime.contracts import AgentStatus, AgentEventKind as Event
from app.agent_runtime.context_runtime import ContextAgentRuntime
from app.agent_runtime.sandbox import SandboxManager, SandboxPolicy
from app.agent_runtime.storage import FileAgentSessionStore
from app.agent_runtime.tools import AgentTool, ToolRegistry, ToolResult
from scripted_agent_platform import Scripted



def test_continuation_plan_and_exact_evidence_survive_model_requested_rollover(tmp_path):
    calls = []
    def probe(context, arguments):
        calls.append(arguments["id"])
        return ToolResult(True, "PROOF_" + arguments["id"])

    plan = [{"step": "Run checks", "status": "completed", "outcome": "passed", "evidence": "PROOF_first", "evidence_refs": [{"call_id": "first"}]},
            {"step": "Write report", "status": "in_progress"}]
    platform = Scripted([
        ModelResponse(tool_calls=(ToolCall("first", "probe", {"id": "first"}), ToolCall("plan", "update_plan", {"plan": plan}))),
        ModelResponse(text="The report comes next.", end_turn=False),
        ModelResponse(tool_calls=(ToolCall("rollover", "new_context", {}), ToolCall("second", "probe", {"id": "second"}))),
        ModelResponse(text="Checks first and second passed. Report remains. Keep their proof references."),
        ModelResponse(tool_calls=(ToolCall("third", "probe", {"id": "third"}),)),
        ModelResponse(text="Report: all three checks passed."),
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
        actor_requests = [r for r in platform.requests if r.tools]
        for request in actor_requests[1:]:
            assert not any(m.name == "loom_execution_progress" for m in request.messages)
            task_plan = next(m for m in request.messages if m.name == "loom_task_plan")
            assert "PROOF_first" in task_plan.content
        checkpoints = rt.list_context_checkpoints(session.session_id)
        assert len(checkpoints) == 1
        assert "lossy assistant-authored handoff" in checkpoints[0].summary_message().content
        compacted = next(m for r in actor_requests for m in r.messages if m.name == "loom_compaction")
        assert "not new user instructions or independent verification" in compacted.content
        archived = checkpoints[0].archived_messages
        assert "PROOF_first" in str(archived) and "PROOF_second" in str(archived)
        events = rt.store.events(session.session_id)
        checkpoint = next(e for e in events if e.kind is Event.CONTEXT_CHECKPOINTED)
        later_requests = [e for e in events if e.kind is Event.MODEL_REQUESTED and e.created_at >= checkpoint.created_at]
        assert any(e.data.get("model_requested_rollover") is True for e in later_requests)
        assert not any(e.kind in {Event.TURN_FAILED, Event.LIMIT_REACHED} for e in events)
    finally:
        rt.close()


def test_no_plan_does_not_reinject_tool_counts_as_model_instructions(tmp_path):
    platform = Scripted([
        ModelResponse(tool_calls=(ToolCall("first", "probe", {}),)),
        ModelResponse(tool_calls=(ToolCall("second", "probe", {}),)),
        ModelResponse(text="Both checks completed."),
    ])
    rt = ContextAgentRuntime(platform=platform, store=FileAgentSessionStore(tmp_path / "state"),
        tools=ToolRegistry((AgentTool("probe", "check", {"type": "object"},
            lambda *_: ToolResult(True, "durable receipt")),)),
        sandbox_manager=SandboxManager(policy=SandboxPolicy.OFF))
    session = rt.create_session("test", workspace_dir=tmp_path, permission_mode="full-access")
    try:
        result = rt.start_turn(session.session_id, "Perform two checks")
        assert result.status is AgentStatus.COMPLETED
        assert len(platform.requests) == 3
        for request in platform.requests:
            assert not any(m.name == "loom_execution_progress" for m in request.messages)
        receipts = [m for m in platform.requests[-1].messages if m.role is MessageRole.TOOL]
        assert {m.tool_call_id for m in receipts} == {"first", "second"}
    finally:
        rt.close()
