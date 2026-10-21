"""A durable event commits a snapshot of the session; on a long session that was most of the time a step spent.

The snapshot is encoded from what cannot have changed (messages are frozen, context frames are appended once) and
is not written again when it says what the file already says.
"""
from __future__ import annotations

import json
import uuid

import pytest

from app.agent_runtime import AgentEvent, AgentEventKind, AgentSession, FileAgentSessionStore
from app.agent_runtime import storage
from app.agent_runtime.storage import _SnapshotEncoder, session_to_dict, utc_now
from app.ai import AIMessage, ImagePart, MessageRole, TextPart, ToolCall


def _session(tmp_path, *, messages=0):
    store = FileAgentSessionStore(tmp_path / "state")
    session = AgentSession(
        session_id=str(uuid.uuid4()), profile_id="agent.fast", system_prompt="prompt",
        workspace_dir=str(tmp_path), created_at=utc_now(), updated_at=utc_now(),
    )
    for index in range(messages):
        session.messages.append(AIMessage(MessageRole.USER, f"question {index} ✓ \"quoted\"\nsecond line"))
        session.messages.append(AIMessage(MessageRole.ASSISTANT, "", tool_calls=(ToolCall(f"call-{index}", "exec", {"argv": ["echo", str(index)]}),)))
        session.messages.append(AIMessage(MessageRole.TOOL, f"result {index}", tool_call_id=f"call-{index}"))
        session.messages.append(AIMessage(MessageRole.ASSISTANT, f"answer {index}", reasoning="because", phase="final_answer"))
    session.request_context_frames.append({"step_id": "s1", "after_message_count": 2, "messages": [{"role": "user", "content": "page ✓"}], "metadata": {}})
    store.create(session)
    return store, session


def _event(session, event_id, kind=AgentEventKind.TOOL_STARTED, **data):
    return AgentEvent(event_id, session.session_id, "turn-1", kind, utc_now(), data)


def test_the_encoded_snapshot_is_the_session_document(tmp_path):
    _, session = _session(tmp_path, messages=3)
    session.messages.append(AIMessage(MessageRole.USER, (TextPart("look"), ImagePart("data:image/png;base64,AAAA"))))
    session.pending_bindings = {"call-1": {"effect": "read_only"}}
    data, _ = _SnapshotEncoder().encode(session)
    assert json.loads(data) == session_to_dict(session)
    assert b"\n" not in data  # compact: the C encoder, and nothing that has to be re-indented


def test_what_cannot_have_changed_is_not_encoded_again(tmp_path, monkeypatch):
    _, session = _session(tmp_path, messages=5)
    encoder = _SnapshotEncoder()
    encoder.encode(session)
    encoded = []
    real = storage._encode
    monkeypatch.setattr(storage, "_encode", lambda value: encoded.append(value) or real(value))
    encoder.encode(session)
    # Only the messages that carry tool calls are encoded again; the others are reused.
    assert len(encoded) == 5
    assert all(record.get("tool_calls") for record in encoded)
    session.messages.append(AIMessage(MessageRole.USER, "one more"))
    encoded.clear()
    data, _ = encoder.encode(session)
    assert len(encoded) == 6 and json.loads(data) == session_to_dict(session)


def test_a_tool_call_edited_in_place_is_written_as_edited(tmp_path):
    """A tool may redact its own arguments after the message exists; a cached encoding would keep the secret."""
    _, session = _session(tmp_path, messages=2)
    encoder = _SnapshotEncoder()
    before, _ = encoder.encode(session)
    call = next(message for message in session.messages if message.tool_calls).tool_calls[0]
    call.arguments["argv"] = ["[redacted]"]
    after, _ = encoder.encode(session)
    assert b"[redacted]" not in before and b"[redacted]" in after
    assert json.loads(after) == session_to_dict(session)


def test_a_context_frame_edited_after_it_was_saved_is_written_as_edited(tmp_path):
    """capture_context adds its timings to the frame it appended after it has saved the session once."""
    store, session = _session(tmp_path, messages=2)
    encoder = _SnapshotEncoder()
    before, _ = encoder.encode(session)
    session.request_context_frames[-1]["metadata"]["context_capture_phases_ms"] = {"session_save": 12.5}
    after, key = encoder.encode(session)
    assert b"session_save" not in before and b"session_save" in after
    assert json.loads(after) == session_to_dict(session)
    # ...and the store writes it, although the same step already saved the session once
    store.save(session)
    session.request_context_frames[-1]["metadata"]["context_capture_ms"] = 3.5
    store.commit_event(session, _event(session, "e1", AgentEventKind.MODEL_REQUESTED))
    path = store.session_dir(session.session_id) / "session.json"
    assert json.loads(path.read_text(encoding="utf-8"))["request_context_frames"][-1]["metadata"]["context_capture_ms"] == 3.5


def test_messages_that_left_the_session_leave_the_cache(tmp_path):
    _, session = _session(tmp_path, messages=4)
    encoder = _SnapshotEncoder()
    encoder.encode(session)
    assert len(encoder._messages) == 12  # the 12 without tool calls
    session.messages = session.messages[:4]
    session.request_context_frames = []
    data, _ = encoder.encode(session)
    assert len(encoder._messages) == 3
    assert json.loads(data) == session_to_dict(session)


def test_a_snapshot_that_contains_the_placeholder_is_still_encoded_correctly(tmp_path):
    _, session = _session(tmp_path, messages=1)
    # Values equal to the placeholder, in a field that comes before the list it stands in for.
    session.model_selection = storage._MESSAGES_TOKEN
    session.active_skills = {storage._MESSAGES_TOKEN: "x"}
    data, _ = _SnapshotEncoder().encode(session)
    assert json.loads(data) == session_to_dict(session)


def test_an_event_that_changes_nothing_is_appended_without_a_new_snapshot(tmp_path):
    store, session = _session(tmp_path, messages=2)
    session.final_text = "first change"
    store.commit_event(session, _event(session, "e1", AgentEventKind.TOOL_COMPLETED))
    path = store.session_dir(session.session_id) / "session.json"
    written = path.stat()
    written_text = path.read_bytes()

    store.commit_event(session, _event(session, "e2", AgentEventKind.TOOL_STARTED, tool="exec"))
    store.commit_event(session, _event(session, "e3", AgentEventKind.PROCESS_OUTPUT, stdout="x"))

    assert path.read_bytes() == written_text and path.stat().st_mtime_ns == written.st_mtime_ns
    assert not (path.parent / ".pending-commit.json").exists()
    assert [event.event_id for event in store.events(session.session_id)] == ["e1", "e2", "e3"]
    # and the next change is written, with the moved updated_at
    session.final_text = "second change"
    store.commit_event(session, _event(session, "e4", AgentEventKind.TOOL_COMPLETED))
    on_disk = json.loads(path.read_text(encoding="utf-8"))
    assert on_disk["final_text"] == "second change" and on_disk["updated_at"] == session.updated_at
    assert on_disk == session_to_dict(store.load(session.session_id))


def test_a_file_changed_behind_the_stores_back_is_written_again(tmp_path):
    store, session = _session(tmp_path, messages=1)
    session.final_text = "x"
    store.commit_event(session, _event(session, "e1"))
    path = store.session_dir(session.session_id) / "session.json"
    path.write_text('{"version":2,"damaged":true}', encoding="utf-8")
    store.commit_event(session, _event(session, "e2"))
    assert json.loads(path.read_text(encoding="utf-8")) == session_to_dict(session)


def test_a_snapshot_that_failed_to_write_is_not_taken_for_written(tmp_path, monkeypatch):
    store, session = _session(tmp_path, messages=1)
    session.final_text = "never landed"
    real = store._write_session
    monkeypatch.setattr(store, "_write_session", lambda *args: (_ for _ in ()).throw(OSError("disk full")))
    with pytest.raises(OSError):
        store.commit_event(session, _event(session, "e1"))
    monkeypatch.setattr(store, "_write_session", real)
    # The journal of the failed commit is redone on the next access; a commit that follows must still write.
    store.commit_event(session, _event(session, "e2"))
    path = store.session_dir(session.session_id) / "session.json"
    assert json.loads(path.read_text(encoding="utf-8"))["final_text"] == "never landed"
    assert [event.event_id for event in store.events(session.session_id)] == ["e1", "e2"]


def test_save_does_not_rewrite_an_unchanged_snapshot(tmp_path):
    store, session = _session(tmp_path, messages=1)
    path = store.session_dir(session.session_id) / "session.json"
    stamp = path.stat().st_mtime_ns
    store.save(session)
    assert path.stat().st_mtime_ns == stamp
    session.final_text = "changed"
    store.save(session)
    assert json.loads(path.read_text(encoding="utf-8"))["final_text"] == "changed"
