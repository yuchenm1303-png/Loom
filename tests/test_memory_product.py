from __future__ import annotations

import json
import threading
import time

from app.agent_runtime import (
    AgentRuntime,
    AgentStatus,
    FileAgentSessionStore,
    MemoryCandidate,
    MemoryCategory,
    MemoryScope,
    SandboxManager,
    SandboxPolicy,
)
from app.agent_runtime.workspace_tools import loom_default_tools
from app.ai import AGENT_FAST_ROLE, ModelResponse, ModelUsage
from app.app_server import PROTOCOL_VERSION
from app.app_server_project_move import (
    ProjectMovableLoomAppServerService,
    ProjectMovableLoomRpcController,
)
from app.settings import LoomSettingsStore, SETTINGS_UPDATE_PREFIX


class ScriptedPlatform:
    def __init__(self, responses=()):
        self.responses = list(responses)
        self.requests = []
        self._lock = threading.Lock()

    def execute_chat(self, profile_id, request):
        with self._lock:
            self.requests.append((profile_id, request))
            if not self.responses:
                raise AssertionError("scripted platform ran out of responses")
            return self.responses.pop(0)


def _memory_payload() -> str:
    return json.dumps(
        {
            "summary": "Testing convention.",
            "memories": [
                {
                    "text": "This workspace uses pytest for its test suite.",
                    "scope": "workspace",
                    "category": "decision",
                    "importance": 4,
                    "evidence": "User chose pytest for this workspace.",
                }
            ],
        }
    )


def _wait_until(predicate, *, timeout=3.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.01)
    raise AssertionError("condition did not become true before timeout")


def _runtime(tmp_path, platform, **kwargs):
    store = FileAgentSessionStore(tmp_path / "home")
    runtime = AgentRuntime(
        platform=platform,
        store=store,
        tools=loom_default_tools(),
        sandbox_manager=SandboxManager(policy=SandboxPolicy.OFF),
        auto_configure_web_search=False,
        **kwargs,
    )
    return runtime, store


def _initialize(controller):
    response = controller.handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": PROTOCOL_VERSION,
                "clientInfo": {"name": "memory-product-test", "version": "1"},
            },
        }
    )
    assert response is not None
    return response["result"]


def test_memory_settings_are_typed_durable_and_legacy_compatible(tmp_path):
    settings = LoomSettingsStore(tmp_path / "home")
    snapshot = settings.snapshot()
    assert snapshot["schemaVersion"] == 3
    assert snapshot["memory"] == {
        "enabled": True,
        "autoExtract": True,
        "semanticAuto": True,
        "idleSeconds": 45,
    }

    snapshot = settings.set_value("memory.enabled", False)
    assert snapshot["memory"]["enabled"] is False
    snapshot = settings.set_value("memory.idleSeconds", 12)
    assert snapshot["memory"]["idleSeconds"] == 12

    envelope = SETTINGS_UPDATE_PREFIX + json.dumps(
        {"path": "memory.semanticAuto", "value": False}
    )
    snapshot = settings.set_capability(envelope, True)
    assert snapshot["memory"]["semanticAuto"] is False

    reloaded = LoomSettingsStore(tmp_path / "home").snapshot()
    assert reloaded["memory"]["enabled"] is False
    assert reloaded["memory"]["idleSeconds"] == 12
    assert reloaded["memory"]["semanticAuto"] is False


def test_disabling_memory_stops_generation_and_reenable_recovers_backlog(tmp_path):
    platform = ScriptedPlatform(
        [
            ModelResponse(text="foreground done", usage=ModelUsage(5, 2, 7)),
            ModelResponse(text=_memory_payload(), usage=ModelUsage(12, 8, 20)),
        ]
    )
    runtime, _store = _runtime(
        tmp_path,
        platform,
        memory_enabled=False,
        memory_auto_extract=True,
        memory_idle_seconds=0.01,
        memory_semantic_auto=False,
    )
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    session = runtime.create_session(AGENT_FAST_ROLE.role_id, workspace_dir=workspace)

    first = runtime.start_turn(session.session_id, "Use pytest for this workspace.")
    assert first.status is AgentStatus.COMPLETED
    time.sleep(0.08)
    assert len(platform.requests) == 1
    assert runtime.memory_store.counts(workspace=workspace)["total"] == 0

    configured = runtime.configure_memory(memory_enabled=True)
    assert configured["enabled"] is True
    _wait_until(lambda: runtime.memory_store.counts(workspace=workspace)["total"] == 1)
    assert len(platform.requests) == 2
    runtime.close()


def test_memory_rpc_and_live_settings_manage_existing_memory_when_disabled(tmp_path):
    platform = ScriptedPlatform([])
    runtime, store = _runtime(
        tmp_path,
        platform,
        memory_auto_extract=False,
        memory_semantic_auto=False,
    )
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    session = runtime.create_session(AGENT_FAST_ROLE.role_id, workspace_dir=workspace)

    runtime.memory_store.add_extraction(
        source_session_id=session.session_id,
        source_turn_id="seed",
        workspace=workspace,
        summary="seed",
        candidates=(
            MemoryCandidate(
                text="This workspace uses pytest.",
                scope=MemoryScope.WORKSPACE,
                category=MemoryCategory.DECISION,
                importance=4,
                evidence="User selected pytest.",
            ),
        ),
    )
    record = runtime.memory_store.consolidate_pending()[0]

    # Persist disabled automation before service startup so settings -> runtime
    # synchronization is tested rather than constructor-only flags.
    settings = LoomSettingsStore(tmp_path / "home")
    settings.set_value("memory.autoExtract", False)
    settings.set_value("memory.semanticAuto", False)

    service = ProjectMovableLoomAppServerService(
        runtime=runtime,
        store=store,
        model="test-model",
        default_workspace=workspace,
    )
    controller = ProjectMovableLoomRpcController(service)
    initialized = _initialize(controller)
    assert initialized["capabilities"]["memory"]["read"] is True
    assert runtime.memory_auto_extract is False
    assert runtime.memory_semantic_auto is False

    listed = controller.handle(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "memory/list",
            "params": {"threadId": session.session_id},
        }
    )
    assert listed["result"]["memories"][0]["memory_id"] == record.memory_id

    searched = controller.handle(
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "memory/search",
            "params": {"threadId": session.session_id, "query": "pytest"},
        }
    )
    assert searched["result"]["memories"][0]["memory_id"] == record.memory_id

    read = controller.handle(
        {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "memory/read",
            "params": {"threadId": session.session_id, "memoryId": record.memory_id},
        }
    )
    assert read["result"]["memory"]["memory_id"] == record.memory_id
    assert read["result"]["evidence"][0]["excerpt"] == "User selected pytest."

    disabled = controller.handle(
        {
            "jsonrpc": "2.0",
            "id": 5,
            "method": "settings/set",
            "params": {"path": "memory.enabled", "value": False},
        }
    )
    assert disabled["result"]["settings"]["memory"]["enabled"] is False
    assert runtime.memory_enabled is False

    # Disabling use/generation must not hide user-owned memory management.
    still_listed = controller.handle(
        {
            "jsonrpc": "2.0",
            "id": 6,
            "method": "memory/list",
            "params": {"threadId": session.session_id},
        }
    )
    assert still_listed["result"]["memories"][0]["memory_id"] == record.memory_id

    status = controller.handle(
        {
            "jsonrpc": "2.0",
            "id": 7,
            "method": "memory/status",
            "params": {"threadId": session.session_id},
        }
    )
    assert status["result"]["memory"]["enabled"] is False

    forgotten = controller.handle(
        {
            "jsonrpc": "2.0",
            "id": 8,
            "method": "memory/forget",
            "params": {"threadId": session.session_id, "memoryId": record.memory_id},
        }
    )
    assert forgotten["result"]["forgotten"] is True
    assert runtime.memory_store.get(record.memory_id) is None
    runtime.close()
