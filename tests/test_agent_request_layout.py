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
        assert [(m.role.value, m.name or "") for m in request.messages] == [
            ("system", ""), ("system", "loom_runtime_state"),
            ("system", "loom_communication_language"), ("system", "loom_task_plan"),
            ("system", "loom_inline_sticker_protocol"), ("system", "loom_balanced_sticker_distribution"),
            ("user", ""), ("assistant", ""), ("tool", "update_plan"),
            ("assistant", ""), ("tool", "list_workspace_files"),
        ]
        state = next(m.content for m in request.messages if m.name == "loom_runtime_state")
        assert "authoritative for the current model step" in state
        assert '"model_step": 3' in state
        assert request.messages[6].content == "Two steps"
        assert [m.tool_call_id for m in request.messages if m.role.value == "tool"] == ["plan", "list"]
    finally:
        runtime.close()
