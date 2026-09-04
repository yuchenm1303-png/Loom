from __future__ import annotations

from app.agent_runtime import PermissionMode, SandboxManager, SandboxPolicy, StepContext, build_world_state_envelope
from app.agent_runtime.workspace_tools import loom_default_tools


def test_world_state_digest_ignores_step_identity(tmp_path):
    workspace = tmp_path / "project"
    workspace.mkdir()
    tools = loom_default_tools().router()
    sandbox = SandboxManager(policy=SandboxPolicy.OFF).snapshot(
        permission_mode=PermissionMode.WORKSPACE,
        workspace=workspace,
    )
    first = StepContext.build(
        step_id="step-1",
        session_id="session-1",
        turn_id="turn-1",
        model_step=1,
        workspace_dir=str(workspace),
        profile_id="agent.fast",
        permission_mode=PermissionMode.WORKSPACE,
        tool_router=tools,
        sandbox_snapshot=sandbox,
    )
    second = StepContext.build(
        step_id="step-2",
        session_id="session-1",
        turn_id="turn-1",
        model_step=2,
        workspace_dir=str(workspace),
        profile_id="agent.fast",
        permission_mode=PermissionMode.WORKSPACE,
        tool_router=tools,
        sandbox_snapshot=sandbox,
    )

    one = build_world_state_envelope(first)
    two = build_world_state_envelope(second)

    assert one.digest == two.digest
    assert one.payload["identity"]["step_id"] != two.payload["identity"]["step_id"]
    assert one.payload["state_digest"] == one.digest
