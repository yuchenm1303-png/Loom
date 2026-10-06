"""Characterize the production request layout before the context migration."""
import json

from app.agent_runtime import AgentRuntime, FileAgentSessionStore, SandboxManager, SandboxPolicy
from app.agent_runtime.workspace_tools import loom_default_tools
from app.ai import ModelResponse, ToolCall


class ScriptedPlatform:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.requests = []

    def execute_chat(self, profile, request):
        self.requests.append(request)
        return next(self.responses)


def test_production_request_layout_before_context_composer(tmp_path):
    platform = ScriptedPlatform([
        ModelResponse(text="Plan", tool_calls=(ToolCall("plan", "update_plan", {"plan": [
            {"step": "Prepare", "status": "in_progress"}, {"step": "Run", "status": "pending"}]}),)),
        ModelResponse(tool_calls=(ToolCall("list", "list_workspace_files", {}),)),
        ModelResponse(text="Done"),
    ])
    runtime = AgentRuntime(platform=platform, store=FileAgentSessionStore(tmp_path / "state"),
        tools=loom_default_tools(), sandbox_manager=SandboxManager(policy=SandboxPolicy.OFF))
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    try:
        session = runtime.create_session("agent.fast", workspace_dir=workspace, permission_mode="full-access")
        runtime.start_turn(session.session_id, "Two steps")
        request = platform.requests[-1]
        print(json.dumps([{"role": m.role.value, "name": m.name, "content": m.content}
                          for m in request.messages], ensure_ascii=False, indent=2))
        # Runtime snapshots follow the canonical boundary that first observed
        # them. Tool requests have no sticker instructions or decorated history.
        # A snapshot is appended only when its content changes. Per-step ids and
        # per-result counters used to make every step's copy unique.
        assert [(m.role.value, m.name or "") for m in request.messages] == [
            ("system", ""), ("user", ""),
            ("user", "loom_runtime_state"), ("user", "loom_communication_language"),
            ("assistant", ""), ("tool", "update_plan"),
            ("user", "loom_task_plan"),
            ("assistant", ""), ("tool", "list_workspace_files"),
        ]
        state = next(m.content for m in reversed(request.messages) if m.name == "loom_runtime_state")
        assert "authoritative for the current model step" in state
        assert '\"model_step\"' not in state and '\"step_id\"' not in state
        assert request.messages[1].content == "Two steps"
        assert "LOOM_CONTEXT_ITEMS v1" in request.messages[0].content
        for earlier, later in zip(platform.requests, platform.requests[1:]):
            assert later.messages[:len(earlier.messages)] == earlier.messages
        assert [m.tool_call_id for m in request.messages if m.role.value == "tool"] == ["plan", "list"]
    finally:
        runtime.close()
