from __future__ import annotations

from app.ai import ChatRequest, MessageRole, ModelResponse, ToolCall
from app.agent_runtime.contracts import PermissionMode, ToolEffect
from app.agent_runtime.orchestrator import ToolOrchestrator
from app.agent_runtime.runtime import AgentRuntime
from app.agent_runtime.step import StepContext
from app.agent_runtime.storage import FileAgentSessionStore
from app.agent_runtime.tools import AgentTool, ToolContext, ToolPolicy, ToolRegistry, ToolResult


class CapturePlatform:
    def __init__(self) -> None:
        self.requests: list[ChatRequest] = []

    def execute_chat(self, profile_id: str, request: ChatRequest) -> ModelResponse:
        self.requests.append(request)
        return ModelResponse(text="done")


def _tool(name: str, effect: ToolEffect) -> AgentTool:
    return AgentTool(
        name=name,
        description=f"General test capability {name}.",
        input_schema={"type": "object", "properties": {}, "additionalProperties": False},
        handler=lambda context, arguments: ToolResult(ok=True, content="ok"),
        effect=effect,
    )


def _step(permission_mode: PermissionMode) -> StepContext:
    registry = ToolRegistry(
        (
            _tool("inspect_anything", ToolEffect.READ_ONLY),
            _tool("general_exec", ToolEffect.SENSITIVE),
            _tool("change_anything", ToolEffect.MUTATING),
        )
    )
    return StepContext.build(
        step_id="step-1",
        session_id="session-1",
        turn_id="turn-1",
        model_step=1,
        workspace_dir=".",
        profile_id="agent.fast",
        permission_mode=permission_mode,
        tool_router=registry.router(),
    )


def _core_route_step(permission_mode: PermissionMode) -> StepContext:
    registry = ToolRegistry(
        (
            _tool("exec", ToolEffect.SENSITIVE),
            _tool("computer_status", ToolEffect.READ_ONLY),
            _tool("browser_navigate", ToolEffect.SENSITIVE),
        )
    )
    return StepContext.build(
        step_id="step-routes",
        session_id="session-routes",
        turn_id="turn-routes",
        model_step=1,
        workspace_dir=".",
        profile_id="agent.fast",
        permission_mode=permission_mode,
        tool_router=registry.router(),
    )


def test_capability_contract_uses_real_workspace_permission_decisions():
    orchestrator = ToolOrchestrator()
    step = _step(PermissionMode.WORKSPACE)

    contract = orchestrator.capability_contract(step)

    assert "permission_mode=workspace" in contract
    assert "inspect_anything[read_only]" in contract
    assert "change_anything[mutating]" in contract
    assert "approval=general_exec[sensitive]" in contract
    assert "deny=(none)" in contract
    assert "Tool names are not a capability ontology" in contract
    assert "Authorization is not capability" in contract
    assert "general-purpose tool may satisfy a request" in contract
    assert "Disambiguate user vocabulary from Loom subsystem names by context" in contract


def test_capability_contract_distinguishes_denied_from_unavailable():
    orchestrator = ToolOrchestrator()
    step = _step(PermissionMode.READ_ONLY)

    contract = orchestrator.capability_contract(step)

    assert "permission_mode=read-only" in contract
    assert "allow=inspect_anything[read_only]" in contract
    assert "approval=(none)" in contract
    deny_line = next(line for line in contract.splitlines() if line.startswith("deny="))
    assert "change_anything[mutating]" in deny_line
    assert "general_exec[sensitive]" in deny_line
    assert "blocked by the current permission mode, not evidence that Loom never supports" in contract


def test_capability_contract_routes_host_cli_and_gui_status_independently():
    orchestrator = ToolOrchestrator()
    contract = orchestrator.capability_contract(_core_route_step(PermissionMode.WORKSPACE))

    assert "host_system_read=exec:approval" in contract
    assert "memory, CPU, disk, processes, and network status" in contract
    assert "windows_gui_status=computer_status:allow" in contract
    assert "scope=GUI Computer Use only" in contract
    assert "Computer Use, screen capture, and GUI input are not prerequisites" in contract
    assert "A computer_status result is scoped only to the Windows GUI Computer Use subsystem" in contract
    assert "do not infer that exec, browser, filesystem, MCP, or other independent tools are disabled" in contract
    assert "Do not ask the user in prose to pre-authorize an approval-required tool" in contract
    assert "answer by capability surface" in contract


def test_capability_contract_marks_host_cli_blocked_in_read_only_mode_without_calling_it_unavailable():
    orchestrator = ToolOrchestrator()
    contract = orchestrator.capability_contract(_core_route_step(PermissionMode.READ_ONLY))

    assert "host_system_read=exec:deny" in contract
    assert "windows_gui_status=computer_status:allow" in contract
    assert "A tool under deny is blocked by the current permission mode" in contract
    assert "Say a capability is unavailable only from runtime evidence" in contract


def test_capability_contract_and_execution_share_one_authorization_evaluator():
    orchestrator = ToolOrchestrator()
    legacy_policy = ToolPolicy()

    for mode in PermissionMode:
        step = _step(mode)
        for tool in step.tool_router.all():
            expected, expected_reason = orchestrator.evaluate_tool(
                step,
                tool,
                legacy_policy=legacy_policy,
            )
            prepared = orchestrator.prepare(
                step,
                ToolCall(call_id=f"{mode.value}-{tool.name}", name=tool.name, arguments={}),
                legacy_policy=legacy_policy,
            )
            assert prepared.decision is expected
            assert prepared.reason == expected_reason


def test_runtime_injects_fresh_contract_without_persisting_it(tmp_path):
    platform = CapturePlatform()
    store = FileAgentSessionStore(tmp_path / "state")
    registry = ToolRegistry(
        (
            _tool("host_probe", ToolEffect.READ_ONLY),
            _tool("general_exec", ToolEffect.SENSITIVE),
            _tool("exec", ToolEffect.SENSITIVE),
            _tool("computer_status", ToolEffect.READ_ONLY),
        )
    )
    runtime = AgentRuntime(platform=platform, store=store, tools=registry)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    session = runtime.create_session(
        "agent.fast",
        system_prompt="CUSTOM BASE PROMPT",
        workspace_dir=workspace,
        permission_mode=PermissionMode.WORKSPACE,
    )

    result = runtime.start_turn(session.session_id, "Can you inspect something on this host?")

    assert result.final_text == "done"
    assert len(platform.requests) == 1
    request = platform.requests[0]
    system = request.messages[0]
    assert system.role is MessageRole.SYSTEM
    assert isinstance(system.content, str)
    assert system.content.startswith("CUSTOM BASE PROMPT\n\n<loom_capability_contract>")
    assert "approval=general_exec[sensitive]" in system.content
    assert "exec[sensitive]" in system.content
    assert "computer_status[read_only]" in system.content
    assert "host_probe[read_only]" in system.content
    assert "host_system_read=exec:approval" in system.content
    assert "windows_gui_status=computer_status:allow" in system.content
    assert "Do not infer host, OS, network, filesystem, process, browser, or GUI inaccessibility" in system.content
    assert "Do not ask the user in prose to pre-authorize an approval-required tool" in system.content

    persisted = runtime.get_session(session.session_id)
    assert persisted.system_prompt == "CUSTOM BASE PROMPT"
    assert "loom_capability_contract" not in persisted.system_prompt
    runtime.close()
