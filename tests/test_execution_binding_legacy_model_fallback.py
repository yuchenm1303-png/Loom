from __future__ import annotations

from app.agent_runtime.contracts import PermissionMode, ToolEffect
from app.agent_runtime.execution_binding import binding_digest
from app.agent_runtime.step import StepContext
from app.agent_runtime.tools import AgentTool, ToolResult, ToolRouter


class _Profile:
    def __init__(self, model: str) -> None:
        self.model = model

    def as_safe_dict(self):
        return {"profile_id": "agent.fast", "model": self.model}


class _Registry:
    def __init__(self, model: str) -> None:
        self.profile = _Profile(model)

    def get(self, _profile_id: str):
        return self.profile


class _Platform:
    def __init__(self, model: str) -> None:
        self.registry = _Registry(model)


def test_uncaptured_step_keeps_live_registry_model_identity_fallback(tmp_path):
    tool = AgentTool(
        name="change",
        description="test",
        input_schema={"type": "object", "properties": {}, "additionalProperties": False},
        handler=lambda _context, _arguments: ToolResult(ok=True, content="ok"),
        effect=ToolEffect.MUTATING,
    )
    step = StepContext.build(
        step_id="step-1",
        session_id="session-1",
        turn_id="turn-1",
        model_step=1,
        workspace_dir=str(tmp_path),
        profile_id="agent.fast",
        permission_mode=PermissionMode.APPROVAL,
        tool_router=ToolRouter((tool,)),
    )

    assert step.request_state.captured is False
    assert binding_digest(step, tool, _Platform("model-a")) != binding_digest(
        step,
        tool,
        _Platform("model-b"),
    )
