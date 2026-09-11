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
from app.agent_runtime.tool_schema_budget import (
    plan_tool_schema_pressure,
)
from app.ai import AGENT_FAST_ROLE, ModelResponse, ToolCall


class RecordingPlatform:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def execute_chat(self, _profile_id, request):
        self.requests.append(request)
        if not self.responses:
            raise AssertionError("scripted platform ran out of responses")
        return self.responses.pop(0)


def _tool(
    name: str,
    *,
    description_size: int = 0,
    enum_size: int = 0,
    calls: list[str] | None = None,
):
    values = [f"value-{index:04d}-{'x' * 12}" for index in range(enum_size)]

    def handler(_context, arguments):
        value = str(arguments.get("value") or "")
        if calls is not None:
            calls.append(value)
        return ToolResult(ok=True, content=f"called:{name}:{value}")

    property_schema = {
        "type": "string",
        "description": "property help " + ("p" * description_size),
    }
    if values:
        property_schema["enum"] = values
    return AgentTool(
        name=name,
        description="tool help " + ("d" * description_size),
        input_schema={
            "type": "object",
            "title": "Verbose tool schema",
            "description": "schema help " + ("s" * description_size),
            "properties": {"value": property_schema},
            "required": ["value"],
            "additionalProperties": False,
        },
        handler=handler,
        exposure=ToolExposure.DIRECT,
    )


def test_schema_pressure_strips_annotations_without_changing_validation_shape():
    tools = tuple(
        _tool(f"verbose_{index}", description_size=1800)
        for index in range(3)
    )
    router = ToolRegistry(tools).router()

    plan = plan_tool_schema_pressure(
        router,
        max_schema_tokens=1600,
        allow_shedding=False,
    )

    assert plan.mode == "compact"
    assert not plan.omitted_names
    assert plan.planned_schema_tokens < plan.original_schema_tokens
    projected = plan.router.get("verbose_0")
    assert projected is not None
    assert projected.input_schema["required"] == ["value"]
    assert projected.input_schema["additionalProperties"] is False
    assert projected.input_schema["properties"]["value"]["type"] == "string"
    assert "description" not in projected.input_schema
    assert "description" not in projected.input_schema["properties"]["value"]
    assert "title" not in projected.input_schema


def test_schema_pressure_sheds_only_with_discovery_and_keeps_pinned_tool():
    calls: list[str] = []
    search = _tool("tool_search")
    target = _tool("zebra_special_action", enum_size=260, calls=calls)
    fillers = tuple(_tool(f"bulk_tool_{index}", enum_size=260) for index in range(6))
    router = ToolRegistry((search, target, *fillers)).router()

    plan = plan_tool_schema_pressure(
        router,
        max_schema_tokens=2800,
        pinned_names=("zebra_special_action",),
        allow_shedding=True,
    )

    visible = {tool.name for tool in plan.router.all()}
    assert plan.mode == "structural"
    assert "tool_search" in visible
    assert "zebra_special_action" in visible
    assert plan.omitted_names
    assert set(plan.omitted_names).isdisjoint({"tool_search", "zebra_special_action"})


def test_context_shed_direct_tool_can_be_searched_pinned_and_executed(tmp_path: Path):
    calls: list[str] = []
    target = _tool("zebra_special_action", enum_size=320, calls=calls)
    fillers = tuple(_tool(f"bulk_tool_{index}", enum_size=320) for index in range(8))
    platform = RecordingPlatform(
        [
            ModelResponse(
                tool_calls=(
                    ToolCall(
                        call_id="search-1",
                        name="tool_search",
                        arguments={"query": "zebra special action", "limit": 1},
                    ),
                )
            ),
            ModelResponse(
                tool_calls=(
                    ToolCall(
                        call_id="target-1",
                        name="zebra_special_action",
                        arguments={"value": "value-0001-xxxxxxxxxxxx"},
                    ),
                )
            ),
            ModelResponse(text="done"),
        ]
    )
    runtime = ToolSearchRuntime(
        platform=platform,
        store=FileAgentSessionStore(tmp_path / "state"),
        tools=ToolRegistry((target, *fillers)),
        mcp_servers=(),
        auto_configure_browser=False,
        auto_configure_web_search=False,
    )
    try:
        session = runtime.create_session(
            AGENT_FAST_ROLE.role_id,
            workspace_dir=tmp_path,
            permission_mode=PermissionMode.FULL_ACCESS,
        )
        result = runtime.start_turn(session.session_id, "Run the zebra special action.")

        assert result.status is AgentStatus.COMPLETED
        assert calls == ["value-0001-xxxxxxxxxxxx"]
        assert len(platform.requests) == 3
        first_names = {tool.name for tool in platform.requests[0].tools}
        second_names = {tool.name for tool in platform.requests[1].tools}
        assert "tool_search" in first_names
        assert "zebra_special_action" not in first_names
        assert "zebra_special_action" in second_names

        first_step_plan = next(
            event.data.get("tool_schema_plan")
            for event in runtime.events(session.session_id)
            if event.kind.value == "model_requested" and event.data.get("tool_schema_plan")
        )
        assert first_step_plan["omitted_count"] >= 1
        assert first_step_plan["planned_schema_tokens"] < first_step_plan["original_schema_tokens"]
    finally:
        runtime.close()
