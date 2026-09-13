from __future__ import annotations

from dataclasses import replace

import pytest

from app.agent_runtime import (
    AgentEventKind,
    AgentRuntime,
    AgentStatus,
    AgentTool,
    FileAgentSessionStore,
    PermissionMode,
    ToolEffect,
    ToolRegistry,
    ToolResult,
)
from app.ai import MessageRole, ModelResponse, ToolCall


class ScriptedPlatform:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def execute_chat(self, profile_id, request):
        self.requests.append((profile_id, request))
        if not self.responses:
            raise AssertionError("scripted platform ran out of responses")
        return self.responses.pop(0)


def _tool(name, *, effect, handler, binding_key=""):
    return AgentTool(
        name=name,
        description=f"{name} parity tool",
        input_schema={
            "type": "object",
            "properties": {
                "value": {"type": "string"},
            },
            "additionalProperties": False,
        },
        handler=handler,
        effect=effect,
        binding_key=binding_key,
    )


def test_multiple_tool_calls_pause_at_approval_then_resume_with_observations(tmp_path):
    """Codex approvals.rs parity: completed calls stay observed while a later call waits."""
    executed = []

    def inspect(_context, arguments):
        executed.append(("inspect", arguments["value"]))
        return ToolResult(ok=True, content=f"observed:{arguments['value']}")

    def change(_context, arguments):
        executed.append(("change", arguments["value"]))
        return ToolResult(ok=True, content=f"changed:{arguments['value']}")

    platform = ScriptedPlatform(
        [
            ModelResponse(
                tool_calls=(
                    ToolCall("read-call", "inspect", {"value": "before"}),
                    ToolCall("write-call", "change", {"value": "after"}),
                ),
                finish_reason="tool_calls",
            ),
            ModelResponse(text="done after observations", finish_reason="stop"),
        ]
    )
    runtime = AgentRuntime(
        platform=platform,
        store=FileAgentSessionStore(tmp_path / "state"),
        tools=ToolRegistry(
            (
                _tool("inspect", effect=ToolEffect.READ_ONLY, handler=inspect),
                _tool("change", effect=ToolEffect.MUTATING, handler=change),
            )
        ),
    )
    session = runtime.create_session(
        "agent.fast",
        permission_mode=PermissionMode.APPROVAL,
    )

    waiting = runtime.start_turn(session.session_id, "inspect, then change")

    assert waiting.status is AgentStatus.WAITING_APPROVAL
    assert waiting.pending_approval is not None
    assert waiting.pending_approval.call_id == "write-call"
    assert executed == [("inspect", "before")]

    events = runtime.store.events(session.session_id)
    completed_index = next(
        index
        for index, event in enumerate(events)
        if event.kind is AgentEventKind.TOOL_COMPLETED
        and event.data.get("call_id") == "read-call"
    )
    approval_index = next(
        index
        for index, event in enumerate(events)
        if event.kind is AgentEventKind.TOOL_APPROVAL_REQUIRED
        and event.data.get("call_id") == "write-call"
    )
    assert completed_index < approval_index

    result = runtime.resume_approval(
        session.session_id,
        "write-call",
        approved=True,
    )

    assert result.status is AgentStatus.COMPLETED
    assert result.final_text == "done after observations"
    assert executed == [("inspect", "before"), ("change", "after")]

    follow_up = platform.requests[-1][1]
    tool_messages = [
        message
        for message in follow_up.messages
        if message.role is MessageRole.TOOL
    ]
    assert [message.tool_call_id for message in tool_messages[-2:]] == [
        "read-call",
        "write-call",
    ]
    assert "observed:before" in str(tool_messages[-2].content)
    assert "changed:after" in str(tool_messages[-1].content)
    runtime.close()


def test_waiting_approval_fails_closed_on_permission_or_binding_drift(tmp_path):
    """Codex captured-authority parity: an approval cannot silently widen or reroute."""
    executed = []

    def change(_context, arguments):
        executed.append(arguments["value"])
        return ToolResult(ok=True, content="changed")

    original = _tool(
        "change",
        effect=ToolEffect.MUTATING,
        handler=change,
        binding_key="endpoint-v1",
    )
    platform = ScriptedPlatform(
        [
            ModelResponse(
                tool_calls=(ToolCall("call", "change", {"value": "original"}),),
                finish_reason="tool_calls",
            ),
            ModelResponse(text="done", finish_reason="stop"),
        ]
    )
    runtime = AgentRuntime(
        platform=platform,
        store=FileAgentSessionStore(tmp_path / "state"),
        tools=ToolRegistry((original,)),
    )
    session = runtime.create_session(
        "agent.fast",
        permission_mode=PermissionMode.APPROVAL,
    )

    waiting = runtime.start_turn(session.session_id, "change it")
    assert waiting.status is AgentStatus.WAITING_APPROVAL

    with pytest.raises(RuntimeError, match="cannot change permissions"):
        runtime.set_permission_mode(session.session_id, PermissionMode.FULL_ACCESS)

    runtime.tools = ToolRegistry(
        (replace(original, binding_key="endpoint-v2"),)
    )
    with pytest.raises(ValueError, match="binding changed"):
        runtime.resume_approval(session.session_id, "call", approved=True)

    still_waiting = runtime.get_session(session.session_id)
    assert still_waiting.status is AgentStatus.WAITING_APPROVAL
    assert still_waiting.pending_approval is not None
    assert executed == []

    runtime.tools = ToolRegistry((original,))
    result = runtime.resume_approval(session.session_id, "call", approved=True)
    assert result.status is AgentStatus.COMPLETED
    assert executed == ["original"]
    runtime.close()


def test_cancel_waiting_approval_invalidates_late_resolution(tmp_path):
    """Codex approval abort parity: cancellation wins and late approval cannot execute."""
    executed = []

    def change(_context, arguments):
        executed.append(arguments["value"])
        return ToolResult(ok=True, content="changed")

    runtime = AgentRuntime(
        platform=ScriptedPlatform(
            [
                ModelResponse(
                    tool_calls=(ToolCall("call", "change", {"value": "must-not-run"}),),
                    finish_reason="tool_calls",
                )
            ]
        ),
        store=FileAgentSessionStore(tmp_path / "state"),
        tools=ToolRegistry(
            (_tool("change", effect=ToolEffect.MUTATING, handler=change),)
        ),
    )
    session = runtime.create_session(
        "agent.fast",
        permission_mode=PermissionMode.APPROVAL,
    )
    assert runtime.start_turn(session.session_id, "change it").status is AgentStatus.WAITING_APPROVAL

    cancelled = runtime.cancel(session.session_id)

    assert cancelled.status is AgentStatus.CANCELLED
    stored = runtime.get_session(session.session_id)
    assert stored.pending_approval is None
    assert stored.pending_tool_calls == []
    assert executed == []

    with pytest.raises(RuntimeError, match="not waiting for approval"):
        runtime.resume_approval(session.session_id, "call", approved=True)
    assert executed == []
    runtime.close()


def test_reused_call_id_executes_each_sample_once_without_deduplicating(tmp_path):
    """Minimum compatibility for Codex's reused-call-id regression coverage."""
    executed = []

    def echo(_context, arguments):
        executed.append(arguments["value"])
        return ToolResult(ok=True, content=arguments["value"])

    platform = ScriptedPlatform(
        [
            ModelResponse(
                tool_calls=(ToolCall("reused", "echo", {"value": "first"}),),
                finish_reason="tool_calls",
            ),
            ModelResponse(
                tool_calls=(ToolCall("reused", "echo", {"value": "second"}),),
                finish_reason="tool_calls",
            ),
            ModelResponse(text="done", finish_reason="stop"),
        ]
    )
    runtime = AgentRuntime(
        platform=platform,
        store=FileAgentSessionStore(tmp_path / "state"),
        tools=ToolRegistry(
            (_tool("echo", effect=ToolEffect.READ_ONLY, handler=echo),)
        ),
    )
    session = runtime.create_session(
        "agent.fast",
        permission_mode=PermissionMode.APPROVAL,
    )

    result = runtime.start_turn(session.session_id, "echo twice")

    assert result.status is AgentStatus.COMPLETED
    assert executed == ["first", "second"]
    stored = runtime.get_session(session.session_id)
    outputs = [
        message
        for message in stored.messages
        if message.role is MessageRole.TOOL and message.tool_call_id == "reused"
    ]
    assert len(outputs) == 2
    assert '"content":"first"' in str(outputs[0].content)
    assert '"content":"second"' in str(outputs[1].content)
    runtime.close()
