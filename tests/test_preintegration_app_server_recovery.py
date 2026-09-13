from __future__ import annotations

from app.agent_runtime import AgentSession, AgentStatus, FileAgentSessionStore
from app.agent_runtime.storage import utc_now
from app.agent_runtime.tools import ToolRegistry
from app.app_server import LoomAppServerService


class _Runtime:
    def __init__(self, store):
        self.store = store
        self.tools = ToolRegistry()
        self.unclean_calls = []
        self.safe_calls = []

    def subscribe(self, listener):
        self.listener = listener

    def get_session(self, session_id):
        return self.store.load(session_id)

    def recover_interrupted(self, session_id):
        self.unclean_calls.append(session_id)
        session = self.store.load(session_id)
        session.status = AgentStatus.INTERRUPTED
        self.store.save(session)
        return session

    def recover_turn_if_idle(self, session_id, turn_id):
        self.safe_calls.append((session_id, turn_id))
        return self.store.load(session_id)


def _service(tmp_path):
    root = tmp_path / "home" / "agent_runtime" / "sessions"
    store = FileAgentSessionStore(root)
    runtime = _Runtime(store)
    workspace = tmp_path / "project"
    workspace.mkdir()
    session = AgentSession(
        session_id="thread-1",
        profile_id="agent.fast",
        system_prompt="system",
        workspace_dir=str(workspace),
        created_at=utc_now(),
        updated_at=utc_now(),
        status=AgentStatus.RUNNING,
        current_turn_id="turn-1",
    )
    store.create(session)
    service = LoomAppServerService(
        runtime=runtime,
        store=store,
        model="agent.fast",
        default_workspace=workspace,
    )
    return service, runtime


def test_app_server_load_is_observational_for_persisted_running_turn(tmp_path):
    service, runtime = _service(tmp_path)

    loaded = service._load("thread-1")

    assert loaded.status is AgentStatus.RUNNING
    assert loaded.current_turn_id == "turn-1"
    assert runtime.unclean_calls == []


def test_thread_resume_routes_explicit_safe_handoff_without_unclean_finalization(tmp_path):
    service, runtime = _service(tmp_path)
    service._launch = lambda _session_id, operation: operation()

    payload = service.thread_resume({"threadId": "thread-1", "recoverTurnId": "turn-1"})

    assert runtime.safe_calls == [("thread-1", "turn-1")]
    assert runtime.unclean_calls == []
    assert payload["thread"]["currentTurnId"] == "turn-1"


def test_thread_resume_rejects_wrong_safe_handoff_turn_id(tmp_path):
    service, runtime = _service(tmp_path)

    try:
        service.thread_resume({"threadId": "thread-1", "recoverTurnId": "other-turn"})
    except ValueError as exc:
        assert "recoverTurnId" in str(exc)
    else:
        raise AssertionError("wrong recovery turn id must fail closed")
    assert runtime.safe_calls == []
    assert runtime.unclean_calls == []
