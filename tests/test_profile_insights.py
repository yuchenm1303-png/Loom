from __future__ import annotations

import time
from pathlib import Path

from app.ai import ModelResponse, ModelUsage
from app.agent_runtime import (
    DurableAgentRuntime,
    FileAgentSessionStore,
    PermissionMode,
    ToolRegistry,
)
from app.app_server import PROTOCOL_VERSION
from app.app_server_thread_management import (
    ManagedStreamingLoomAppServerService,
    ManagedStreamingLoomRpcController,
)


class ProfilePlatform:
    def __init__(self) -> None:
        self.responses = [
            ModelResponse(text="first answer", usage=ModelUsage(120, 30, 150)),
            ModelResponse(text="second answer", usage=ModelUsage(40, 10, 50)),
        ]

    def execute_chat(self, _profile_id, request):
        # Auto-title generation is detached from the visible turn. Give it a
        # harmless deterministic answer without consuming a scripted turn.
        if int(getattr(request, "max_output_tokens", 0) or 0) == 48:
            return ModelResponse(text="Usage profile test")
        if not self.responses:
            raise AssertionError("scripted profile responses exhausted")
        return self.responses.pop(0)


def _wait_until(predicate, *, timeout: float = 3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.01)
    raise AssertionError("timed out waiting for profile test turn")


def _build_service(tmp_path: Path):
    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = FileAgentSessionStore(home)
    runtime = DurableAgentRuntime(
        platform=ProfilePlatform(),
        store=store,
        tools=ToolRegistry(()),
        default_permission_mode=PermissionMode.WORKSPACE,
        auto_drain_queue=False,
    )
    service = ManagedStreamingLoomAppServerService(
        runtime=runtime,
        store=store,
        model="test-model",
        default_workspace=workspace,
        default_permission_mode=PermissionMode.WORKSPACE,
    )
    return service, runtime, workspace


def test_profile_insights_uses_durable_model_usage_and_rpc(tmp_path: Path) -> None:
    service, runtime, workspace = _build_service(tmp_path)
    try:
        thread_id = service.thread_start({"workspace": str(workspace)})["thread"]["id"]

        service.turn_start({"threadId": thread_id, "input": "first"})
        _wait_until(lambda: thread_id not in service.runtime_status()["activeThreadIds"])
        service.turn_start({"threadId": thread_id, "input": "second"})
        _wait_until(lambda: thread_id not in service.runtime_status()["activeThreadIds"])

        snapshot = service.profile_insights({"days": 90})
        assert snapshot["totals"]["sessions"] == 1
        assert snapshot["totals"]["turns"] == 2
        assert snapshot["totals"]["modelCalls"] == 2
        assert snapshot["totals"]["inputTokens"] == 160
        assert snapshot["totals"]["outputTokens"] == 40
        assert snapshot["totals"]["totalTokens"] == 200
        assert snapshot["totals"]["activeDays"] == 1
        assert snapshot["peakDay"]["totalTokens"] == 200
        assert sum(day["totalTokens"] for day in snapshot["days"]) == 200
        assert snapshot["models"][0] == {
            "name": "test-model",
            "calls": 2,
            "tokens": 200,
        }

        controller = ManagedStreamingLoomRpcController(service)
        initialized = controller.handle(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": PROTOCOL_VERSION,
                    "clientInfo": {"name": "pytest", "version": "1"},
                },
            }
        )
        assert initialized["result"]["capabilities"]["profileInsights"] == {
            "usage": True,
            "heatmap": True,
        }

        response = controller.handle(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "profile/insights",
                "params": {"days": 90},
            }
        )
        assert response["result"]["totals"]["totalTokens"] == 200
    finally:
        runtime.close()
