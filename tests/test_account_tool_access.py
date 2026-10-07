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
    assert result.data["authorization"]["status"] == "credential_missing"
    assert "could not be verified" in result.content
    assert ran == []
    set_account_tool_access_credential(None)


def test_authorization_cache_and_failure_diagnostics(monkeypatch):
    import json
    from app.agent_runtime import account_tool_access as gate

    monkeypatch.setenv("LOOM_ACCOUNT_TOOL_ACCESS_ENFORCED", "1")
    monkeypatch.setenv("LOOM_ACCOUNT_API_BASE_URL", "https://example.test/v1")
    set_account_tool_access_credential("test-token-cache")
    calls = []

    class Response:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def read(self, size): return json.dumps({"access": {"browserUse": True, "computerUse": False}}).encode()

    def fetch(*args, **kwargs):
        calls.append(1)
        return Response()

    monkeypatch.setattr(gate, "urlopen", fetch)
    try:
        for _ in range(50):
            assert gate.account_capability_allowed("browserUse")
        assert len(calls) == 1
        assert gate.account_tool_access_status()["status"] == "confirmed"
        import concurrent.futures
        monkeypatch.setattr(gate, "_CACHE_EXPIRES_AT", 0)
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            assert all(pool.map(lambda _: gate.account_capability_allowed("browserUse"), range(16)))
        assert len(calls) == 2
        monkeypatch.setattr(Response, "read", lambda self, size: b'{"access":{"browserUse":false,"computerUse":false}}')
        monkeypatch.setattr(gate, "_CACHE_EXPIRES_AT", 0)
        assert not gate.account_capability_allowed("browserUse")
        assert gate.account_tool_access_status()["status"] == "confirmed"
        monkeypatch.setattr(gate, "_CACHE_EXPIRES_AT", 0)
        monkeypatch.setattr(gate, "urlopen", lambda *a, **k: (_ for _ in ()).throw(TimeoutError()))
        assert not gate.account_capability_allowed("browserUse")
        assert gate.account_tool_access_status()["status"] == "service_unavailable"
        set_account_tool_access_credential("")
        assert not gate.account_capability_allowed("browserUse")
    finally:
        set_account_tool_access_credential(None)


def test_old_account_response_cannot_restore_access(monkeypatch):
    import json
    from app.agent_runtime import account_tool_access as gate
    monkeypatch.setenv("LOOM_ACCOUNT_TOOL_ACCESS_ENFORCED", "1")
    monkeypatch.setenv("LOOM_ACCOUNT_API_BASE_URL", "https://example.test/v1")
    set_account_tool_access_credential("old-account")
    class Response:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def read(self, size):
            set_account_tool_access_credential("")
            return json.dumps({"access": {"browserUse": True, "computerUse": True}}).encode()
    monkeypatch.setattr(gate, "urlopen", lambda *a, **k: Response())
    try:
        assert not gate.account_capability_allowed("browserUse")
        assert gate.account_tool_access_status()["status"] == "credential_missing"
    finally:
        set_account_tool_access_credential(None)
