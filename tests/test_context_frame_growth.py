"""Runtime context frames must not repeat unchanged state once per model step."""
from __future__ import annotations

from app.agent_runtime import AgentRuntime, FileAgentSessionStore, SandboxManager, SandboxPolicy
from app.agent_runtime.workspace_tools import loom_default_tools
from app.ai import ModelResponse, ToolCall


class _LongTurn:
    def __init__(self, steps):
        self.steps, self.requests = steps, []

    def execute_chat(self, profile, request):
        self.requests.append(request)
        n = len(self.requests)
        if n == 1:
            plan = {"plan": [{"step": "Prepare", "status": "in_progress"},
                             {"step": "Run", "status": "pending"}]}
            return ModelResponse(tool_calls=(ToolCall("plan", "update_plan", plan),))
        if n <= self.steps:
            return ModelResponse(tool_calls=(ToolCall(f"c{n}", "list_workspace_files", {}),))
        return ModelResponse(text="Done.")


def test_unchanged_state_and_plan_are_not_reappended_every_step(tmp_path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    platform = _LongTurn(steps=12)
    runtime = AgentRuntime(platform=platform, store=FileAgentSessionStore(tmp_path / "state"),
                           tools=loom_default_tools(), sandbox_manager=SandboxManager(policy=SandboxPolicy.OFF))
    try:
        session = runtime.create_session("agent.fast", workspace_dir=workspace, permission_mode="full-access")
        runtime.start_turn(session.session_id, "long turn")
        last = platform.requests[-1].messages
        names = [message.name for message in last]
        # Nothing in the runtime state changed after the first step, and the plan
        # changed once; per-step ids and result counters must not create copies.
        assert names.count("loom_runtime_state") == 1
        assert names.count("loom_task_plan") == 1
        state = next(m.content for m in last if m.name == "loom_runtime_state")
        assert '"step_id"' not in state and '"model_step"' not in state
        # The prefix stays append-only while the frames stop growing.
        for earlier, later in zip(platform.requests, platform.requests[1:]):
            assert later.messages[:len(earlier.messages)] == earlier.messages
    finally:
        runtime.close()
