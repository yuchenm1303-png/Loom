"""Browser ownership spans turns; inactive leases expire without evicting execution."""
import threading

import pytest

from app.agent_runtime.browser_session import BrowserPageState, BrowserSessionManager, BrowserSessionUnavailable


class Backend:
    backend_name = "lifecycle-fake"
    def __init__(self, _options):
        self.closed = False
    def start(self):
        return BrowserPageState(url="about:blank", title="")
    def state(self):
        return self.start()
    def close(self):
        self.closed = True


def test_idle_expiry_has_explicit_reason_and_activity_resets_clock():
    clock = [0.0]
    manager = BrowserSessionManager(Backend, idle_timeout_seconds=1200, clock=lambda: clock[0])
    browser = manager.start("owner")
    clock[0] = 1100
    manager.state("owner", browser.browser_id)
    clock[0] = 1201
    assert manager.reap_idle() == ()
    clock[0] = 2301
    assert manager.reap_idle() == (browser.browser_id,)
    assert browser.backend.closed
    with pytest.raises(BrowserSessionUnavailable) as caught:
        manager.state("owner", browser.browser_id)
    assert caught.value.lifecycle["reason"] == "idle_expired"


def test_active_turn_is_protected_and_deactivation_starts_full_idle_period():
    clock = [0.0]
    manager = BrowserSessionManager(Backend, idle_timeout_seconds=1200, clock=lambda: clock[0])
    browser = manager.start("owner")
    manager.set_owner_active("owner", True)
    clock[0] = 9999
    assert manager.reap_idle() == ()
    manager.set_owner_active("owner", False)
    assert manager.reap_idle() == ()
    clock[0] += 1201
    assert manager.reap_idle() == (browser.browser_id,)


def test_capacity_reclaims_only_inactive_lru_session():
    clock = [0.0]
    manager = BrowserSessionManager(Backend, max_sessions_total=2, clock=lambda: clock[0])
    active = manager.start("active")
    manager.set_owner_active("active", True)
    clock[0] = 1
    inactive = manager.start("inactive")
    clock[0] = 2
    newer = manager.start("new")
    assert not active.backend.closed
    assert inactive.backend.closed
    assert manager.active_count() == 2
    with pytest.raises(BrowserSessionUnavailable) as caught:
        manager.state("inactive", inactive.browser_id)
    assert caught.value.lifecycle["reason"] == "evicted_for_capacity"


def test_runtime_release_tombstone_survives_new_manager_without_snapshot_rewrite(tmp_path):
    from test_browser_runtime import _runtime
    runtime, _platform, session, _calls, _created, _workspace = _runtime(tmp_path, [])
    try:
        browser = runtime.browser_sessions.start(session.session_id)
        before = (runtime.store.session_dir(session.session_id) / "session.json").read_bytes()
        runtime.browser_sessions.close(session.session_id, browser.browser_id, reason="explicit_close")
        assert (runtime.store.session_dir(session.session_id) / "session.json").read_bytes() == before
        runtime._configure_browser_connection(cdp_url="", extension=False, persist_profile=False, profile_dir=None)
        with pytest.raises(BrowserSessionUnavailable) as caught:
            runtime.browser_sessions.state(session.session_id, browser.browser_id)
        assert caught.value.lifecycle["reason"] == "explicit_close"
        events = runtime.store.events(session.session_id)
        assert len([e for e in events if e.kind.value == "browser_session_released"]) == 1
    finally:
        runtime.close()


def test_host_restart_reference_records_open_lease_as_released(tmp_path):
    from test_browser_runtime import _runtime
    from app.agent_runtime.contracts import AgentEvent, AgentEventKind
    from app.agent_runtime.storage import utc_now
    runtime, _platform, session, _calls, _created, _workspace = _runtime(tmp_path, [])
    try:
        runtime.store.append_event(AgentEvent(event_id="old-open", session_id=session.session_id,
            turn_id="previous", kind=AgentEventKind.BROWSER_SESSION_OPENED, created_at=utc_now(),
            data={"browser_id": "lost-after-restart"}))
        with pytest.raises(BrowserSessionUnavailable) as caught:
            runtime.browser_sessions.state(session.session_id, "lost-after-restart")
        assert caught.value.lifecycle["reason"] == "host_restart"
        releases = [e for e in runtime.store.events(session.session_id) if e.kind.value == "browser_session_released"]
        assert releases[-1].data["reason"] == "host_restart"
    finally:
        runtime.close()


def test_idle_sweep_cannot_close_backend_during_operation():
    entered, release = threading.Event(), threading.Event()
    class BlockingBackend(Backend):
        def state(self):
            entered.set()
            assert release.wait(3)
            return self.start()
    clock = [0.0]
    manager = BrowserSessionManager(BlockingBackend, idle_timeout_seconds=1200, clock=lambda: clock[0])
    browser = manager.start("owner")
    worker = threading.Thread(target=manager.state, args=("owner", browser.browser_id))
    worker.start()
    assert entered.wait(1)
    clock[0] = 9999
    try:
        assert manager.reap_idle() == ()
        assert not browser.backend.closed
    finally:
        release.set()
        worker.join(3)
    assert not worker.is_alive()
    assert manager.reap_idle() == ()
    manager.close_all()


@pytest.mark.parametrize("outcome", ["completed", "failed", "cancelled"])
def test_runtime_terminal_paths_release_activity_without_closing_handles(tmp_path, outcome):
    from test_browser_runtime import _runtime
    from app.ai import ModelResponse
    from app.agent_runtime import PermissionMode
    from app.ai.errors import AITransportError
    runtime, platform, session, _calls, created, _workspace = _runtime(
        tmp_path, [ModelResponse(text="Done")], mode=PermissionMode.FULL_ACCESS)
    manager = runtime.browser_sessions
    browser = manager.start(session.session_id)
    if outcome == "failed":
        def fail(*_a, **_k):
            raise AITransportError("unavailable", retryable=False, status_code=429)
        runtime.platform.execute_chat = fail
    elif outcome == "cancelled":
        def cancel(*_a, **_k):
            runtime.cancel(session.session_id)
            return ModelResponse(text="Cancelled candidate")
        runtime.platform.execute_chat = cancel
    try:
        result = runtime.start_turn(session.session_id, "Run")
        assert result.status.value == outcome
        assert session.session_id not in manager._active_owners
        assert not created[0].closed
        assert manager.list(session.session_id)[0]["browser_id"] == browser.browser_id
    finally:
        runtime.close()


def test_approval_wait_keeps_browser_and_resumes_same_handle(tmp_path):
    from test_browser_runtime import _runtime
    from app.ai import ModelResponse, ToolCall
    runtime, _platform, session, _calls, created, _workspace = _runtime(tmp_path, [
        ModelResponse(tool_calls=(ToolCall("open-approval", "browser_open", {"url": "https://example.com/"}),)),
        ModelResponse(text="Done"),
    ])
    manager = runtime.browser_sessions
    browser = manager.start(session.session_id)
    try:
        result = runtime.start_turn(session.session_id, "Open the browser")
        assert result.status.value == "waiting_approval"
        assert session.session_id not in manager._active_owners
        assert not created[0].closed
        resumed = runtime.resume_approval(session.session_id, "open-approval", approved=True)
        assert resumed.status.value == "completed"
        assert manager.list(session.session_id)[0]["browser_id"] == browser.browser_id
        assert not created[0].closed
    finally:
        runtime.close()
