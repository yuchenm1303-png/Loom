from __future__ import annotations

from app.ai import ChatRequest, MessageRole, ModelResponse, ToolCall
from app.agent_runtime.contracts import AgentStatus, PermissionMode, ToolEffect
from app.agent_runtime.orchestrator import ToolOrchestrator
from app.agent_runtime.runtime import AgentRuntime
from app.agent_runtime.step import StepContext
from app.agent_runtime.storage import FileAgentSessionStore
from app.agent_runtime.tools import AgentTool, ToolPolicy, ToolRegistry, ToolResult


class CapturePlatform:
    def __init__(self) -> None:
        self.requests: list[ChatRequest] = []

    def execute_chat(self, profile_id: str, request: ChatRequest) -> ModelResponse:
        self.requests.append(request)
        return ModelResponse(text="done")


class OneToolPlatform:
    def __init__(self, tool_name: str) -> None:
        self.tool_name = tool_name
        self.requests: list[ChatRequest] = []
        self.calls = 0

    def execute_chat(self, profile_id: str, request: ChatRequest) -> ModelResponse:
        self.requests.append(request)
        self.calls += 1
        if self.calls == 1:
            return ModelResponse(
                tool_calls=(
                    ToolCall(
                        call_id="call-1",
                        name=self.tool_name,
                        arguments={},
                    ),
                )
            )
        return ModelResponse(text="done")


def _tool(name: str, effect: ToolEffect, *, ran: list[str] | None = None) -> AgentTool:
    def handler(context, arguments):
        if ran is not None:
            ran.append(name)
        return ToolResult(ok=True, content="ok")

    return AgentTool(
        name=name,
        description=f"General test capability {name}.",
        input_schema={"type": "object", "properties": {}, "additionalProperties": False},
        handler=handler,
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


def test_model_harness_contract_is_tool_first_with_policy_specific_exec_guidance():
    orchestrator = ToolOrchestrator()

    workspace_contract = orchestrator.capability_contract(_step(PermissionMode.WORKSPACE))
    read_only_contract = orchestrator.capability_contract(_step(PermissionMode.READ_ONLY))

    for contract in (workspace_contract, read_only_contract):
        assert contract.startswith("<loom_tool_harness>")
        assert "tool definitions attached to this model request are the authoritative capability surface" in contract
        assert "issue the tool call directly" in contract
        assert "Do not ask the user to pre-authorize it in prose" in contract
        assert "runtime will allow it, request approval, or deny it" in contract
        assert "status or failure is scoped to that tool or subsystem" in contract
        assert "tool_search" in contract
        assert "permission_mode=" not in contract
        assert "filesystem_access=" not in contract
        assert "sandbox=" not in contract
        assert "allow=" not in contract
        assert "approval=" not in contract
        assert "deny=" not in contract
        assert "inspect_anything" not in contract
        assert "general_exec" not in contract
        assert "change_anything" not in contract

    assert workspace_contract != read_only_contract
    assert "prefer sandbox_permissions=with_additional_permissions" in workspace_contract
    assert "Approval policy is never" in read_only_contract


def test_authorization_and_execution_share_one_runtime_evaluator():
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


def test_runtime_injects_tool_harness_without_persisting_policy_matrix(tmp_path):
    platform = CapturePlatform()
    store = FileAgentSessionStore(tmp_path / "state")
    registry = ToolRegistry(
        (
            _tool("host_probe", ToolEffect.READ_ONLY),
            _tool("general_exec", ToolEffect.SENSITIVE),
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
    assert system.content.startswith("CUSTOM BASE PROMPT\n\n<loom_tool_harness>")
    assert "authoritative capability surface" in system.content
    assert "pre-authorize" in system.content
    assert "permission_mode=" not in system.content
    assert "allow=" not in system.content
    assert "approval=" not in system.content
    assert "deny=" not in system.content
    assert "host_probe" not in system.content
    assert "general_exec" not in system.content
    assert "computer_status" not in system.content

    # The exact current capability surface is already represented structurally
    # by the request's tool definitions, not duplicated into prompt prose.
    assert {tool.name for tool in request.tools} == {
        "computer_status",
        "general_exec",
        "host_probe",
    }

    persisted = runtime.get_session(session.session_id)
    assert persisted.system_prompt == "CUSTOM BASE PROMPT"
    assert "loom_tool_harness" not in persisted.system_prompt
    runtime.close()


def test_model_emits_tool_call_before_workspace_approval_and_runtime_owns_consent(tmp_path):
    ran: list[str] = []
    platform = OneToolPlatform("general_exec")
    store = FileAgentSessionStore(tmp_path / "state")
    runtime = AgentRuntime(
        platform=platform,
        store=store,
        tools=ToolRegistry((_tool("general_exec", ToolEffect.SENSITIVE, ran=ran),)),
    )
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    session = runtime.create_session(
        "agent.fast",
        workspace_dir=workspace,
        permission_mode=PermissionMode.WORKSPACE,
    )

    waiting = runtime.start_turn(session.session_id, "Do the task.")

    assert waiting.status is AgentStatus.WAITING_APPROVAL
    assert waiting.pending_approval is not None
    assert waiting.pending_approval.tool_name == "general_exec"
    assert ran == []
    assert platform.calls == 1
    assert "permission_mode=" not in str(platform.requests[0].messages[0].content)

    completed = runtime.resume_approval(
        session.session_id,
        waiting.pending_approval.call_id,
        approved=True,
    )

    assert completed.status is AgentStatus.COMPLETED
    assert completed.final_text == "done"
    assert ran == ["general_exec"]
    assert platform.calls == 2
    runtime.close()


def test_model_can_attempt_denied_tool_and_runtime_returns_denial_as_observation(tmp_path):
    ran: list[str] = []
    platform = OneToolPlatform("general_exec")
    store = FileAgentSessionStore(tmp_path / "state")
    runtime = AgentRuntime(
        platform=platform,
        store=store,
        tools=ToolRegistry((_tool("general_exec", ToolEffect.SENSITIVE, ran=ran),)),
    )
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    session = runtime.create_session(
        "agent.fast",
        workspace_dir=workspace,
        permission_mode=PermissionMode.READ_ONLY,
    )

    result = runtime.start_turn(session.session_id, "Try the task.")

    assert result.status is AgentStatus.COMPLETED
    assert result.final_text == "done"
    assert ran == []
    assert platform.calls == 2
    second_request = platform.requests[1]
    tool_messages = [message for message in second_request.messages if message.role is MessageRole.TOOL]
    assert tool_messages
    assert "blocked by permissions" in str(tool_messages[-1].content)
    runtime.close()