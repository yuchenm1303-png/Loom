from __future__ import annotations

from pathlib import Path

import pytest

from app.ai import ToolCall
from app.agent_runtime.contracts import ApprovalKind, PendingToolApproval, PermissionMode, ToolEffect
from app.agent_runtime.orchestrator import ToolOrchestrator
from app.agent_runtime.step import StepContext
from app.agent_runtime.storage import _approval_from_dict, _approval_to_dict
from app.agent_runtime.tools import AgentTool, ToolContext, ToolResult, ToolRouter


def _tool(name: str, effect: ToolEffect, handler) -> AgentTool:
    return AgentTool(
        name=name,
        description=f"{name} test tool",
        input_schema={"type": "object", "properties": {}, "additionalProperties": False},
        handler=handler,
        effect=effect,
    )


def _step(tmp_path: Path, mode: PermissionMode, tool: AgentTool) -> StepContext:
    return StepContext.build(
        step_id="step-1",
        session_id="session-1",
        turn_id="turn-1",
        model_step=1,
        workspace_dir=str(tmp_path),
        profile_id="agent.fast",
        permission_mode=mode,
        tool_router=ToolRouter((tool,)),
    )


def _context(tmp_path: Path) -> ToolContext:
    return ToolContext(
        session_id="session-1",
        turn_id="turn-1",
        workspace=tmp_path,
    )


def test_orchestrator_executes_allowed_handler_once(tmp_path):
    calls: list[str] = []

    def handler(_context, _arguments):
        calls.append("called")
        return ToolResult(ok=True, content="ok")

    tool = _tool("inspect", ToolEffect.READ_ONLY, handler)
    prepared = ToolOrchestrator().prepare(
        _step(tmp_path, PermissionMode.READ_ONLY, tool),
        ToolCall(call_id="call-1", name="inspect", arguments={}),
    )

    result = ToolOrchestrator().execute(prepared, _context(tmp_path))

    assert result.ok is True
    assert calls == ["called"]


def test_orchestrator_requires_explicit_grant_for_approval_call(tmp_path):
    calls: list[str] = []

    def handler(_context, _arguments):
        calls.append("called")
        return ToolResult(ok=True, content="changed")

    orchestrator = ToolOrchestrator()
    tool = _tool("change", ToolEffect.MUTATING, handler)
    prepared = orchestrator.prepare(
        _step(tmp_path, PermissionMode.APPROVAL, tool),
        ToolCall(call_id="call-2", name="change", arguments={}),
    )

    with pytest.raises(RuntimeError, match="requires approval"):
        orchestrator.execute(prepared, _context(tmp_path))
    assert calls == []

    result = orchestrator.execute(
        prepared,
        _context(tmp_path),
        approval_granted=True,
    )
    assert result.ok is True
    assert calls == ["called"]


def test_orchestrator_never_executes_denied_call(tmp_path):
    calls: list[str] = []

    def handler(_context, _arguments):
        calls.append("called")
        return ToolResult(ok=True, content="changed")

    orchestrator = ToolOrchestrator()
    tool = _tool("change", ToolEffect.MUTATING, handler)
    prepared = orchestrator.prepare(
        _step(tmp_path, PermissionMode.READ_ONLY, tool),
        ToolCall(call_id="call-3", name="change", arguments={}),
    )

    with pytest.raises(RuntimeError, match="denied"):
        orchestrator.execute(
            prepared,
            _context(tmp_path),
            approval_granted=True,
        )
    assert calls == []


def test_orchestrator_converts_handler_failure_to_tool_result(tmp_path):
    def handler(_context, _arguments):
        raise ValueError("boom")

    orchestrator = ToolOrchestrator()
    tool = _tool("inspect", ToolEffect.READ_ONLY, handler)
    prepared = orchestrator.prepare(
        _step(tmp_path, PermissionMode.READ_ONLY, tool),
        ToolCall(call_id="call-4", name="inspect", arguments={}),
    )

    result = orchestrator.execute(prepared, _context(tmp_path))

    assert result.ok is False
    assert "ValueError: boom" in result.content


def test_pending_approval_stage_round_trips_and_legacy_defaults_to_initial():
    pending = PendingToolApproval(
        call_id="call-5",
        tool_name="exec",
        arguments={"argv": ["python", "-V"]},
        effect=ToolEffect.SENSITIVE,
        reason="retry requires an explicit decision",
        kind=ApprovalKind.SANDBOX_ESCALATION,
        retry_reason="initial contained attempt was rejected",
    )

    payload = _approval_to_dict(pending)
    assert _approval_from_dict(payload) == pending

    legacy = dict(payload or {})
    legacy.pop("kind", None)
    legacy.pop("retry_reason", None)
    restored = _approval_from_dict(legacy)
    assert restored is not None
    assert restored.kind is ApprovalKind.INITIAL
    assert restored.retry_reason == ""
