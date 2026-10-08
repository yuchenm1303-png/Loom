from types import SimpleNamespace

import pytest

from app.agent_runtime import AgentStatus
from app.app_server_reasoning import ReasoningManagedLoomAppServerService


def _service(session, *, active=False, with_recovery=True):
    service = object.__new__(ReasoningManagedLoomAppServerService)
    calls = []
    replacement = SimpleNamespace(session_id=session.session_id, status=AgentStatus.INTERRUPTED)

    def recover(session_id):
        calls.append(session_id)

    service.runtime = SimpleNamespace(recover_interrupted=recover) if with_recovery else SimpleNamespace()
    service._is_active = lambda _session_id: active
    service._load = lambda _session_id: replacement
    return service, calls, replacement


def test_orphaned_old_running_thread_is_recovered_before_model_switch():
    original = SimpleNamespace(session_id="old-thread", status=AgentStatus.RUNNING)
    service, calls, recovered = _service(original)

    resolved = service._recover_orphaned_thread_model_turn(original)

    assert calls == ["old-thread"]
    assert resolved is recovered
    assert not service._thread_model_blocked(resolved)


def test_live_old_thread_still_blocks_model_switch_without_recovery():
    original = SimpleNamespace(session_id="active-thread", status=AgentStatus.RUNNING)
    service, calls, _replacement = _service(original, active=True)

    assert service._recover_orphaned_thread_model_turn(original) is original
    assert calls == []
    assert service._thread_model_blocked(original)


def test_waiting_approval_must_not_be_recovered_automatically():
    original = SimpleNamespace(session_id="approval-thread", status=AgentStatus.WAITING_APPROVAL)
    service, calls, _replacement = _service(original)

    assert service._recover_orphaned_thread_model_turn(original) is original
    assert calls == []
    assert service._thread_model_blocked(original)


def test_unsupported_recovery_fails_closed():
    original = SimpleNamespace(session_id="old-thread", status=AgentStatus.RUNNING)
    service, calls, _replacement = _service(original, with_recovery=False)

    assert service._recover_orphaned_thread_model_turn(original) is original
    assert calls == []
    assert service._thread_model_blocked(original)
