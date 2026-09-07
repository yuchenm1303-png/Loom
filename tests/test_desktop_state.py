from __future__ import annotations

from app.desktop.state import OPTIMISTIC_USER_KEY, ThreadState


def _snapshot(**overrides):
    payload = {
        "thread": {
            "id": "thread-1",
            "title": "Inspect project",
            "workspace": "C:/work",
            "permissionMode": "workspace",
            "status": "completed",
            "currentTurnId": "turn-1",
            "usage": {"totalTokens": 7},
        },
        "messages": [],
        "turns": [],
        "events": [],
        "pendingApproval": None,
    }
    payload.update(overrides)
    return payload


def _turn(*items):
    return [{"id": "turn-1", "status": "completed", "items": list(items)}]


def test_transcript_follows_turn_item_order():
    state = ThreadState()
    assert state.apply_snapshot(
        _snapshot(
            turns=_turn(
                {"id": "user:1", "type": "user_message", "status": "completed", "text": "hi"},
                {
                    "id": "tool:1",
                    "type": "tool_call",
                    "status": "completed",
                    "toolName": "read_file",
                },
                {
                    "id": "assistant:1",
                    "type": "assistant_message",
                    "status": "completed",
                    "text": "done",
                },
            )
        )
    )
    assert [(entry.kind, entry.key) for entry in state.entries()] == [
        ("user", "user:1"),
        ("tool", "tool:1"),
        ("assistant", "assistant:1"),
    ]


def test_approval_items_never_become_transcript_rows():
    state = ThreadState()
    state.apply_snapshot(
        _snapshot(
            turns=_turn(
                {"id": "approval:1", "type": "approval", "status": "approved"},
                {"id": "tool:1", "type": "tool_call", "status": "completed", "toolName": "run"},
            )
        )
    )
    assert [entry.kind for entry in state.entries()] == ["tool"]


def test_plain_messages_render_when_turn_items_carry_no_conversation():
    state = ThreadState()
    state.apply_snapshot(
        _snapshot(
            messages=[
                {"role": "user", "content": "Inspect the repository"},
                {"role": "assistant", "content": "The repository is ready."},
            ],
            turns=_turn(
                {"id": "process:1", "type": "process", "status": "completed", "argv": ["python"]}
            ),
        )
    )
    kinds = [entry.kind for entry in state.entries()]
    assert kinds == ["user", "assistant", "process"]


def test_turn_items_win_over_duplicate_plain_messages():
    state = ThreadState()
    state.apply_snapshot(
        _snapshot(
            messages=[{"role": "user", "content": "hi"}],
            turns=_turn(
                {"id": "user:1", "type": "user_message", "status": "completed", "text": "hi"}
            ),
        )
    )
    assert [entry.key for entry in state.entries()] == ["user:1"]


def test_streamed_text_accumulates_and_survives_an_empty_completion():
    state = ThreadState()
    state.apply_snapshot(_snapshot())
    state.append_text_delta("assistant:live", "Live ")
    state.append_text_delta("assistant:live", "chunk")

    entry = state.entries()[-1]
    assert entry.text == "Live chunk"
    assert entry.streaming is True

    state.upsert_item(
        {"id": "assistant:live", "type": "assistant_message", "status": "completed"},
        streaming=False,
    )
    entry = state.entries()[-1]
    assert entry.text == "Live chunk"
    assert entry.streaming is False


def test_snapshot_refresh_keeps_an_uncommitted_streaming_item():
    state = ThreadState()
    state.apply_snapshot(_snapshot())
    state.append_text_delta("assistant:live", "partial")

    state.apply_snapshot(_snapshot())

    assert [entry.key for entry in state.entries()] == ["assistant:live"]
    assert state.entries()[0].text == "partial"


def test_optimistic_prompt_is_replaced_by_the_durable_user_item():
    state = ThreadState()
    state.apply_snapshot(_snapshot())
    state.set_optimistic_user("run the tests")
    assert state.entries()[-1].key == OPTIMISTIC_USER_KEY

    state.upsert_item(
        {"id": "user:1", "type": "user_message", "status": "completed", "text": "run the tests"}
    )
    assert [entry.key for entry in state.entries()] == ["user:1"]


def test_process_output_appends_to_the_matching_item():
    state = ThreadState()
    state.apply_snapshot(
        _snapshot(
            turns=_turn(
                {"id": "process:1", "type": "process", "status": "running", "argv": ["pytest"]}
            )
        )
    )
    state.append_process_output("process:1", stdout="line one\n")
    state.append_process_output("process:1", stdout="line two\n", stderr="warn\n")

    item = state.items_of_type("process")[0]
    assert item["stdout"] == "line one\nline two\n"
    assert item["stderr"] == "warn\n"


def test_switching_threads_drops_the_previous_live_state():
    state = ThreadState()
    state.apply_snapshot(_snapshot())
    state.append_text_delta("assistant:live", "partial")

    other = _snapshot()
    other["thread"] = dict(other["thread"], id="thread-2")
    state.apply_snapshot(other)

    assert state.thread_id == "thread-2"
    assert state.entries() == []


def test_signature_changes_only_when_rendered_content_changes():
    state = ThreadState()
    state.apply_snapshot(
        _snapshot(
            turns=_turn(
                {"id": "tool:1", "type": "tool_call", "status": "running", "toolName": "run"}
            )
        )
    )
    before = state.entries()[0].signature()

    state.upsert_item({"id": "tool:1", "updatedAt": "later"})
    assert state.entries()[0].signature() == before

    state.upsert_item({"id": "tool:1", "status": "completed"})
    assert state.entries()[0].signature() != before


def test_pending_approval_is_normalized():
    state = ThreadState()
    state.apply_snapshot(_snapshot())
    approval = state.set_pending_approval(
        {"callId": "call-1", "toolName": "run", "extra": "ignored"}
    )
    assert approval == {
        "callId": "call-1",
        "toolName": "run",
        "arguments": {},
        "effect": None,
        "reason": None,
    }
    assert state.set_pending_approval({"toolName": "run"}) is None
