from __future__ import annotations

import pytest

from app.ai import AGENT_FAST_ROLE, ModelResponse, ToolCall
from app.agent_runtime.contracts import AgentStatus, PermissionMode, ToolEffect
from app.agent_runtime.execution_binding import action_binding_digest, binding_digest
from app.agent_runtime.runtime import AgentRuntime
from app.agent_runtime.storage import FileAgentSessionStore
from app.agent_runtime.tools import AgentTool, ToolRegistry, ToolResult


class ScriptedPlatform:
    def __init__(self, responses):
        self.responses = list(responses)

    def execute_chat(self, _profile_id, _request):
        if not self.responses:
            raise AssertionError("scripted platform ran out of responses")
        return self.responses.pop(0)


def _exec_tool(handler) -> AgentTool:
    return AgentTool(
        name="exec",
        description="Synthetic exec for Core action-binding tests.",
        input_schema={
            "type": "object",
            "properties": {
                "argv": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 1,
                },
                "cwd": {"type": "string"},
                "env": {
                    "type": "object",
                    "additionalProperties": {"type": "string"},
                },
                "pty": {"type": "boolean"},
            },
            "required": ["argv"],
            "additionalProperties": True,
        },
        handler=handler,
        effect=ToolEffect.SENSITIVE,
    )


def _call(*, argv=None, env_value: str = "one") -> ToolCall:
    return ToolCall(
        call_id="exec-1",
        name="exec",
        arguments={
            "argv": list(argv or ["synthetic-program", "--one"]),
            "cwd": ".",
            "env": {"NAME": env_value},
            "pty": False,
        },
    )


def _runtime(tmp_path, handler, *, call=None, final_text="done") -> AgentRuntime:
    resolved_call = call or _call()
    return AgentRuntime(
        platform=ScriptedPlatform(
            (
                ModelResponse(tool_calls=(resolved_call,)),
                ModelResponse(text=final_text),
            )
        ),
        store=FileAgentSessionStore(tmp_path / "state"),
        tools=ToolRegistry((_exec_tool(handler),)),
    )


def _session(runtime: AgentRuntime, tmp_path):
    return runtime.create_session(
        AGENT_FAST_ROLE.role_id,
        workspace_dir=tmp_path,
        permission_mode=PermissionMode.APPROVAL,
    )


def test_core_persists_call_specific_exec_binding_and_resumes_same_action(tmp_path):
    handled = []

    def handler(_context, arguments):
        handled.append(dict(arguments))
        return ToolResult(ok=True, content="ok")

    runtime = _runtime(tmp_path, handler)
    try:
        session = _session(runtime, tmp_path)
        waiting = runtime.start_turn(session.session_id, "Run it.")

        assert waiting.status is AgentStatus.WAITING_APPROVAL
        assert handled == []

        stored = runtime.store.load(session.session_id)
        call = stored.pending_tool_calls[0]
        step = runtime._build_step_context(
            stored,
            next_model_step=False,
            step_id=stored.pending_step_id,
        )
        tool = step.tool_router.get("exec")
        assert tool is not None
        expected = action_binding_digest(step, tool, call, runtime.platform)

        assert stored.pending_bindings[call.call_id] == expected
        assert expected != binding_digest(step, tool, runtime.platform)

        completed = runtime.resume_approval(
            session.session_id,
            call.call_id,
            approved=True,
        )

        assert completed.status is AgentStatus.COMPLETED
        assert completed.final_text == "done"
        assert len(handled) == 1
    finally:
        runtime.close()


def test_core_rejects_pending_exec_argument_drift_before_execution(tmp_path):
    handled = []

    def handler(_context, arguments):
        handled.append(dict(arguments))
        return ToolResult(ok=True, content="unexpected")

    runtime = _runtime(tmp_path, handler)
    try:
        session = _session(runtime, tmp_path)
        waiting = runtime.start_turn(session.session_id, "Run it.")
        assert waiting.status is AgentStatus.WAITING_APPROVAL

        stored = runtime.store.load(session.session_id)
        original = stored.pending_tool_calls[0]
        stored.pending_tool_calls[0] = ToolCall(
            call_id=original.call_id,
            name=original.name,
            arguments={
                **dict(original.arguments),
                "argv": ["synthetic-program", "--changed"],
            },
        )
        runtime.store.save(stored)

        with pytest.raises(ValueError, match="approval binding changed"):
            runtime.resume_approval(
                session.session_id,
                original.call_id,
                approved=True,
            )

        assert handled == []
    finally:
        runtime.close()


def test_malformed_exec_reaches_normal_tool_validation_instead_of_failing_turn(tmp_path):
    handled = []

    def handler(_context, arguments):
        handled.append(dict(arguments))
        return ToolResult(ok=True, content="unexpected")

    malformed = ToolCall(
        call_id="exec-1",
        name="exec",
        arguments={"argv": [], "cwd": ".", "pty": False},
    )
    runtime = _runtime(
        tmp_path,
        handler,
        call=malformed,
        final_text="corrected after invalid request",
    )
    try:
        session = _session(runtime, tmp_path)
        completed = runtime.start_turn(session.session_id, "Try the malformed action.")

        assert completed.status is AgentStatus.COMPLETED
        assert completed.pending_approval is None
        assert completed.final_text == "corrected after invalid request"
        assert handled == []
    finally:
        runtime.close()