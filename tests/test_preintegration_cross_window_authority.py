from __future__ import annotations

from pathlib import Path

from app.agent_runtime import (
    AgentStatus,
    AgentTool,
    FileAgentSessionStore,
    PermissionMode,
    ToolExposure,
    ToolRegistry,
    ToolResult,
    ToolSearchRuntime,
)
from app.agent_runtime.contracts import ToolEffect
from app.agent_runtime.mcp_runtime import (
    MCPClientManager,
    MCPServerConfig,
    MCPToolDescriptor,
    _ConnectedServer,
)
from app.ai import AGENT_FAST_ROLE, ModelResponse, ToolCall


class _Platform:
    def __init__(self, responses=()):
        self.responses = list(responses)
        self.requests = []

    def execute_chat(self, _profile_id, request):
        self.requests.append(request)
        if not self.responses:
            raise AssertionError("scripted platform ran out of responses")
        return self.responses.pop(0)


class _Runner:
    def run(self, *_args, **_kwargs):
        raise AssertionError("MCP call should not execute in this contract test")

    def close(self):
        return None


def _tool(name: str, *, exposure: ToolExposure, effect: ToolEffect, calls: list[str]):
    def handler(_context, arguments):
        calls.append(str(arguments.get("value") or ""))
        return ToolResult(ok=True, content="ok")

    return AgentTool(
        name=name,
        description=f"synthetic {name}",
        input_schema={
            "type": "object",
            "properties": {"value": {"type": "string"}},
            "required": ["value"],
            "additionalProperties": False,
        },
        handler=handler,
        effect=effect,
        exposure=exposure,
    )


def test_same_process_approval_resume_reuses_sampled_step(tmp_path: Path):
    calls: list[str] = []
    sensitive = _tool(
        "external.write_record",
        exposure=ToolExposure.DEFERRED,
        effect=ToolEffect.SENSITIVE,
        calls=calls,
    )
    platform = _Platform(
        (
            ModelResponse(
                tool_calls=(
                    ToolCall(
                        call_id="search-1",
                        name="tool_search",
                        arguments={"query": "external write record", "limit": 1},
                    ),
                )
            ),
            ModelResponse(
                tool_calls=(
                    ToolCall(
                        call_id="write-1",
                        name="external.write_record",
                        arguments={"value": "approved"},
                    ),
                )
            ),
            ModelResponse(text="done"),
        )
    )
    runtime = ToolSearchRuntime(
        platform=platform,
        store=FileAgentSessionStore(tmp_path / "state"),
        tools=ToolRegistry((sensitive,)),
        mcp_servers=(),
        auto_configure_browser=False,
        auto_configure_web_search=False,
    )
    try:
        session = runtime.create_session(
            AGENT_FAST_ROLE.role_id,
            workspace_dir=tmp_path,
            permission_mode=PermissionMode.APPROVAL,
        )
        waiting = runtime.start_turn(session.session_id, "Write the record.")
        assert waiting.status is AgentStatus.WAITING_APPROVAL
        assert calls == []

        original_build = runtime._build_step_context

        def guarded_build(session_value, *, next_model_step: bool, step_id=None):
            if not next_model_step:
                raise AssertionError("approval resume rebuilt the sampled StepContext")
            return original_build(
                session_value,
                next_model_step=next_model_step,
                step_id=step_id,
            )

        runtime._build_step_context = guarded_build
        completed = runtime.resume_approval(
            session.session_id,
            "write-1",
            approved=True,
        )

        assert completed.status is AgentStatus.COMPLETED
        assert completed.final_text == "done"
        assert calls == ["approved"]
    finally:
        runtime.close()


def test_tool_search_activation_keeps_exact_mcp_prepared_call(tmp_path: Path):
    stale_calls: list[str] = []
    stale = _tool(
        "mcp.demo.echo",
        exposure=ToolExposure.DIRECT,
        effect=ToolEffect.READ_ONLY,
        calls=stale_calls,
    )
    stale = AgentTool(
        name=stale.name,
        description=stale.description,
        input_schema=stale.input_schema,
        handler=stale.handler,
        effect=stale.effect,
        exposure=stale.exposure,
        binding_key="mcp-binding:stale-registry-projection",
    )
    runtime = ToolSearchRuntime(
        platform=_Platform(),
        store=FileAgentSessionStore(tmp_path / "state"),
        tools=ToolRegistry((stale,)),
        mcp_servers=(),
        auto_configure_browser=False,
        auto_configure_web_search=False,
    )

    manager = MCPClientManager(())
    manager._runner = _Runner()
    descriptor = MCPToolDescriptor(
        canonical_name="mcp.demo.echo",
        server_name="demo",
        remote_name="echo",
        description="[MCP:demo] echo",
        input_schema={"type": "object", "properties": {}},
        effect=ToolEffect.READ_ONLY,
        exposure=ToolExposure.DIRECT,
    )
    manager._servers["demo"] = _ConnectedServer(
        config=MCPServerConfig(name="demo", transport="stdio", command="python"),
        client=object(),
        descriptors=(descriptor,),
        protocol_version="v1",
        server_info="demo@test",
    )
    runtime.mcp_clients = manager

    try:
        session = runtime.create_session(
            AGENT_FAST_ROLE.role_id,
            workspace_dir=tmp_path,
            permission_mode=PermissionMode.FULL_ACCESS,
        )
        runtime._activate_deferred_name(
            session.session_id,
            session.current_turn_id,
            "mcp.demo.echo",
        )
        step = runtime._build_step_context(session, next_model_step=True)

        assert step.mcp_binding is not None
        prepared = step.mcp_binding.prepare_call("demo", "echo")
        assert prepared is not None
        selected = step.tool_router.get("mcp.demo.echo")
        assert selected is not None
        assert selected.binding_key == step.mcp_binding.identity
        assert selected.binding_key != stale.binding_key
        assert selected.handler is not stale.handler
        assert selected.handler.__kwdefaults__["_prepared"] is prepared
        assert stale_calls == []
    finally:
        runtime.close()
