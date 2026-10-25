import threading
import time

import pytest

from app.ai import ModelResponse, ModelUsage, ToolCall
from app.agent_runtime import AgentTool, PermissionMode, ToolEffect, ToolResult
from app.app_server import LoomAppServerService
from app.ambient_service import AmbientServiceMixin, ServiceExecutor
from test_app_server import _build_service, _wait_until


class Service(AmbientServiceMixin, LoomAppServerService):
    pass


def build(tmp_path, responses, **kwargs):
    _, runtime, store, platform, workspace = _build_service(tmp_path, responses, **kwargs)
    service = Service(runtime=runtime, store=store, model="test-model", default_workspace=workspace,
                      default_permission_mode=kwargs.get("permission_mode", PermissionMode.WORKSPACE))
    thread_id = service.thread_start({"workspaceDir": str(workspace)})["thread"]["id"]
    return service, runtime, platform, workspace, thread_id


def test_existing_runtime_usage_and_manual_busy_queue(tmp_path):
    service, runtime, platform, workspace, thread = build(tmp_path, [ModelResponse(text="done", usage=ModelUsage(5, 3, 8))])
    try:
        item = service.ambient.put({"threadId": thread, "name": "check", "prompt": "check", "kind": "event"})
        with service._guard:
            service._active_sessions.add(thread)
        service.ambient.event(item["id"], "1")
        service.ambient.tick()
        assert not platform.requests
        with service._guard:
            service._active_sessions.remove(thread)
        service.ambient.tick()
        _wait_until(lambda: not service._is_active(thread))
        service.ambient.tick()
        snapshot = service.ambient.snapshot()
        assert snapshot["runs"][0]["status"] == "completed"
        assert snapshot["tasks"][0]["tokensUsed"] == 8
        assert len(platform.requests) == 1
        assert "Automatic task: check" in str(runtime.get_session(thread).messages)
        executor = ServiceExecutor(service)
        with pytest.raises(ValueError, match="inside"):
            executor.validate({"threadId": thread, "kind": "file", "path": str(workspace.parent / "outside")})
    finally:
        runtime.close()
    assert not service.ambient.owner


def test_automatic_trigger_cannot_bypass_real_tool_approval(tmp_path):
    calls = []
    sensitive = AgentTool(name="sensitive_test_tool", description="Sensitive test action",
        input_schema={"type": "object", "properties": {}}, effect=ToolEffect.SENSITIVE,
        handler=lambda context, arguments: calls.append(True) or ToolResult(ok=True, content="ran"))
    service, runtime, platform, workspace, thread = build(tmp_path,
        [ModelResponse(tool_calls=(ToolCall(call_id="approval", name="sensitive_test_tool", arguments={}),))],
        tools=(sensitive,), permission_mode=PermissionMode.APPROVAL)
    try:
        item = service.ambient.put({"threadId": thread, "name": "approval", "prompt": "act", "kind": "event"})
        service.ambient.event(item["id"], "1")
        service.ambient.tick()
        _wait_until(lambda: not service._is_active(thread))
        service.ambient.tick()
        assert service.ambient.snapshot()["runs"][0]["status"] == "waiting_approval"
        service.ambient.event(item["id"], "2")
        service.ambient.tick()
        assert len(platform.requests) == 1
        assert not calls
        service.ambient.cancel(item["id"])
        service.ambient.tick()
        assert not calls
        assert not service.ambient.snapshot()["tasks"][0]["enabled"]
        assert runtime.get_session(thread).pending_approval is None
    finally:
        runtime.close()


def test_preparation_cancellation_disables_future_wakeups(tmp_path):
    service, runtime, platform, workspace, thread = build(tmp_path, [])
    preparing, release = threading.Event(), threading.Event()
    def prepare(session):
        preparing.set()
        release.wait(3)
    service._prepare_turn = prepare
    try:
        item = service.ambient.put({"threadId": thread, "name": "check", "prompt": "check", "kind": "event"})
        service.ambient.event(item["id"], "1")
        service.ambient.tick()
        assert preparing.wait(1)
        run = service.ambient.snapshot()["runs"][0]
        service.turn_interrupt({"threadId": thread, "turnId": run["turnId"]})
        release.set()
        _wait_until(lambda: not service._is_active(thread))
        service.ambient.tick()
        assert not service.ambient.snapshot()["tasks"][0]["enabled"]
        assert not service.ambient.event(item["id"], "2")
        assert not platform.requests
    finally:
        release.set()
        runtime.close()


def test_live_service_timer_executes_without_renderer_or_connected_client(tmp_path):
    service, runtime, platform, workspace, thread = build(tmp_path, [ModelResponse(text="AMBIENT_NO_CHANGE", usage=ModelUsage(2, 1, 3))])
    try:
        service.ambient.put({"threadId": thread, "name": "live", "prompt": "check", "kind": "once", "nextAt": time.time() + .1})
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            runs = service.ambient.snapshot()["runs"]
            if runs and runs[0]["status"] == "completed":
                break
            time.sleep(.05)
        snapshot = service.ambient.snapshot()
        assert snapshot["runs"][0]["status"] == "completed"
        assert snapshot["tasks"][0]["tokensUsed"] == 3
        assert len(platform.requests) == 1
        assert not snapshot["notifications"]
    finally:
        runtime.close()
