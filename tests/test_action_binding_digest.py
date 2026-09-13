from __future__ import annotations

from dataclasses import replace

import pytest

from app.ai import ToolCall
from app.agent_runtime.contracts import PermissionMode, ToolEffect
from app.agent_runtime.execution_binding import action_binding_digest, binding_digest
from app.agent_runtime.shell_environment import ShellEnvironmentPolicy
from app.agent_runtime.step import StepContext
from app.agent_runtime.tools import AgentTool, ToolResult, ToolRouter


class BarePlatform:
    pass


def _tool(name: str) -> AgentTool:
    return AgentTool(
        name=name,
        description=f"Synthetic {name} tool.",
        input_schema={"type": "object", "properties": {}, "additionalProperties": True},
        handler=lambda _context, _arguments: ToolResult(ok=True, content="ok"),
        effect=ToolEffect.SENSITIVE,
    )


def _step(tmp_path, tool: AgentTool) -> StepContext:
    step = StepContext.build(
        step_id="step-1",
        session_id="session-1",
        turn_id="turn-1",
        model_step=1,
        workspace_dir=str(tmp_path),
        profile_id="agent.fast",
        permission_mode=PermissionMode.WORKSPACE,
        tool_router=ToolRouter((tool,)),
    )
    return replace(step, environment_policy=ShellEnvironmentPolicy(inherit="none"))


def _exec_call(call_id: str, *, flag: str = "one", env_value: str = "value") -> ToolCall:
    return ToolCall(
        call_id=call_id,
        name="exec",
        arguments={
            "argv": ["synthetic-program", f"--{flag}"],
            "cwd": ".",
            "env": {"NAME": env_value},
            "pty": False,
        },
    )


def test_exec_action_binding_changes_when_command_semantics_change(tmp_path):
    tool = _tool("exec")
    step = _step(tmp_path, tool)
    platform = BarePlatform()

    generic = binding_digest(step, tool, platform)
    first = action_binding_digest(step, tool, _exec_call("call-1", flag="one"), platform)
    second = action_binding_digest(step, tool, _exec_call("call-2", flag="two"), platform)

    assert first != generic
    assert second != generic
    assert first != second


def test_exec_action_binding_changes_for_explicit_environment_value(tmp_path):
    tool = _tool("exec")
    step = _step(tmp_path, tool)
    platform = BarePlatform()

    first = action_binding_digest(step, tool, _exec_call("call-1", env_value="one"), platform)
    second = action_binding_digest(step, tool, _exec_call("call-2", env_value="two"), platform)

    assert first != second


def test_exec_action_binding_ignores_call_id_for_same_semantics(tmp_path):
    tool = _tool("exec")
    step = _step(tmp_path, tool)
    platform = BarePlatform()

    first = action_binding_digest(step, tool, _exec_call("call-1"), platform)
    second = action_binding_digest(step, tool, _exec_call("call-2"), platform)

    assert first == second


def test_malformed_exec_keeps_normal_validation_but_still_has_call_specific_binding(tmp_path):
    tool = _tool("exec")
    step = _step(tmp_path, tool)
    platform = BarePlatform()
    first = ToolCall(
        call_id="exec-invalid-1",
        name="exec",
        arguments={"argv": [], "cwd": "."},
    )
    second = ToolCall(
        call_id="exec-invalid-2",
        name="exec",
        arguments={"argv": [], "cwd": "../outside"},
    )

    generic = binding_digest(step, tool, platform)
    first_binding = action_binding_digest(step, tool, first, platform)
    same_binding = action_binding_digest(
        step,
        tool,
        ToolCall(call_id="other-id", name="exec", arguments=dict(first.arguments)),
        platform,
    )
    second_binding = action_binding_digest(step, tool, second, platform)

    assert first_binding != generic
    assert first_binding == same_binding
    assert second_binding != first_binding


def test_untyped_tool_keeps_legacy_binding(tmp_path):
    tool = _tool("change")
    step = _step(tmp_path, tool)
    platform = BarePlatform()
    call = ToolCall(call_id="change-1", name="change", arguments={"value": "x"})

    assert action_binding_digest(step, tool, call, platform) == binding_digest(
        step,
        tool,
        platform,
    )


def test_action_binding_rejects_call_tool_mismatch(tmp_path):
    tool = _tool("exec")
    step = _step(tmp_path, tool)

    with pytest.raises(ValueError, match="does not match"):
        action_binding_digest(
            step,
            tool,
            ToolCall(call_id="other-1", name="other", arguments={}),
            BarePlatform(),
        )
