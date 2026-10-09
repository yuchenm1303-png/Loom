"""A commit appends an event and replaces the session snapshot; a crash between the two must be redone.

The snapshot of a long session is megabytes, so it is encoded once per commit and the
same text goes into the journal and into session.json.
"""
from __future__ import annotations

import json
import uuid

import pytest

from app.agent_runtime import AgentEvent, AgentEventKind, AgentSession, AgentStatus, FileAgentSessionStore
from app.agent_runtime.storage import session_to_dict, utc_now


class Crash(Exception):
    pass


def _store(tmp_path):
    store = FileAgentSessionStore(tmp_path / "state")
    session = AgentSession(
        session_id=str(uuid.uuid4()), profile_id="agent.fast", system_prompt="prompt",
        workspace_dir=str(tmp_path), created_at=utc_now(), updated_at=utc_now(),
    )
    store.create(session)
    return store, session


def _event(session, event_id, kind=AgentEventKind.TURN_STARTED, **data):
    return AgentEvent(event_id, session.session_id, "turn-1", kind, utc_now(), data)


def test_a_commit_writes_the_same_snapshot_to_the_journal_and_to_session_json(tmp_path, monkeypatch):
    store, session = _store(tmp_path)
    session.status = AgentStatus.RUNNING
    session.final_text = "snapshot ✓ with unicode and\nnewlines"
    seen = {}
    real_write = store._write_session

    def spy(target, data):
        directory = store.session_dir(target.session_id)
        seen["journal"] = json.loads((directory / ".pending-commit.json").read_text(encoding="utf-8"))
        seen["data"] = data
        return real_write(target, data)

    monkeypatch.setattr(store, "_write_session", spy)
    store.commit_event(session, _event(session, "event-1", text="hi"))

    directory = store.session_dir(session.session_id)
    on_disk = json.loads((directory / "session.json").read_text(encoding="utf-8"))
    assert seen["journal"]["session"] == on_disk == json.loads(seen["data"])
    assert on_disk["updated_at"] == session.updated_at
    assert seen["journal"]["event"]["event_id"] == "event-1"
    assert not (directory / ".pending-commit.json").exists()
    assert [event.event_id for event in store.events(session.session_id)] == ["event-1"]
    assert store.load(session.session_id).final_text == session.final_text


@pytest.mark.parametrize("crash_in", ["_append_event", "_write_session"])
def test_a_crash_inside_a_commit_is_redone_exactly_once(tmp_path, monkeypatch, crash_in):
    store, session = _store(tmp_path)
    session.status = AgentStatus.WAITING_APPROVAL
    session.current_turn_id = "turn-1"
    real = getattr(store, crash_in)

    def crash(*args, **kwargs):
        # Before the event is appended, or after it but before the snapshot lands.
        raise Crash

    monkeypatch.setattr(store, crash_in, crash)
    with pytest.raises(Crash):
        store.commit_event(session, _event(session, "event-2", AgentEventKind.TURN_STARTED))
    monkeypatch.setattr(store, crash_in, real)

    directory = store.session_dir(session.session_id)
    assert (directory / ".pending-commit.json").exists()
    restarted = FileAgentSessionStore(tmp_path / "state")
    restored = restarted.load(session.session_id)

    assert restored.status is AgentStatus.WAITING_APPROVAL and restored.current_turn_id == "turn-1"
    assert restored.updated_at == session.updated_at
    assert [event.event_id for event in restarted.events(session.session_id)] == ["event-2"]
    assert not (directory / ".pending-commit.json").exists()
    # and the recovered snapshot is the full, valid document
    assert json.loads((directory / "session.json").read_text(encoding="utf-8")) == session_to_dict(restored)


def test_a_later_commit_after_recovery_keeps_both_events(tmp_path, monkeypatch):
    store, session = _store(tmp_path)
    monkeypatch.setattr(store, "_write_session", lambda *args: (_ for _ in ()).throw(Crash()))
    with pytest.raises(Crash):
        store.commit_event(session, _event(session, "event-3"))
    monkeypatch.undo()

    restarted = FileAgentSessionStore(tmp_path / "state")
    revived = restarted.load(session.session_id)
    restarted.commit_event(revived, _event(revived, "event-4", AgentEventKind.USER_MESSAGE, text="next"))

    assert [event.event_id for event in restarted.events(session.session_id)] == ["event-3", "event-4"]
