from __future__ import annotations

import json
import threading
from types import SimpleNamespace

import pytest

from app.agent_runtime import AgentLimits, AgentStatus, AgentTool, ToolExposure, ToolEffect, ToolResult
from app.agent_runtime.runtime import CancellationToken
from app.agent_runtime.browser_session import BrowserOperationError
from app.agent_runtime.context_limits import resolve_context_limits
from app.agent_runtime.context_reducer import reduce_tool_outputs
from app.ai import AIMessage, MessageRole, ModelResponse, ToolCall
from app.ai.errors import AITransportError
from test_alignment_reliability import make_runtime, Scripted


def test_deferred_builtin_executes_once_without_discovery_roundtrip(tmp_path):
    executed = []
    tool = AgentTool("deferred_probe", "probe", {"type": "object"},
                     lambda c, a: executed.append(c.services["execution_step"].permissions) or ToolResult(True, "ready"),
                     exposure=ToolExposure.DEFERRED)
    platform = Scripted([ModelResponse(tool_calls=(ToolCall("probe", tool.name, {}),)), ModelResponse(text="done")])
    rt = make_runtime(tmp_path, platform, [tool])
    session = rt.create_session("agent.fast", workspace_dir=tmp_path)
    try:
        assert rt.start_turn(session.session_id, "inspect").status is AgentStatus.COMPLETED
        assert len(executed) == 1 and len(platform.requests) == 2
        assert not any(e.kind.value == "tool_failed" for e in rt.store.events(session.session_id))
    finally:
        rt.close()


def test_deferred_sensitive_tool_still_waits_for_original_permission_approval(tmp_path):
    calls = []
    tool = AgentTool("deferred_write", "write", {"type": "object"},
                     lambda c, a: calls.append(1) or ToolResult(True, "done"),
                     exposure=ToolExposure.DEFERRED, effect=ToolEffect.SENSITIVE)
    platform = Scripted([ModelResponse(tool_calls=(ToolCall("write", tool.name, {}),)), ModelResponse(text="done")])
    rt = make_runtime(tmp_path, platform, [tool])
    session = rt.create_session("agent.fast", workspace_dir=tmp_path)
    try:
        result = rt.start_turn(session.session_id, "write")
        assert result.status is AgentStatus.WAITING_APPROVAL and not calls
        assert rt.resume_approval(session.session_id, "write", approved=True).status is AgentStatus.COMPLETED
        assert calls == [1]
    finally:
        rt.close()


def test_invalid_predecessor_holds_only_dependent_writes(tmp_path):
    calls = []
    def tool(name, effect):
        return AgentTool(name, name, {"type": "object", "required": ["value"]},
                         lambda c, a: calls.append(name) or ToolResult(True, "done"), effect=effect)
    tools = [tool("read", ToolEffect.READ_ONLY), tool("write", ToolEffect.SENSITIVE)]
    platform = Scripted([ModelResponse(tool_calls=(ToolCall("bad", "read", {}),
        ToolCall("independent", "read", {"value": 1}), ToolCall("dependent", "write", {"value": 1}))), ModelResponse(text="done")])
    rt = make_runtime(tmp_path, platform, tools)
    session = rt.create_session("agent.fast", workspace_dir=tmp_path, permission_mode="full-access")
    try:
        assert rt.start_turn(session.session_id, "inspect").status is AgentStatus.COMPLETED
        assert calls == ["read"]
        results = {m.tool_call_id: json.loads(m.content) for m in platform.requests[1].messages if m.role is MessageRole.TOOL}
        assert results["independent"]["ok"]
        assert results["dependent"]["data"]["failure_kind"] == "dependency_not_executed"
    finally:
        rt.close()


def test_sandbox_and_nested_execution_share_the_complete_service_contract(tmp_path):
    seen = []
    def probe(context, arguments):
        assert all(context.services.get(key) is not None for key in (
            "execution_step", "visual_feedback", "development_verification", "diff_tracker", "environment_policy"))
        seen.append(context.services["execution_step"].permissions)
        return ToolResult(True, "ready")
    tool = AgentTool("services_probe", "probe", {"type": "object"}, probe)
    platform = Scripted([ModelResponse(tool_calls=(ToolCall("services", tool.name, {}),)), ModelResponse(text="done")])
    rt = make_runtime(tmp_path, platform, [tool])
    session = rt.create_session("agent.fast", workspace_dir=tmp_path)
    try:
        assert rt.start_turn(session.session_id, "probe").status is AgentStatus.COMPLETED
        session = rt.store.load(session.session_id)
        step = rt._build_step_context(session, next_model_step=False)
        result = rt._invoke_code_mode_tool(session, parent_call_id="nested", tool_name=tool.name,
                                          arguments={}, token=CancellationToken(), step=step)
        assert result["ok"] and len(seen) == 2
    finally:
        rt.close()


def test_transport_recovery_has_a_real_limit(monkeypatch, tmp_path):
    import app.agent_runtime.turn_runner as runner
    monkeypatch.setattr(runner, "wait_for_signal", lambda *a: "timeout")
    class Broken:
        def __init__(self): self.calls = 0
        def execute_chat(self, *a):
            self.calls += 1
            raise AITransportError("connection unavailable")
    platform = Broken()
    rt = make_runtime(tmp_path, platform, limits=AgentLimits(transport_retries=1))
    session = rt.create_session("agent.fast", workspace_dir=tmp_path)
    try:
        assert rt.start_turn(session.session_id, "inspect").status is AgentStatus.FAILED
        assert platform.calls == 2
        receipts = [e for e in rt.store.events(session.session_id) if e.kind.value == "model_response_rejected"]
        assert receipts[-1].data["will_retry"] is False
    finally:
        rt.close()


def test_unknown_operation_cannot_turn_into_unbounded_model_polling(tmp_path):
    calls = []
    def unresolved(c, a):
        calls.append(1)
        raise BrowserOperationError("unknown operation", operation_id="op", resource="browser")
    tool = AgentTool("browser_probe", "probe", {"type": "object"}, unresolved)
    platform = Scripted([ModelResponse(tool_calls=(ToolCall(str(i), tool.name, {}),)) for i in range(10)])
    rt = make_runtime(tmp_path, platform, [tool])
    session = rt.create_session("agent.fast", workspace_dir=tmp_path)
    try:
        assert rt.start_turn(session.session_id, "inspect").status is AgentStatus.LIMIT_REACHED
        assert len(platform.requests) == len(calls) == 2
    finally:
        rt.close()


def test_unchanged_browser_polling_needs_observable_progress():
    from app.agent_runtime.contracts import AgentEventKind
    from app.agent_runtime.execution_progress import unresolved_operation_loop
    def observation(effect):
        return SimpleNamespace(kind=AgentEventKind.TOOL_COMPLETED,
            data={"tool": "browser_state", "data": {"browser_id": "b", "effect": effect}})
    assert unresolved_operation_loop([observation("unchanged")] * 3) == "unchanged:b"
    assert unresolved_operation_loop([observation("unchanged"), observation("changed"), observation("unchanged")]) is None


def test_reducer_uses_one_aggregate_estimation_and_preserves_full_history():
    history = tuple(AIMessage(role=MessageRole.TOOL, name="read", tool_call_id=str(i), content="a" * 12000) for i in range(100))
    calls = []
    projected, stats = reduce_tool_outputs(history, per_output_token_limit=1000, target_total_tokens=10000,
        estimate_total=lambda values: calls.append(1) or sum(len(str(v.content)) // 3 for v in values))
    assert len(calls) == 1 and stats.tool_outputs_collapsed > 0
    assert all(len(m.content) == 12000 for m in history)
    assert [m.tool_call_id for m in projected] == [m.tool_call_id for m in history]


def test_default_working_budget_is_operating_policy_not_provider_capacity():
    rt = SimpleNamespace(limits=AgentLimits(), platform=SimpleNamespace())
    limits = resolve_context_limits(rt, SimpleNamespace(profile_id="test", session_id="s"))
    assert not limits.window_known
    assert limits.working_context_tokens == 64000
    assert limits.working_context_source == "runtime_policy"


def test_long_history_is_projected_before_first_model_call_without_erasing_durable_evidence(tmp_path):
    platform = Scripted([ModelResponse(text="done")])
    rt = make_runtime(tmp_path / "state", platform)
    session = rt.create_session("agent.fast", workspace_dir=tmp_path)
    session.messages.append(AIMessage(role=MessageRole.USER, content="Earlier investigation"))
    for i in range(100):
        call = ToolCall(f"old-{i}", "read", {"path": f"source-{i}"})
        session.messages.extend((AIMessage(role=MessageRole.ASSISTANT, content="", tool_calls=(call,)),
            AIMessage(role=MessageRole.TOOL, name="read", tool_call_id=call.call_id,
                      content=json.dumps({"ok": True, "content": "evidence " * 2000}))))
    rt.store.save(session)
    try:
        result = rt.start_turn(session.session_id, "Align the header; preserve the current brand")
        assert result.status is AgentStatus.COMPLETED
        assert len(platform.requests) == 1  # no model-driven compaction or recovery round
        request = platform.requests[0]
        assert any(m.content == "Align the header; preserve the current brand" for m in request.messages)
        from app.agent_runtime.context_budget import estimate_tokens
        assert estimate_tokens(request.messages, request.tools) < 64000
        durable = rt.store.load(session.session_id).messages
        assert sum(len(str(m.content)) for m in durable) > 1_000_000
    finally:
        rt.close()


def test_long_retry_after_does_not_sleep_outside_recovery_budget(monkeypatch, tmp_path):
    import app.agent_runtime.turn_runner as runner
    sleeps = []
    monkeypatch.setattr(runner, "wait_for_signal", lambda *a: sleeps.append(a))
    class Busy:
        calls = 0
        def execute_chat(self, *a):
            self.calls += 1
            raise AITransportError("busy", retry_after_seconds=3600)
    platform = Busy()
    rt = make_runtime(tmp_path, platform)
    session = rt.create_session("agent.fast", workspace_dir=tmp_path)
    try:
        assert rt.start_turn(session.session_id, "inspect").status is AgentStatus.FAILED
        assert platform.calls == 1 and sleeps == []
    finally:
        rt.close()


def test_foreign_workspace_change_does_not_become_a_task_obligation(tmp_path):
    rt = make_runtime(tmp_path / "state", Scripted([]))
    workspace = tmp_path / "project"
    workspace.mkdir()
    session = rt.create_session("agent.fast", workspace_dir=workspace)
    session.current_turn_id = "own-turn"
    rt.store.save(session)
    try:
        rt.development_verification.begin(session)
        (workspace / "Foreign.tsx").write_text("other task")
        rt.development_verification.observe(session, scan_git=True)
        assert rt.development_verification.summary(session)["subjects"] == []
        (workspace / "Own.tsx").write_text("own task")
        rt.diff_trackers.for_turn(session.session_id, session.current_turn_id).record_text_change("Own.tsx", before=None, after="own task")
        rt.development_verification.observe(session)
        assert [s["path"] for s in rt.development_verification.summary(session)["subjects"]] == ["Own.tsx"]
    finally:
        rt.close()


def test_extension_timeout_retains_operation_and_fences_new_input(monkeypatch):
    from app.agent_runtime.browser_extension_bridge import BrowserExtensionBridge
    bridge = BrowserExtensionBridge(port=0, token="testing-operation-token")
    monkeypatch.setattr(bridge, "start", lambda: None)
    dispatched = []
    def timeout(command, seconds):
        dispatched.append(command)
        command.dispatched_at = 1
        return False
    monkeypatch.setattr(bridge, "_await_result", timeout)
    with pytest.raises(BrowserOperationError) as error:
        bridge.call("click", {"session_id": "s"})
    pending = dispatched[0]
    assert pending.command_id in bridge._pending and not pending.cancelled
    monkeypatch.setattr(pending.settled, "wait", lambda **kw: False)
    with pytest.raises(BrowserOperationError) as repeat:
        bridge.call("click", {"session_id": "s"})
    assert repeat.value.operation_id == error.value.operation_id
    assert len(dispatched) == 1
    # A late receipt is retained and can be reconciled through a fresh read.
    monkeypatch.setattr(pending.settled, "wait", lambda **kw: True)
    monkeypatch.setattr(bridge, "_await_result", lambda *a: True)
    assert bridge.call("state", {"session_id": "s"}) == {}
    assert bridge._unresolved == {}
    bridge.stop()


def test_extension_protocol_reconciles_a_late_settlement_without_replaying():
    from urllib.request import Request, urlopen
    from app.agent_runtime.browser_extension_bridge import BrowserExtensionBridge
    bridge = BrowserExtensionBridge(port=0, token="testing-operation-token")
    bridge.start()
    errors = []
    def invoke():
        try:
            bridge.call("click", {"session_id": "s"})
        except BrowserOperationError as exc:
            errors.append(exc)
    worker = threading.Thread(target=invoke)
    worker.start()
    def request(path, body=None):
        req = Request(bridge.url + path, data=json.dumps(body).encode() if body is not None else None,
                      headers={"X-Loom-Token": bridge.token, "Content-Type": "application/json"})
        with urlopen(req, timeout=3) as response:
            return json.load(response)
    try:
        command = request("/browser-extension/v1/poll?client_id=test&version=0.1.27")["command"]
        identifier = command["id"]
        request("/browser-extension/v1/result", {"id": identifier, "ok": False, "error": "unknown",
            "operation": {"operation_id": identifier, "recovery_scope": "s", "execution_status": "unknown"}})
        worker.join(3)
        assert len(errors) == 1 and errors[0].operation_id == identifier
        original = bridge._pending[identifier]
        assert not original.settled.is_set()
        request("/browser-extension/v1/result", {"id": identifier, "ok": True, "result": {"done": True},
            "operation": {"operation_id": identifier, "recovery_scope": "s", "execution_status": "settled"}})
        assert identifier not in bridge._pending and original.settled.is_set()
        assert original.result == {"done": True} and original.error == ""
    finally:
        bridge.stop()
        worker.join(3)


def test_finite_process_wait_uses_its_existing_lifecycle_deadline(tmp_path, monkeypatch):
    import sys
    from app.agent_runtime.process_runtime import ManagedProcess
    from app.agent_runtime.process_tools import exec_tool, exec_wait_tool
    windows = []
    original = ManagedProcess.wait
    def wait(process, **kwargs):
        windows.append(kwargs.get("wait_timeout_seconds"))
        return original(process, **kwargs)
    monkeypatch.setattr(ManagedProcess, "wait", wait)
    class Platform:
        calls = 0
        def execute_chat(self, profile, request):
            self.calls += 1
            if self.calls == 1:
                return ModelResponse(tool_calls=(ToolCall("start", "exec", {
                    "argv": [sys.executable, "-c", "import time; time.sleep(.05); print('finished')"],
                    "wait": False, "timeout_seconds": 3}),))
            if self.calls == 2:
                data = json.loads(next(m.content for m in reversed(request.messages) if m.role is MessageRole.TOOL))["data"]
                return ModelResponse(tool_calls=(ToolCall("await", "exec_wait", {"process_id": data["process_id"]}),))
            return ModelResponse(text="done")
    platform = Platform()
    rt = make_runtime(tmp_path / "state", platform, [exec_tool(), exec_wait_tool()])
    session = rt.create_session("agent.fast", workspace_dir=tmp_path, permission_mode="full-access")
    try:
        assert rt.start_turn(session.session_id, "run the finite job").status is AgentStatus.COMPLETED
        assert windows == [None] and platform.calls == 3
    finally:
        rt.close()
