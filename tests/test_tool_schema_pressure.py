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
    schema_token_budget,
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


def test_schema_pressure_keeps_every_tool_even_above_soft_budget():
    calls: list[str] = []
    search = _tool("tool_search")
    target = _tool("zebra_special_action", enum_size=260, calls=calls)
    fillers = tuple(_tool(f"bulk_tool_{index}", enum_size=260) for index in range(6))
    router = ToolRegistry((search, target, *fillers)).router()

    plan = plan_tool_schema_pressure(
        router,
        max_schema_tokens=2800,
        pinned_names=("zebra_special_action",),
    )

    visible = {tool.name for tool in plan.router.all()}
    assert plan.mode == "compact"
    assert "tool_search" in visible
    assert "zebra_special_action" in visible
    assert not plan.omitted_names
    assert visible == {tool.name for tool in router.all()}


def test_definitions_keep_their_ceiling_until_the_conversation_needs_the_room():
    budget = 50_790  # a real 64k-window session's input budget

    # Nothing measured, and a roomy conversation, both keep the plain ceiling.
    assert schema_token_budget(budget) == 10_158
    assert schema_token_budget(budget, conversation_tokens=budget // 2) == 10_158

    # Past the ceiling's worth of headroom, definitions start giving way...
    squeezed = schema_token_budget(budget, conversation_tokens=48_758)
    assert squeezed == 2_032

    # ...but never all the way: an agent with no callable tools is a worse
    # outcome than a crowded request the hard budget can report precisely.
    assert schema_token_budget(budget, conversation_tokens=budget * 2) == 1_200


def test_crowded_conversation_compacts_schema_without_removing_tools():
    """History growth may change schema verbosity, never callable identities."""

    search = _tool("tool_search")
    # Sized to sit inside the plain ceiling, so only the conversation's own
    # pressure can be what moves the planner here.
    fillers = tuple(_tool(f"bulk_tool_{index}", enum_size=260) for index in range(4))
    router = ToolRegistry((search, *fillers)).router()
    budget = 50_790

    roomy = plan_tool_schema_pressure(
        router,
        max_schema_tokens=schema_token_budget(budget, conversation_tokens=10_000),
    )
    crowded = plan_tool_schema_pressure(
        router,
        max_schema_tokens=schema_token_budget(budget, conversation_tokens=48_758),
    )

    # With room, every tool stays directly callable at full fidelity.
    assert roomy.mode == "full"
    assert roomy.omitted_names == ()
    # Crowded, argument annotations are compacted and every handler remains.
    assert crowded.mode == "compact"
    assert not crowded.omitted_names
    assert crowded.planned_schema_tokens <= roomy.planned_schema_tokens
    assert {tool.name for tool in crowded.router.all()} == {tool.name for tool in roomy.router.all()}
    # Explicit deferred discovery also stays available.
    assert "tool_search" in {tool.name for tool in crowded.router.all()}


def test_a_squeezed_runtime_keeps_web_search_resident():
    search = _tool("tool_search")
    web = _tool("web_search", description_size=20)
    fillers = tuple(_tool(f"bulk_tool_{index}", enum_size=260) for index in range(6))
    router = ToolRegistry((search, web, *fillers)).router()

    plan = plan_tool_schema_pressure(
        router,
        max_schema_tokens=1800,
    )

    visible = {tool.name for tool in plan.router.all()}
    assert plan.mode == "compact"
    assert "tool_search" in visible
    assert "web_search" in visible


def test_a_squeezed_browser_keeps_the_ordinary_driving_loop():
    """Schema pressure must preserve normal DOM driving, not every escape hatch.

    Specialized/high-authority capabilities such as browser_eval are deliberately
    discoverable through tool_search rather than guaranteed resident. The fixed
    surface needs enough verbs to open and inspect a page, navigate, click, type,
    select, scroll, and request visual fallback when DOM state is insufficient.
    """

    from app.agent_runtime.browser_runtime import BrowserRuntime
    from app.agent_runtime.browser_security import BrowserSecurityPolicy
    from app.agent_runtime.sandbox import SandboxManager, SandboxPolicy
    from app.agent_runtime.workspace_tools import loom_default_tools

    class _Noop:
        def execute_chat(self, _profile_id, _request):
            raise AssertionError("the model is never called here")

    import tempfile

    root = Path(tempfile.mkdtemp())
    runtime = BrowserRuntime(
        platform=_Noop(),
        store=FileAgentSessionStore(root / "state"),
        tools=loom_default_tools(),
        sandbox_manager=SandboxManager(policy=SandboxPolicy.OFF),
        web_search_provider=None,
        auto_configure_web_search=False,
        auto_configure_browser=True,
        browser_security_policy=BrowserSecurityPolicy(resolve_dns=False),
    )
    try:
        # Discovery is independent of the direct tool set.
        runtime.tools = ToolRegistry(tuple(runtime.tools.all()) + (_tool("tool_search"),))
        router = runtime.tools.router(capability_settings={})

        plan = plan_tool_schema_pressure(router, max_schema_tokens=3500)

        visible = {tool.name for tool in plan.router.all()}
        assert plan.mode == "compact"
        assert not plan.omitted_names
        assert {
            "browser_open",
            "browser_state",
            "browser_navigate",
            "browser_click",
            "browser_type",
            "browser_select",
            "browser_scroll",
            "browser_screenshot",
        }.issubset(visible)
    finally:
        runtime.close()


def test_a_crowded_history_reaches_the_planner_through_the_runtime(tmp_path: Path):
    """The wiring, not just the arithmetic: pressure has to arrive at the plan.

    ``schema_token_budget`` can be perfectly tuned and still change nothing if
    the runtime never tells it how big the conversation is.
    """

    from app.ai import AIMessage, MessageRole

    tools = tuple(_tool(f"bulk_tool_{index}", enum_size=90) for index in range(8))
    runtime = ToolSearchRuntime(
        platform=RecordingPlatform([ModelResponse(text="unused")]),
        store=FileAgentSessionStore(tmp_path / "state"),
        tools=ToolRegistry(tools),
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
        empty = runtime._conversation_pressure(session)

        session.messages = [
            AIMessage(role=MessageRole.USER, content="x" * 4_000)
            for _ in range(40)
        ]
        crowded = runtime._conversation_pressure(session)

        assert empty == 0 or empty < crowded
        # ~160k characters of history is real pressure, not a rounding artefact.
        assert crowded > 40_000
    finally:
        runtime.close()


def test_explicitly_deferred_tool_can_be_searched_activated_and_executed(tmp_path: Path):
    calls: list[str] = []
    from dataclasses import replace
    target = replace(_tool("zebra_special_action", enum_size=320, calls=calls), exposure=ToolExposure.DEFERRED)
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
            for event in runtime.store.events(session.session_id)
            if event.kind.value == "model_requested" and event.data.get("tool_schema_plan")
        )
        assert first_step_plan["omitted_count"] == 0
        assert first_step_plan["planned_schema_tokens"] < first_step_plan["original_schema_tokens"]
    finally:
        runtime.close()


def test_long_history_does_not_remove_direct_execution_capabilities(tmp_path):
    from app.ai import AIMessage, MessageRole
    tools = tuple(_tool(f"action_{index}", enum_size=260) for index in range(7))
    runtime = ToolSearchRuntime(platform=RecordingPlatform([]),
        store=FileAgentSessionStore(tmp_path / "state"), tools=ToolRegistry(tools),
        mcp_servers=(), auto_configure_browser=False, auto_configure_web_search=False)
    session = runtime.create_session(AGENT_FAST_ROLE.role_id, workspace_dir=tmp_path,
        permission_mode=PermissionMode.FULL_ACCESS)
    try:
        session.messages = [AIMessage(role=MessageRole.USER, content="x" * 400000)]
        step = runtime._build_step_context(session, next_model_step=True)
        assert ({tool.name for tool in tools} | {"tool_search"}).issubset(
            {tool.name for tool in step.tool_router.all()})
        assert runtime.tool_schema_plan(session.session_id, session.current_turn_id, step.step_id)["omitted_names"] == []
    finally:
        runtime.close()


def test_cold_started_agent_compacts_history_then_executes_direct_tool(tmp_path):
    import argparse
    from types import SimpleNamespace
    from loom_cli import _build_runtime
    from test_unknown_context_window_semantics import _history_over
    runtime, store, _ = _build_runtime(argparse.Namespace(provider="openai-compatible",
        base_url="https://example.test/v1", model="declared", allow_unconfigured_model=True,
        vision=False, timeout=120, home=str(tmp_path / "state"),
        context_limits={"contextWindowTokens": 512000, "workingContextTokens": 64000}))
    calls, requests = [], []
    runtime.tools.register(_tool("zebra_special_action", enum_size=260, calls=calls))
    actors = iter([ModelResponse(tool_calls=(ToolCall("action", "zebra_special_action",
        {"value": "value-0001-xxxxxxxxxxxx"}),)), ModelResponse(text="Action completed.")])
    def execute(_profile, request):
        requests.append(request)
        return next(actors) if request.tools else ModelResponse(text="Prior work is archived. Execute the requested action.")
    runtime.platform = SimpleNamespace(registry=runtime.platform.registry, execute_chat=execute)
    session = runtime.create_session(AGENT_FAST_ROLE.role_id, workspace_dir=tmp_path,
        permission_mode=PermissionMode.FULL_ACCESS)
    session.messages = list(_history_over(150000))
    store.save(session)
    try:
        result = runtime.start_turn(session.session_id, "Execute the zebra special action.")
        assert result.status is AgentStatus.COMPLETED
        assert calls == ["value-0001-xxxxxxxxxxxx"]
        events = store.events(session.session_id)
        assert any(e.kind.value == "context_checkpointed" for e in events)
        assert not any(e.kind.value in {"tool_failed", "turn_failed"} for e in events)
        for request in requests:
            if request.tools:
                assert "zebra_special_action" in {tool.name for tool in request.tools}
        for event in events:
            if event.kind.value == "model_requested":
                assert event.data["tool_schema_plan"]["omitted_names"] == []
    finally:
        runtime.close()
