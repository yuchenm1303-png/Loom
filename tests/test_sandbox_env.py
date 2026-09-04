from __future__ import annotations

from app.agent_runtime import AgentRuntime, FileAgentSessionStore, SandboxPolicy
from app.agent_runtime.workspace_tools import loom_default_tools
from app.ai import ModelResponse


class StaticPlatform:
    def execute_chat(self, profile_id, request):
        return ModelResponse(text="done")


def test_default_runtime_reads_loom_sandbox_policy(tmp_path, monkeypatch):
    monkeypatch.setenv("LOOM_SANDBOX_POLICY", "off")
    runtime = AgentRuntime(
        platform=StaticPlatform(),
        store=FileAgentSessionStore(tmp_path / "state"),
        tools=loom_default_tools(),
    )

    assert runtime.sandbox_manager.policy is SandboxPolicy.OFF
