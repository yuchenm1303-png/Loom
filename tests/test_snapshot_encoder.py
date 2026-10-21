"""A durable event commits a snapshot of the session; on a long session that was most of the time a step spent.

The snapshot is encoded from what cannot have changed (messages are frozen, old context frames are final) and
is not written again when it says what the file already says.
"""
from __future__ import annotations

import json
import random
import uuid
from dataclasses import replace

import pytest

from app.agent_runtime import (
    AgentEvent, AgentEventKind, AgentRuntime, AgentSession, AgentTool, FileAgentSessionStore, SandboxManager, SandboxPolicy,
    ToolRegistry, ToolResult,
)
from app.agent_runtime import storage
from app.agent_runtime.contracts import PermissionMode
from app.agent_runtime.storage import _SnapshotEncoder, session_to_dict, utc_now
from app.ai import AIMessage, ImagePart, MessageRole, ModelResponse, TextPart, ToolCall


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


def _frame(index):
    return {"step_id": f"step-{index}", "after_message_count": index, "history_anchors": [],
            "messages": [{"role": "user", "name": "loom_note", "content": f"note {index} ✓"}], "metadata": {}}


def _encoded_values(monkeypatch):
    """Everything handed to the JSON encoder from now on."""
    seen = []
    real = storage._encode
    monkeypatch.setattr(storage, "_encode", lambda value: seen.append(value) or real(value))
    return seen


def test_the_encoded_snapshot_is_the_session_document(tmp_path):
    _, session = _session(tmp_path, messages=3)
    session.messages.append(AIMessage(MessageRole.USER, (TextPart("look"), ImagePart("data:image/png;base64,AAAA"))))
    session.pending_bindings = {"call-1": {"effect": "read_only"}}
    data, _ = _SnapshotEncoder().encode(session)
    assert json.loads(data) == session_to_dict(session)
    assert b"\n" not in data  # compact: the C encoder, and nothing that has to be re-indented


def test_only_what_was_added_is_encoded_again(tmp_path, monkeypatch):
    _, session = _session(tmp_path, messages=12)
    encoder = _SnapshotEncoder()
    encoder.encode(session)
    encoded = _encoded_values(monkeypatch)
    encoder.encode(session)
    # The newest messages that carry tool calls and the one frame: the newest ones are never taken as final.
    calls = [record for record in encoded if "role" in record]
    assert len(calls) == storage._CALL_MESSAGES_REENCODED and all(record["tool_calls"] for record in calls)
    assert [record["tool_calls"][0]["call_id"] for record in calls] == [f"call-{n}" for n in range(12 - len(calls), 12)]
    assert len([record for record in encoded if "step_id" in record]) == 1 and len(encoded) == len(calls) + 1
    session.messages.append(AIMessage(MessageRole.USER, "one more"))
    encoded.clear()
    data, _ = encoder.encode(session)
    assert len(encoded) == len(calls) + 2 and json.loads(data) == session_to_dict(session)


def test_old_context_frames_are_not_encoded_again(tmp_path, monkeypatch):
    _, session = _session(tmp_path, messages=1)
    session.request_context_frames.extend(_frame(index) for index in range(2, 12))
    encoder = _SnapshotEncoder()
    encoder.encode(session)
    encoded = _encoded_values(monkeypatch)
    encoder.encode(session)
    frames = [record for record in encoded if "step_id" in record]
    assert [frame["step_id"] for frame in frames] == ["step-9", "step-10", "step-11"]
    session.request_context_frames.append(_frame(12))
    encoded.clear()
    data, _ = encoder.encode(session)
    # The three newest frames of the last snapshot may have been edited since; the new one is encoded for the first time.
    assert [record["step_id"] for record in encoded if "step_id" in record] == ["step-9", "step-10", "step-11", "step-12"]
    assert json.loads(data) == session_to_dict(session)


def test_a_tool_call_edited_in_place_is_written_as_edited(tmp_path):
    """A tool handler is handed the arguments dict of its call and may edit it; a cached encoding would keep the old."""
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


def test_messages_that_left_the_session_leave_the_encoder(tmp_path):
    _, session = _session(tmp_path, messages=4)
    encoder = _SnapshotEncoder()
    encoder.encode(session)
    assert len(encoder._messages) == 16 == len(encoder._message_parts)
    session.messages = session.messages[:4]
    session.request_context_frames = []
    data, _ = encoder.encode(session)
    assert len(encoder._messages) == 4 == len(encoder._message_parts) and encoder._frames == []
    assert json.loads(data) == session_to_dict(session)


def test_a_replaced_message_is_encoded_as_replaced(tmp_path):
    _, session = _session(tmp_path, messages=3)
    encoder = _SnapshotEncoder()
    encoder.encode(session)
    # Compaction swaps the head of the history for a summary and keeps the tail.
    session.messages[:6] = [AIMessage(MessageRole.USER, "summary of what came before")]
    session.request_context_frames[:] = [_frame(1)]
    data, _ = encoder.encode(session)
    assert json.loads(data) == session_to_dict(session)
    # An equal message in its place gives the encoding it had.
    session.messages[1] = replace(session.messages[1])
    data, _ = encoder.encode(session)
    assert json.loads(data) == session_to_dict(session)


@pytest.mark.parametrize("seed", range(6))
def test_the_encoding_follows_every_change_of_a_growing_session(tmp_path, seed):
    """Whatever happens to a session between two snapshots, the bytes are the document the session has."""
    rng = random.Random(seed)
    _, session = _session(tmp_path, messages=2)
    encoder = _SnapshotEncoder()
    counter = 0

    def tool_message():
        # A handler edits the arguments of the call it is running, which is among the newest.
        return [i for i, message in enumerate(session.messages) if message.tool_calls][-storage._CALL_MESSAGES_REENCODED:]

    for _ in range(250):
        counter += 1
        choice = rng.randrange(18)
        if choice == 0:
            session.messages.append(AIMessage(MessageRole.USER, f"user {counter} é\n\"q\""))
        elif choice == 1:
            session.messages.append(AIMessage(MessageRole.ASSISTANT, f"text {counter}", reasoning="r", phase="commentary"))
        elif choice == 2:
            call = ToolCall(f"c{counter}", "exec", {"argv": ["x", str(counter)], "nested": {"a": [1, 2]}})
            session.messages.append(AIMessage(MessageRole.ASSISTANT, "", tool_calls=(call,)))
            session.messages.append(AIMessage(MessageRole.TOOL, f"out {counter}", tool_call_id=call.call_id))
        elif choice == 3:
            session.request_context_frames.append(_frame(counter))
        elif choice == 4 and session.request_context_frames:
            session.request_context_frames[-1]["metadata"][f"k{counter}"] = {"ms": counter}
        elif choice == 5 and session.request_context_frames:
            frame = rng.choice(session.request_context_frames[-3:])
            frame["messages"].append({"role": "user", "name": "x", "content": f"late {counter}"})
        elif choice == 6 and tool_message():
            message = session.messages[rng.choice(tool_message())]
            message.tool_calls[0].arguments["nested"] = {"edited": counter}
        elif choice == 7 and len(session.messages) > 4:
            del session.messages[rng.randrange(0, len(session.messages) - 2)]
        elif choice == 8 and session.messages:
            session.messages.pop()
        elif choice == 9 and len(session.messages) > 6:
            session.messages[:] = [AIMessage(MessageRole.USER, f"summary {counter}")] + session.messages[-4:]
            session.request_context_frames[:] = [_frame(counter)]
        elif choice == 10 and session.messages:
            index = rng.randrange(len(session.messages))
            session.messages[index] = replace(session.messages[index])
        elif choice == 11:
            session.final_text, session.model_steps = f"final {counter}", counter
        elif choice == 12:
            session.usage = replace(session.usage, input_tokens=counter)
        elif choice == 13 and session.messages:
            session.messages[rng.randrange(len(session.messages))] = AIMessage(MessageRole.USER, f"changed {counter}")
        elif choice == 14:
            session.messages.insert(rng.randrange(len(session.messages) + 1), AIMessage(MessageRole.USER, f"inserted {counter}"))
        elif choice == 15 and session.request_context_frames:
            session.request_context_frames[:] = [_frame(counter + n) for n in range(len(session.request_context_frames))]
        elif choice == 16 and len(session.request_context_frames) > 4:
            session.request_context_frames[rng.randrange(len(session.request_context_frames) - 3)] = _frame(counter)
        else:
            session.pending_bindings = {f"b{counter}": "x"}
        data, _ = encoder.encode(session)
        assert json.loads(data) == session_to_dict(session), f"step {counter}, choice {choice}"


def test_the_key_moves_with_the_state_and_not_with_updated_at(tmp_path):
    _, session = _session(tmp_path, messages=2)
    encoder = _SnapshotEncoder()
    _, first = encoder.encode(session)
    session.updated_at = "2099-01-01T00:00:00+00:00"
    _, same = encoder.encode(session)
    assert same == first
    for change in (
        lambda: setattr(session, "final_text", "different"),
        lambda: session.messages.append(AIMessage(MessageRole.USER, "more")),
        lambda: session.request_context_frames[-1]["metadata"].update(done=True),
        lambda: session.messages[1].tool_calls[0].arguments.update(argv=["changed"]),
        lambda: setattr(session, "pending_step_id", "step-7"),
    ):
        _, before = encoder.encode(session)
        change()
        _, after = encoder.encode(session)
        assert after != before


def test_a_snapshot_that_contains_the_placeholder_is_still_encoded_correctly(tmp_path):
    _, session = _session(tmp_path, messages=1)
    # Values equal to the placeholders, in fields of the document that are not the lists they stand in for.
    session.model_selection = storage._MESSAGES_TOKEN
    session.active_skills = {storage._FRAMES_TOKEN: "x"}
    data, _ = _SnapshotEncoder().encode(session)
    assert json.loads(data) == session_to_dict(session)


class _CheckingStore(FileAgentSessionStore):
    """Compares the snapshot on disk with the session after every commit and save."""

    def __init__(self, root):
        super().__init__(root)
        self.checks, self.mismatches = 0, []

    def _check(self, session):
        on_disk = json.loads((self.session_dir(session.session_id) / "session.json").read_text(encoding="utf-8"))
        expected = session_to_dict(session)
        on_disk.pop("updated_at"), expected.pop("updated_at")
        self.checks += 1
        if on_disk != expected:
            self.mismatches.append(sorted(key for key in expected if on_disk.get(key) != expected[key]))

    def commit_event(self, session, event):
        super().commit_event(session, event)
        self._check(session)

    def save(self, session):
        super().save(session)
        self._check(session)


def test_every_commit_of_a_running_turn_leaves_the_snapshot_the_session_has(tmp_path):
    """The real runtime, several steps with tools: the file is the session after each of the dozens of commits."""
    class Scripted:
        def __init__(self, responses):
            self.responses = iter(responses)

        def execute_chat(self, profile, request):
            return next(self.responses)

    def echo(context, arguments):
        arguments["seen"] = True  # a handler is handed the dict of the call and may edit it
        return ToolResult(ok=True, content="echoed " + str(arguments.get("text")))

    tool = AgentTool("echo", "echo text", {"type": "object"}, echo)
    platform = Scripted([
        ModelResponse(tool_calls=(ToolCall("a", "echo", {"text": "one"}),)),
        ModelResponse(tool_calls=(ToolCall("b", "echo", {"text": "two"}), ToolCall("c", "echo", {"text": "three"}))),
        ModelResponse(tool_calls=(ToolCall("d", "echo", {"text": "four"}),)),
        ModelResponse(text="all done"),
        ModelResponse(text="and the second turn"),
    ])
    store = _CheckingStore(tmp_path)
    runtime = AgentRuntime(platform=platform, store=store, tools=ToolRegistry((tool,)),
                           sandbox_manager=SandboxManager(policy=SandboxPolicy.OFF))
    try:
        session = runtime.create_session("agent.fast", permission_mode=PermissionMode.FULL_ACCESS)
        runtime.start_turn(session.session_id, "use the tool a few times")
        runtime.start_turn(session.session_id, "and once more")
    finally:
        runtime.close()
    assert store.checks > 25
    assert store.mismatches == []
    final = store.load(session.session_id)
    assert any(call.arguments.get("seen") for message in final.messages for call in message.tool_calls)
    assert len(final.request_context_frames) >= 5


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
