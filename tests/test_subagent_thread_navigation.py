"""Opening a delegated session reads it; it must not recover or wait for its live turn."""
from concurrent.futures import ThreadPoolExecutor, Future
from types import SimpleNamespace
import threading

import pytest

from app.agent_runtime import AgentStatus, FileAgentSessionStore, ToolRegistry
from app.agent_runtime.multi_agent_runtime import MultiAgentRuntime
from app.ai import AGENT_FAST_ROLE, ModelResponse
from app.app_server_reasoning import ReasoningManagedLoomAppServerService
from app.app_server import LoomAppServerService


@pytest.mark.parametrize("background", [True, False])
def test_reading_live_and_closed_subagents_preserves_their_identity_without_recovery(tmp_path, background):
    entered, release = threading.Event(), threading.Event()

    class Platform:
        def execute_chat(self, _profile, _request):
            entered.set()
            assert release.wait(10)
            return ModelResponse(text="child-only-result")

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = FileAgentSessionStore(tmp_path / "home" / "agent_runtime" / "sessions")
    runtime = MultiAgentRuntime(platform=Platform(), store=store, tools=ToolRegistry(()), auto_drain_queue=False)
    service = ReasoningManagedLoomAppServerService(runtime=runtime, store=store, model="test-model", default_workspace=workspace)
    pool = ThreadPoolExecutor(max_workers=2)
    try:
        parent = runtime.create_session(AGENT_FAST_ROLE.role_id, workspace_dir=workspace)
        parent.model = "test-model"
        store.save(parent)
        if background:
            child = runtime.agent_control.spawn(parent.session_id, "delegated-only-input", role="goal-line", history_mode="none").node.session_id
        else:
            pool.submit(runtime.agent_control.spawn, parent.session_id, "delegated-only-input", role="goal-line", history_mode="none", background=False)
            assert entered.wait(5)
            child = runtime.agent_graph.list_children(parent.session_id)[0].session_id
        assert entered.wait(5)
        assert service._is_active(child)
        assert service.runtime is runtime
        assert service._record(store.load(child))["parentThreadId"] == parent.session_id
        result = pool.submit(service.thread_read, {"threadId": child, "presentationOnly": True, "turnLimit": 20}).result(timeout=2)
        assert result["thread"]["id"] == child
        assert result["thread"]["parentThreadId"] == parent.session_id
        assert result["thread"]["agentRole"] == "goal-line"
        assert result["thread"]["status"] == "running"
        assert store.load(child).status is AgentStatus.RUNNING
        assert not any(e.kind.value == "turn_interrupted" for e in store.events(child))
        release.set()
        runtime.agent_control.wait(parent.session_id, child, timeout_seconds=5)
        pool.shutdown(wait=True)
        runtime.agent_control.close_agent(parent.session_id, child)
        final = service.thread_read({"threadId": child, "presentationOnly": True, "turnLimit": 20})
        assert final["thread"]["id"] == child
        assert final["thread"]["parentThreadId"] == parent.session_id
        assert final["thread"]["agentRole"] == "goal-line"
        assert final["finalText"] == "child-only-result"
        assert not any(e.kind.value == "turn_interrupted" for e in store.events(child))
    finally:
        release.set()
        pool.shutdown(wait=True)
        runtime.close()


def test_a_queued_subagent_start_counts_as_owned_before_it_has_a_runtime_token():
    from app.agent_runtime.agent_control import AgentControl

    control = AgentControl.__new__(AgentControl)
    control._lock = threading.RLock()
    queued = Future()
    control._futures = {"child": queued}
    service = LoomAppServerService.__new__(LoomAppServerService)
    service._guard = threading.RLock()
    service._active_sessions = {}
    service.runtime = SimpleNamespace(is_turn_active=lambda _: False, agent_control=control)
    assert service._is_active("child")
    queued.set_result(None)
    assert not service._is_active("child")
