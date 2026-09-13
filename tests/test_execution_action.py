from __future__ import annotations

import json
from dataclasses import replace

import pytest

from app.ai import ToolCall
from app.agent_runtime.contracts import PermissionMode, ToolEffect
from app.agent_runtime.execution_action import (
    ExecActionIdentity,
    exec_environment_identity,
    execution_action_for,
)
from app.agent_runtime.shell_environment import ShellEnvironmentPolicy
from app.agent_runtime.step import StepContext
from app.agent_runtime.tools import AgentTool, ToolResult, ToolRouter


def _tool() -> AgentTool:
    return AgentTool(
        name="exec",
        description="Synthetic exec definition for action identity tests.",
        input_schema={"type": "object", "properties": {}, "additionalProperties": True},
        handler=lambda _context, _arguments: ToolResult(ok=True, content="ok"),
        effect=ToolEffect.SENSITIVE,
    )


def _step(tmp_path, *, policy: ShellEnvironmentPolicy | None = None) -> StepContext:
    tool = _tool()
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
    return replace(
        step,
        environment_policy=policy or ShellEnvironmentPolicy(inherit="none"),
    )


def _call(*, call_id: str = "exec-1", **overrides) -> ToolCall:
    arguments = {
        "argv": ["synthetic-program", "--flag"],
        "cwd": ".",
        "stdin": "private-input-value",
        "env": {"VISIBLE_NAME": "private-env-value"},
        "timeout_seconds": 45,
        "wait": False,
        "pty": True,
        "rows": 31,
        "cols": 101,
    }
    arguments.update(overrides)
    return ToolCall(call_id=call_id, name="exec", arguments=arguments)


def test_execution_action_factory_is_typed_and_extensible(tmp_path):
    step = _step(tmp_path)
    action = execution_action_for(step, _call())

    assert isinstance(action, ExecActionIdentity)
    assert execution_action_for(
        step,
        ToolCall(call_id="other-1", name="other", arguments={}),
    ) is None


def test_exec_action_captures_execution_shape_without_copying_private_values(tmp_path):
    action = ExecActionIdentity.build(_step(tmp_path), _call())
    payload = action.binding_payload()
    rendered = json.dumps(payload, sort_keys=True)

    assert action.argv == ("synthetic-program", "--flag")
    assert action.cwd == "."
    assert action.resolved_cwd == str(tmp_path.resolve())
    assert action.wait is False
    assert action.timeout_seconds == 45
    assert action.pty is True
    assert (action.rows, action.cols) == (31, 101)
    assert action.explicit_environment_names == ("VISIBLE_NAME",)
    assert "call_id" not in payload
    assert "cwd" not in payload
    assert action.instance_payload()["call_id"] == "exec-1"
    assert action.instance_payload()["cwd"] == "."
    assert "private-input-value" not in rendered
    assert "private-env-value" not in rendered
    assert len(action.stdin_identity) == 64
    assert len(action.explicit_environment_identity) == 64
    assert len(action.resolved_environment_identity) == 64


def test_exec_action_semantic_identity_does_not_depend_on_call_id(tmp_path):
    step = _step(tmp_path)
    first = ExecActionIdentity.build(step, _call(call_id="exec-1"))
    second = ExecActionIdentity.build(step, _call(call_id="exec-2"))

    assert first.call_id != second.call_id
    assert first.instance_payload() != second.instance_payload()
    assert first.binding_payload() == second.binding_payload()
    assert first.digest() == second.digest()


def test_exec_action_semantic_identity_uses_resolved_cwd(tmp_path):
    step = _step(tmp_path)
    dot = ExecActionIdentity.build(step, _call(cwd="."))
    dotted = ExecActionIdentity.build(step, _call(cwd="./"))

    assert dot.cwd != dotted.cwd
    assert dot.resolved_cwd == dotted.resolved_cwd
    assert dot.binding_payload() == dotted.binding_payload()
    assert dot.digest() == dotted.digest()


def test_pipe_action_ignores_terminal_dimensions_but_still_validates_them(tmp_path):
    step = _step(tmp_path)
    first = ExecActionIdentity.build(step, _call(pty=False, rows=24, cols=80))
    second = ExecActionIdentity.build(step, _call(pty=False, rows=70, cols=160))

    assert first.rows is None and first.cols is None
    assert second.rows is None and second.cols is None
    assert first.digest() == second.digest()

    with pytest.raises(ValueError, match="rows"):
        ExecActionIdentity.build(step, _call(pty=False, rows=0))


def test_exec_action_identity_changes_for_explicit_environment_value(tmp_path):
    step = _step(tmp_path)
    first = ExecActionIdentity.build(step, _call(env={"NAME": "first"}))
    second = ExecActionIdentity.build(step, _call(env={"NAME": "second"}))

    assert first.explicit_environment_names == second.explicit_environment_names == ("NAME",)
    assert first.explicit_environment_identity != second.explicit_environment_identity
    assert first.resolved_environment_identity != second.resolved_environment_identity
    assert first.digest() != second.digest()


def test_exec_action_identity_changes_with_ambient_environment_policy(tmp_path):
    first_step = _step(
        tmp_path,
        policy=ShellEnvironmentPolicy(inherit="none", set_vars=(("BASE", "one"),)),
    )
    second_step = _step(
        tmp_path,
        policy=ShellEnvironmentPolicy(inherit="none", set_vars=(("BASE", "two"),)),
    )
    call = _call(env={})

    first = ExecActionIdentity.build(first_step, call)
    second = ExecActionIdentity.build(second_step, call)

    assert first.resolved_environment_identity == exec_environment_identity(first_step, {})
    assert second.resolved_environment_identity == exec_environment_identity(second_step, {})
    assert first.resolved_environment_identity != second.resolved_environment_identity
    assert first.digest() != second.digest()


def test_exec_action_identity_changes_for_command_semantics(tmp_path):
    step = _step(tmp_path)
    base = ExecActionIdentity.build(step, _call())
    changed_argv = ExecActionIdentity.build(
        step,
        _call(argv=["synthetic-program", "--different"]),
    )
    changed_tty = ExecActionIdentity.build(step, _call(pty=False))

    assert base.digest() != changed_argv.digest()
    assert base.digest() != changed_tty.digest()


@pytest.mark.parametrize(
    ("overrides", "message"),
    (
        ({"argv": ["synthetic-program", 7]}, "array of strings"),
        ({"cwd": 7}, "cwd must be a string"),
        ({"stdin": 7}, "stdin must be a string"),
        ({"env": {"NAME": 7}}, "env must be an object of string values"),
        ({"timeout_seconds": "45"}, "timeout_seconds must be an integer"),
        ({"pty": "false"}, "pty must be a boolean"),
        ({"rows": "24"}, "rows must be an integer"),
        ({"wait": 1}, "wait must be a boolean"),
        ({"unexpected": "value"}, "unsupported arguments"),
    ),
)
def test_exec_action_rejects_schema_invalid_shapes_before_canonicalization(
    tmp_path,
    overrides,
    message,
):
    with pytest.raises(ValueError, match=message):
        ExecActionIdentity.build(_step(tmp_path), _call(**overrides))


def test_exec_action_rejects_workspace_escape_and_wrong_tool(tmp_path):
    step = _step(tmp_path)

    with pytest.raises(ValueError, match="escapes"):
        ExecActionIdentity.build(step, _call(cwd="../outside"))

    with pytest.raises(ValueError, match="requires an exec"):
        ExecActionIdentity.build(
            step,
            ToolCall(call_id="other-1", name="other", arguments={"argv": ["x"]}),
        )
