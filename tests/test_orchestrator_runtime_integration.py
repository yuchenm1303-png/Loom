from __future__ import annotations

from app.agent_runtime import AgentRuntime, AgentStatus, FileAgentSessionStore, PermissionMode
from app.agent_runtime.contracts import ToolEffect
from app.agent_runtime.orchestrator import ToolOrchestrator
from app.agent_runtime.tools import AgentTool, ToolRegistry, ToolResult
from app.ai import AGENT_FAST_ROLE, ModelResponse, ToolCall


class ScriptedPlatform:
    def __init__(self, responses):
        self.responses = list(responses)

    def execute_chat(self, _profile_id, _request):
        if not self.responses:
            raise AssertionError("scripted platform ran out of responses")
        return self.responses.pop(0)


class TrackingOrchestrator(ToolOrchestrator):
    def __init__(self) -> None:
        super().__init__()
        self.executions: list[tuple[str, bool]] = []

    def execute(self, prepared, context, *, approval_granted: bool = False):
        self.executions.append((prepared.call.call_id, approval_granted))
        return super().execute(
            prepared,
            context,
            approval_granted=approval_granted,
        )


def _tool(effect: ToolEffect, calls: list[str]) -> AgentTool:
    def handler(_context, _arguments):
        calls.append("handler")
        return ToolResult(ok=True, content="ok")

    return AgentTool(
        name="probe",
        description="Probe the orchestrator execution boundary.",
        input_schema={"type": "object", "properties": {}, "additionalProperties": False},
        handler=handler,
        effect=effect,
    )


def _runtime(tmp_path, *, effect: ToolEffect):
    calls: list[str] = []
    platform = ScriptedPlatform(
        [
            ModelResponse(
                tool_calls=(
                    ToolCall(call_id="probe-1", name="probe", arguments={}),
                )
            ),
            ModelResponse(text="done"),
        ]
    )
    runtime = AgentRuntime(
        platform=platform,
        store=FileAgentSessionStore(tmp_path / "state"),
        tools=ToolRegistry((_tool(effect, calls),)),
    )
    tracker = TrackingOrchestrator()
    runtime.orchestrator = tracker
    return runtime, tracker, calls


def test_default_runtime_routes_allowed_tool_through_orchestrator_execute(tmp_path):
    workspace = tmp_path / "project"
    workspace.mkdir()
    runtime, tracker, calls = _runtime(tmp_path, effect=ToolEffect.READ_ONLY)
    session = runtime.create_session(
        AGENT_FAST_ROLE.role_id,
        workspace_dir=workspace,
        permission_mode=PermissionMode.READ_ONLY,
    )

    result = runtime.start_turn(session.session_id, "Run probe.")

    assert result.status is AgentStatus.COMPLETED
    assert tracker.executions == [("probe-1", False)]
    assert calls == ["handler"]


def test_approval_resume_carries_explicit_grant_into_execution_boundary(tmp_path):
    workspace = tmp_path / "project"
    workspace.mkdir()
    runtime, tracker, calls = _runtime(tmp_path, effect=ToolEffect.MUTATING)
    session = runtime.create_session(
        AGENT_FAST_ROLE.role_id,
        workspace_dir=workspace,
        permission_mode=PermissionMode.APPROVAL,
    )

    waiting = runtime.start_turn(session.session_id, "Run probe.")
    assert waiting.status is AgentStatus.WAITING_APPROVAL
    assert tracker.executions == []
    assert calls == []

    result = runtime.resume_approval(
        session.session_id,
        "probe-1",
        approved=True,
    )

    assert result.status is AgentStatus.COMPLETED
    assert tracker.executions == [("probe-1", True)]
    assert calls == ["handler"]
