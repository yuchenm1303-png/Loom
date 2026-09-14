from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from app.agent_runtime import (
    AgentStatus,
    AgentTool,
    FileAgentSessionStore,
    PermissionMode,
    ToolEffect,
    ToolExposure,
    ToolRegistry,
    ToolResult,
    ToolSearchRuntime,
)
from app.agent_runtime.execution_binding import binding_digest
from app.agent_runtime.permissions import permission_snapshot
from app.agent_runtime.step import StepContext
from app.agent_runtime.tool_schema_budget import plan_tool_schema_pressure
from app.agent_runtime.tools import ToolRouter
from app.ai import AGENT_FAST_ROLE, ModelResponse, ToolCall


class BarePlatform:
    pass


class RecordingPlatform:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def execute_chat(self, _profile_id, request):
        self.requests.append(request)
        if not self.responses:
            raise AssertionError("scripted platform ran out of responses")
        return self.responses.pop(0)


def _step(tool: AgentTool, workspace: Path) -> StepContext:
    permissions = permission_snapshot(PermissionMode.APPROVAL)
    return StepContext.build(
        step_id="step-1",
        session_id="session-1",
        turn_id="turn-1",
        model_step=1,
        workspace_dir=str(workspace),
        profile_id="agent.fast",
        permission_mode=permissions.mode,
        permissions=permissions,
        tool_router=ToolRouter((tool,)),
    )


def _sensitive_tool(
    name: str,
    *,
    enum_size: int = 0,
    calls: list[str] | None = None,
) -> AgentTool:
    values = [f"value-{index:04d}-{'x' * 12}" for index in range(enum_size)]

    def handler(_context, arguments):
        value = str(arguments.get("value") or "")
        if calls is not None:
            calls.append(value)
        return ToolResult(ok=True, content=f"called:{name}:{value}")

    value_schema = {
        "type": "string",
        "description": "Human-facing help for the value.",
    }
    if values:
        value_schema["enum"] = values
    return AgentTool(
        name=name,
        description=(
            "A deliberately verbose human-facing description used to verify that "
            "request-only prompt projection is not part of approval identity. " * 8
        ),
        input_schema={
            "type": "object",
            "title": "Verbose approval schema",
            "description": "Prompt-only schema documentation.",
            "properties": {"value": value_schema},
            "required": ["value"],
            "additionalProperties": False,
        },
        handler=handler,
        effect=ToolEffect.SENSITIVE,
        exposure=ToolExposure.DIRECT,
    )


def test_binding_digest_ignores_prompt_only_schema_projection(tmp_path: Path):
    tool = _sensitive_tool("sensitive_action", enum_size=16)
    full_step = _step(tool, tmp_path)
    plan = plan_tool_schema_pressure(
        ToolRouter((tool,)),
        max_schema_tokens=1,
        allow_shedding=False,
    )
    projected = plan.router.get(tool.name)
    assert projected is not None
    assert projected.description != tool.description
    assert "description" not in projected.input_schema

    projected_step = replace(full_step, tool_router=plan.router)

    assert binding_digest(full_step, tool, BarePlatform()) == binding_digest(
        projected_step,
        projected,
        BarePlatform(),
    )


def test_binding_digest_still_changes_for_validating_schema_change(tmp_path: Path):
    tool = _sensitive_tool("sensitive_action", enum_size=2)
    changed_schema = dict(tool.input_schema)
    changed_properties = dict(changed_schema["properties"])
    changed_value = dict(changed_properties["value"])
    changed_value["enum"] = ["different"]
    changed_properties["value"] = changed_value
    changed_schema["properties"] = changed_properties
    changed = replace(tool, input_schema=changed_schema)

    assert binding_digest(_step(tool, tmp_path), tool, BarePlatform()) != binding_digest(
        _step(changed, tmp_path),
        changed,
        BarePlatform(),
    )


def test_schema_projected_sensitive_tool_fails_closed_after_restart(tmp_path: Path):
    calls: list[str] = []
    target = _sensitive_tool("zebra_sensitive_action", enum_size=320, calls=calls)
    fillers = tuple(
        AgentTool(
            name=f"bulk_tool_{index}",
            description="bulk schema",
            input_schema={
                "type": "object",
                "properties": {
                    "value": {
                        "type": "string",
                        "enum": [f"bulk-{item:04d}-{'y' * 12}" for item in range(320)],
                    }
                },
                "required": ["value"],
                "additionalProperties": False,
            },
            handler=lambda _context, _arguments: ToolResult(ok=True, content="bulk"),
            exposure=ToolExposure.DIRECT,
        )
        for index in range(8)
    )
    store = FileAgentSessionStore(tmp_path / "state")
    first_platform = RecordingPlatform(
        [
            ModelResponse(
                tool_calls=(
                    ToolCall(
                        call_id="search-1",
                        name="tool_search",
                        arguments={"query": "zebra sensitive action", "limit": 1},
                    ),
                )
            ),
            ModelResponse(
                tool_calls=(
                    ToolCall(
                        call_id="sensitive-1",
                        name="zebra_sensitive_action",
                        arguments={"value": "value-0001-xxxxxxxxxxxx"},
                    ),
                )
            ),
        ]
    )
    runtime1 = ToolSearchRuntime(
        platform=first_platform,
        store=store,
        tools=ToolRegistry((target, *fillers)),
        mcp_servers=(),
        auto_configure_browser=False,
        auto_configure_web_search=False,
    )
    session = runtime1.create_session(
        AGENT_FAST_ROLE.role_id,
        workspace_dir=tmp_path,
        permission_mode=PermissionMode.APPROVAL,
    )
    waiting = runtime1.start_turn(session.session_id, "Run the zebra sensitive action.")
    assert waiting.status is AgentStatus.WAITING_APPROVAL
    assert calls == []
    assert "zebra_sensitive_action" not in {tool.name for tool in first_platform.requests[0].tools}
    assert "zebra_sensitive_action" in {tool.name for tool in first_platform.requests[1].tools}
    runtime1.close()

    # Approval authority is process-local because it includes the exact sampled
    # StepContext/router. A new runtime must not reconstruct equivalent-looking
    # authority from the durable schema, even when prompt projection is removed.
    second_platform = RecordingPlatform([ModelResponse(text="must not run")])
    runtime2 = ToolSearchRuntime(
        platform=second_platform,
        store=store,
        tools=ToolRegistry((target,)),
        mcp_servers=(),
        auto_configure_browser=False,
        auto_configure_web_search=False,
    )
    try:
        with pytest.raises(RuntimeError, match="captured step context is unavailable"):
            runtime2.resume_approval(
                session.session_id,
                "sensitive-1",
                approved=True,
            )
        assert calls == []
        assert second_platform.requests == []
    finally:
        runtime2.close()