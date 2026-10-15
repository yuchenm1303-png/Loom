"""Atomic snapshot retries and committed-event recovery on Windows."""
from __future__ import annotations

import json
import os
import uuid
from pathlib import Path

import pytest

from app.agent_runtime import AgentEvent, AgentEventKind, AgentSession, FileAgentSessionStore
from app.agent_runtime import journal
from app.agent_runtime.storage import utc_now


def denied(winerror=5):
    exc = PermissionError(13, "file temporarily busy")
    exc.winerror = winerror
    return exc


def make_session(tmp_path):
    store = FileAgentSessionStore(tmp_path / "state")
    session = AgentSession(
        session_id=str(uuid.uuid4()), profile_id="agent.fast",
        system_prompt="prompt", workspace_dir=str(tmp_path),
        created_at=utc_now(), updated_at=utc_now(),
    )
    store.create(session)
    return store, session


@pytest.mark.parametrize("winerror", [5, 32, 33])
def test_snapshot_retries_transient_windows_conflicts(tmp_path, monkeypatch, winerror):
    store, session = make_session(tmp_path)
    target = store.session_dir(session.session_id) / "session.json"
    original = journal.os.replace
    attempts = 0
    sleeps = []

    def flaky(source, destination):
        nonlocal attempts
        if Path(destination) == target:
            attempts += 1
            if attempts < 3:
                raise denied(winerror)
        return original(source, destination)

    monkeypatch.setattr(journal.os, "replace", flaky)
    monkeypatch.setattr(journal.time, "sleep", sleeps.append)
    session.final_text = "persisted after contention"
    store.save(session)

    assert attempts == 3
    assert sleeps == list(journal._REPLACE_RETRY_DELAYS[:2])
    assert store.load(session.session_id).final_text == session.final_text
    assert not list(target.parent.glob(".session.json.*.tmp"))


def test_atomic_journal_helpers_retry_too(tmp_path, monkeypatch):
    target = tmp_path / "snapshot.json"
    original = journal.os.replace
    failures = 2

    def flaky(source, destination):
        nonlocal failures
        if Path(destination) == target and failures:
            failures -= 1
            raise denied(32)
        return original(source, destination)

    monkeypatch.setattr(journal.os, "replace", flaky)
    monkeypatch.setattr(journal.time, "sleep", lambda delay: None)
    journal.atomic_json(target, {"version": 1})
    journal.atomic_text(target, '{"version":2}')
    assert json.loads(target.read_text(encoding="utf-8")) == {"version": 2}
    assert failures == 0
    assert not list(tmp_path.glob(".*.tmp"))


def test_persistent_denial_preserves_event_redo_journal(tmp_path, monkeypatch):
    store, session = make_session(tmp_path)
    directory = store.session_dir(session.session_id)
    target = directory / "session.json"
    before = json.loads(target.read_text(encoding="utf-8"))
    original = journal.os.replace
    attempts = 0
    sleeps = []

    def blocked(source, destination):
        nonlocal attempts
        if Path(destination) == target:
            attempts += 1
            raise denied(5)
        return original(source, destination)

    monkeypatch.setattr(journal.os, "replace", blocked)
    monkeypatch.setattr(journal.time, "sleep", sleeps.append)
    session.final_text = "recover after contention"
    event = AgentEvent(
        "event-1", session.session_id, "turn-1",
        AgentEventKind.TURN_STARTED, utc_now(), {},
    )
    with pytest.raises(PermissionError):
        store.commit_event(session, event)

    assert attempts == len(journal._REPLACE_RETRY_DELAYS) + 1
    assert sleeps == list(journal._REPLACE_RETRY_DELAYS)
    assert json.loads(target.read_text(encoding="utf-8")) == before
    assert (directory / ".pending-commit.json").is_file()
    assert not list(directory.glob(".session.json.*.tmp"))

    monkeypatch.setattr(journal.os, "replace", original)
    restarted = FileAgentSessionStore(tmp_path / "state")
    assert restarted.load(session.session_id).final_text == "recover after contention"
    assert [e.event_id for e in restarted.events(session.session_id)] == ["event-1"]
    assert not (directory / ".pending-commit.json").exists()


def test_unrelated_write_failure_is_not_retried(tmp_path, monkeypatch):
    attempts = 0

    def disk_full(source, destination):
        nonlocal attempts
        attempts += 1
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(journal.os, "replace", disk_full)
    monkeypatch.setattr(journal.time, "sleep", lambda _: pytest.fail("unexpected retry"))
    with pytest.raises(OSError) as exc:
        journal.atomic_text(tmp_path / "session.json", "{}")
    assert exc.value.errno == 28 and attempts == 1
    assert not list(tmp_path.glob(".*.tmp"))


@pytest.mark.skipif(os.name != "nt", reason="Windows-only sharing semantics")
def test_windows_reader_released_during_retry(tmp_path):
    import threading

    store, session = make_session(tmp_path)
    target = store.session_dir(session.session_id) / "session.json"
    reader = target.open("rb")
    timer = threading.Timer(0.15, reader.close)
    timer.start()
    try:
        session.final_text = "saved after reader closed"
        store.save(session)
    finally:
        timer.join(timeout=2)
        reader.close()
    assert store.load(session.session_id).final_text == session.final_text
