from __future__ import annotations

import pytest

from app.agent_runtime.contracts import AgentRunResult, AgentStatus, ToolEffect
from app.agent_runtime.mcp_configured_runtime import ConfiguredMCPRuntime
from app.agent_runtime.mcp_runtime import (
    MCPServerConfig,
    MCPToolDescriptor,
    McpBinding,
    _ConnectedServer,
)
from app.agent_runtime.storage import FileAgentSessionStore
from app.agent_runtime.tools import ToolExposure, ToolRegistry


class _NoModelPlatform:
    def execute_chat(self, profile_id, request):
        raise AssertionError("integration contract should not sample the model")


class _Runner:
    pass


class _BindingSequence:
    def __init__(self, *bindings):
        self._bindings = list(bindings)

    def capture_binding(self):
        if len(self._bindings) > 1:
            return self._bindings.pop(0)
        return self._bindings[0]


def _binding(*, revision: int, remote_name: str = "echo") -> McpBinding:
    config = MCPServerConfig(name="demo", transport="stdio", command="python")
    descriptor = MCPToolDescriptor(
        canonical_name=f"mcp.demo.{remote_name}",
        server_name="demo",
        remote_name=remote_name,
        description=f"[MCP:demo] {remote_name}",
        input_schema={"type": "object", "properties": {}},
        effect=ToolEffect.READ_ONLY,
        exposure=ToolExposure.DIRECT,
    )
    connected = _ConnectedServer(
        config=config,
        client=object(),
        descriptors=(descriptor,),
        protocol_version="v1",
        server_info="demo@test",
        catalog_revision=revision,
    )
    return McpBinding((connected,), runner=_Runner(), max_result_chars=2000)


def _runtime(tmp_path) -> ConfiguredMCPRuntime:
    return ConfiguredMCPRuntime(
        platform=_NoModelPlatform(),
        store=FileAgentSessionStore(tmp_path / "state"),
        tools=ToolRegistry(),
        mcp_servers=(),
        auto_connect_mcp=False,
        auto_configure_browser=False,
        auto_configure_web_search=False,
    )


def test_step_captures_exact_mcp_binding_and_same_router_authority(tmp_path):
    runtime = _runtime(tmp_path)
    workspace = tmp_path / "project"
    workspace.mkdir()
    first = _binding(revision=1, remote_name="echo")
    second = _binding(revision=2, remote_name="new_echo")
    runtime.mcp_clients = _BindingSequence(first, second)
    session = runtime.create_session("agent.fast", workspace_dir=workspace)
    session.current_turn_id = "turn-1"

    step_one = runtime._build_step_context(session, next_model_step=True)
    step_two = runtime._build_step_context(session, next_model_step=True)

    assert step_one.mcp_binding is first
    assert step_two.mcp_binding is second
    assert step_one.tool_router.get("mcp.demo.echo") is not None
    assert step_one.tool_router.get("mcp.demo.new_echo") is None
    assert step_two.tool_router.get("mcp.demo.new_echo") is not None
    assert step_one.tool_router.get("mcp.demo.echo").binding_key == first.identity
    assert step_two.tool_router.get("mcp.demo.new_echo").binding_key == second.identity
    # The legacy JSON field remains diagnostic metadata, not authority.
    assert first.identity in step_one.request_state.mcp_binding_json
    assert second.identity in step_two.request_state.mcp_binding_json
    runtime.close()


def test_safe_handoff_recovers_same_turn_without_new_user_input(tmp_path):
    runtime = _runtime(tmp_path)
    runtime.mcp_clients = _BindingSequence(_binding(revision=1))
    workspace = tmp_path / "project"
    workspace.mkdir()
    session = runtime.create_session("agent.fast", workspace_dir=workspace)
    session.current_turn_id = "existing-turn"
    session.status = AgentStatus.RUNNING
    session.messages = []
    runtime.store.save(session)

    observed = {}

    def drive(current, token):
        observed["turn_id"] = current.current_turn_id
        observed["messages"] = list(current.messages)
        current.status = AgentStatus.COMPLETED
        return AgentRunResult(
            session_id=current.session_id,
            turn_id=current.current_turn_id,
            status=current.status,
        )

    runtime._drive = drive
    try:
        result = runtime.recover_turn_if_idle(session.session_id, "existing-turn")
        assert result.status is AgentStatus.COMPLETED
        assert observed == {"turn_id": "existing-turn", "messages": []}
    finally:
        runtime.close()


def test_safe_handoff_refuses_pending_approval_without_original_process_authority(tmp_path):
    runtime = _runtime(tmp_path)
    runtime.mcp_clients = _BindingSequence(_binding(revision=1))
    workspace = tmp_path / "project"
    workspace.mkdir()
    session = runtime.create_session("agent.fast", workspace_dir=workspace)
    session.current_turn_id = "approval-turn"
    session.status = AgentStatus.WAITING_APPROVAL
    runtime.store.save(session)

    try:
        with pytest.raises(RuntimeError, match="original captured StepContext"):
            runtime.recover_turn_if_idle(session.session_id, "approval-turn")
    finally:
        runtime.close()