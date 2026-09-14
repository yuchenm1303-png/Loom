from __future__ import annotations

from app.ai import AGENT_FAST_ROLE
from app.agent_runtime import FileAgentSessionStore, PermissionMode, ToolRegistry, ToolSearchRuntime
from app.agent_runtime.sandbox import SandboxManager, SandboxPolicy
import app.agent_runtime.tool_search_runtime as tool_search_runtime_module


class _UnusedPlatform:
    def execute_chat(self, _profile_id, _request):  # pragma: no cover - build-step test only
        raise AssertionError("model platform must not be called while building a StepContext")


def test_tool_search_schema_planner_reuses_parent_frozen_context_limits(monkeypatch, tmp_path):
    runtime = ToolSearchRuntime(
        platform=_UnusedPlatform(),
        store=FileAgentSessionStore(tmp_path / "state"),
        tools=ToolRegistry(),
        mcp_servers=(),
        auto_configure_browser=False,
        auto_configure_web_search=False,
        sandbox_manager=SandboxManager(
            policy=SandboxPolicy.OFF,
            system_name="Linux",
            probe_backend=False,
        ),
    )
    try:
        workspace = tmp_path / "project"
        workspace.mkdir()
        session = runtime.create_session(
            AGENT_FAST_ROLE.role_id,
            workspace_dir=workspace,
            permission_mode=PermissionMode.APPROVAL,
        )

        def reject_second_resolution(*_args, **_kwargs):
            raise AssertionError("ToolSearchRuntime must reuse StepContext's frozen context limits")

        monkeypatch.setattr(
            tool_search_runtime_module,
            "resolve_context_limits",
            reject_second_resolution,
        )
        step = runtime._build_step_context(session, next_model_step=True)

        assert step.request_state.captured is True
        assert step.request_state.context_limits is not None
        plan = runtime.tool_schema_plan(
            session.session_id,
            session.current_turn_id,
            step.step_id,
        )
        assert plan is not None
    finally:
        runtime.close()
