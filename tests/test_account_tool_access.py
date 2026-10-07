from __future__ import annotations

from app.ai import ToolCall
from app.agent_runtime.account_tool_access import set_account_tool_access_credential
from app.agent_runtime.contracts import ToolEffect
from app.agent_runtime.orchestrator import PreparedToolCall, ToolOrchestrator
from app.agent_runtime.permissions import PermissionDecision
from app.agent_runtime.tools import AgentTool, ToolContext, ToolRegistry, ToolResult


def _tool(name: str, ran: list[str]) -> AgentTool:
    def handler(_context, _arguments):
        ran.append(name)
        return ToolResult(ok=True, content="ran")

    return AgentTool(
        name=name,
        description=f"test tool {name}",
        input_schema={"type": "object", "properties": {}, "additionalProperties": False},
        handler=handler,
        effect=ToolEffect.READ_ONLY,
    )


def test_account_gate_hides_computer_and_browser_tools_fail_closed(monkeypatch):
    monkeypatch.setenv("LOOM_ACCOUNT_TOOL_ACCESS_ENFORCED", "1")
    monkeypatch.delenv("LOOM_ACCOUNT_API_BASE_URL", raising=False)
    monkeypatch.delenv("LOOM_ACCOUNT_MODEL_CREDENTIAL", raising=False)
    set_account_tool_access_credential(None)
    ran: list[str] = []
    registry = ToolRegistry(
        (
            _tool("computer_screenshot", ran),
            _tool("browser_snapshot", ran),
            _tool("ordinary_probe", ran),
        )
    )

    assert {tool.name for tool in registry.router().all()} == {"ordinary_probe"}
    assert ran == []


def test_execution_rechecks_account_gate_before_sensitive_automation(monkeypatch, tmp_path):
    monkeypatch.setenv("LOOM_ACCOUNT_TOOL_ACCESS_ENFORCED", "1")
    monkeypatch.delenv("LOOM_ACCOUNT_API_BASE_URL", raising=False)
    monkeypatch.delenv("LOOM_ACCOUNT_MODEL_CREDENTIAL", raising=False)
    set_account_tool_access_credential("")
    ran: list[str] = []
    tool = _tool("computer_click", ran)
    prepared = PreparedToolCall(
        call=ToolCall(call_id="call-1", name=tool.name, arguments={}),
        tool=tool,
        decision=PermissionDecision.ALLOW,
        reason="test",
    )

    result = ToolOrchestrator().execute(
        prepared,
        ToolContext(
            session_id="session-1",
            turn_id="turn-1",
            workspace=tmp_path,
        ),
    )

    assert result.ok is False
    assert result.data["failure_kind"] == "account_entitlement"
    assert result.data["execution_status"] == "not_executed"
    assert result.data["capability"] == "computerUse"
    assert "not enabled for this Loom account" in result.content
    assert ran == []
    set_account_tool_access_credential(None)
