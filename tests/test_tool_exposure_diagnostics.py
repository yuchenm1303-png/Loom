from __future__ import annotations

from app.agent_runtime import (
    AgentEventKind,
    AgentRuntime,
    AgentStatus,
    FileAgentSessionStore,
    PermissionMode,
)
from app.agent_runtime.workspace_tools import loom_default_tools
from app.ai import AGENT_FAST_ROLE, ModelResponse


class CapturingPlatform:
    def __init__(self) -> None:
        self.requests = []

    def execute_chat(self, _profile_id, request):
        self.requests.append(request)
        return ModelResponse(text="done")


def test_model_requested_records_exposed_tool_names(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    platform = CapturingPlatform()
    store = FileAgentSessionStore(tmp_path / "state")
    runtime = AgentRuntime(platform=platform, store=store, tools=loom_default_tools())
    session = runtime.create_session(
        AGENT_FAST_ROLE.role_id,
        workspace_dir=project,
        permission_mode=PermissionMode.FULL_ACCESS,
    )

    result = runtime.start_turn(session.session_id, "List the exposed tools.")

    assert result.status is AgentStatus.COMPLETED
    assert platform.requests
    requested_tool_names = {tool.name for tool in platform.requests[0].tools}
    assert "exec" in requested_tool_names
    assert "apply_patch" in requested_tool_names

    model_requested = [
        event
        for event in store.events(session.session_id)
        if event.kind is AgentEventKind.MODEL_REQUESTED
    ][-1]
    diagnostic_names = model_requested.data["tool_names"]
    assert model_requested.data["tool_count"] == len(diagnostic_names)
    assert diagnostic_names == sorted(diagnostic_names)
    assert "exec" in diagnostic_names
    assert "apply_patch" in diagnostic_names
