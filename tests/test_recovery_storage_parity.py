from __future__ import annotations

import json
import uuid

import pytest

from app.agent_runtime import AgentEvent, AgentEventKind, AgentSession, AgentStatus, FileAgentSessionStore
from app.agent_runtime.storage import session_to_dict, utc_now


def _session(tmp_path) -> tuple[FileAgentSessionStore, AgentSession]:
    store = FileAgentSessionStore(tmp_path / "state")
    session = AgentSession(
        session_id=str(uuid.uuid4()),
        profile_id="agent.fast",
        system_prompt="test system prompt",
        workspace_dir=str(tmp_path),
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    store.create(session)
    return store, session


def _event(session: AgentSession, *, event_id: str, kind: AgentEventKind) -> AgentEvent:
    return AgentEvent(
        event_id=event_id,
        session_id=session.session_id,
        turn_id=session.current_turn_id,
        kind=kind,
        created_at=utc_now(),
        data={},
    )


def test_torn_final_event_is_truncated_before_next_append(tmp_path):
    store, session = _session(tmp_path)
    first = _event(session, event_id="event-1", kind=AgentEventKind.TURN_STARTED)
    store.append_event(first)

    path = store.session_dir(session.session_id) / "events.jsonl"
    with path.open("ab") as handle:
        handle.write(b'{"event_id":"torn"')

    second = _event(session, event_id="event-2", kind=AgentEventKind.TURN_INTERRUPTED)
    store.append_event(second)

    events = store.events(session.session_id)
    assert [event.event_id for event in events] == ["event-1", "event-2"]
    assert path.read_bytes().endswith(b"\n")


def test_redo_recovery_does_not_duplicate_already_appended_event(tmp_path):
    store, session = _session(tmp_path)
    session.status = AgentStatus.INTERRUPTED
    session.current_turn_id = "dead-turn"
    event = _event(session, event_id="stable-event-id", kind=AgentEventKind.TURN_INTERRUPTED)
    store.append_event(event)

    pending = {
        "session": session_to_dict(session),
        "event": {
            "event_id": event.event_id,
            "session_id": event.session_id,
            "turn_id": event.turn_id,
            "kind": event.kind.value,
            "created_at": event.created_at,
            "data": event.data,
        },
    }
    directory = store.session_dir(session.session_id)
    (directory / ".pending-commit.json").write_text(
        json.dumps(pending, ensure_ascii=False),
        encoding="utf-8",
    )

    restored = store.load(session.session_id)
    events = store.events(session.session_id)

    assert restored.status is AgentStatus.INTERRUPTED
    assert [item.event_id for item in events].count("stable-event-id") == 1
    assert not (directory / ".pending-commit.json").exists()


def test_malformed_interior_event_fails_closed(tmp_path):
    store, session = _session(tmp_path)
    first = _event(session, event_id="event-1", kind=AgentEventKind.TURN_STARTED)
    second = _event(session, event_id="event-2", kind=AgentEventKind.TURN_INTERRUPTED)
    store.append_event(first)
    store.append_event(second)

    path = store.session_dir(session.session_id) / "events.jsonl"
    lines = path.read_bytes().splitlines(keepends=True)
    path.write_bytes(lines[0] + b"{not-json}\n" + lines[1])

    with pytest.raises(json.JSONDecodeError):
        store.events(session.session_id)
