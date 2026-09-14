from __future__ import annotations

from app.agent_runtime import AgentRuntime, FileAgentSessionStore, SandboxManager, SandboxPolicy
from app.agent_runtime.workspace_tools import loom_default_tools
from app.ai import AGENT_FAST_ROLE, MessageRole, ModelResponse


class ScriptedPlatform:
    def __init__(self):
        self.requests = []

    def execute_chat(self, profile_id, request):
        self.requests.append((profile_id, request))
        return ModelResponse(text="检查完成")


def test_language_anchor_is_last_transient_system_context(tmp_path):
    workspace = tmp_path / "project"
    workspace.mkdir()
    (workspace / "AGENTS.md").write_text(
        "Inspect logs and source files carefully. Keep technical identifiers unchanged.",
        encoding="utf-8",
    )
    platform = ScriptedPlatform()
    runtime = AgentRuntime(
        platform=platform,
        store=FileAgentSessionStore(tmp_path / "state"),
        tools=loom_default_tools(),
        sandbox_manager=SandboxManager(policy=SandboxPolicy.OFF),
    )
    session = runtime.create_session(AGENT_FAST_ROLE.role_id, workspace_dir=workspace)

    runtime.start_turn(session.session_id, "继续检查这个问题，先定位日志里的失败原因。")

    request = platform.requests[0][1]
    names = [message.name for message in request.messages]
    project_index = names.index("loom_project_instructions")
    language_index = names.index("loom_communication_language")
    user_index = next(
        index for index, message in enumerate(request.messages)
        if message.role is MessageRole.USER and not message.name
    )

    assert project_index < language_index < user_index
    assert "Current user communication language: Chinese" in request.messages[language_index].content
    runtime.close()