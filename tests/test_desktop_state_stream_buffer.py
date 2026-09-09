from __future__ import annotations

from app.desktop.state import ThreadState


def _snapshot(*items):
    return {
        "thread": {
            "id": "thread-1",
            "title": "stream",
            "workspace": "C:/work",
            "status": "running",
            "usage": {"totalTokens": 0},
        },
        "turns": [{"id": "turn-1", "items": list(items)}],
        "messages": [],
        "events": [],
        "pendingApproval": None,
    }


def test_text_fragments_materialize_once_at_entry_read_boundary():
    state = ThreadState()
    state.apply_snapshot(_snapshot())

    state.append_text_delta("assistant:1", "hello")
    state.append_text_delta("assistant:1", " world")

    # Provider arrival stays O(1): the canonical string is not rebuilt per chunk.
    assert state._items["assistant:1"]["text"] == ""
    assert state._stream_fragments[("assistant:1", "text")] == ["hello", " world"]

    assert state.entries()[0].text == "hello world"
    assert state._items["assistant:1"]["text"] == "hello world"
    assert state._stream_fragments == {}

    # Reading again does not duplicate already-materialised fragments.
    assert state.entries()[0].text == "hello world"


def test_completion_flushes_buffer_before_empty_completion_frame_merges():
    state = ThreadState()
    state.apply_snapshot(_snapshot())
    state.append_text_delta("assistant:1", "final answer")

    state.upsert_item(
        {
            "id": "assistant:1",
            "type": "assistant_message",
            "status": "completed",
            "text": "",
        },
        streaming=False,
    )

    entry = state.entries()[0]
    assert entry.text == "final answer"
    assert entry.streaming is False
    assert state._stream_fragments == {}


def test_process_output_materializes_at_runtime_read_boundary():
    state = ThreadState()
    state.apply_snapshot(_snapshot())

    state.append_process_output("process:1", stdout="one\n")
    state.append_process_output("process:1", stdout="two\n", stderr="warn\n")

    assert "stdout" not in state._items["process:1"]
    process = state.items_of_type("process")[0]
    assert process["stdout"] == "one\ntwo\n"
    assert process["stderr"] == "warn\n"
    assert state._stream_fragments == {}


def test_snapshot_preserves_uncommitted_buffered_live_item_bytes():
    state = ThreadState()
    state.apply_snapshot(
        _snapshot(
            {
                "id": "assistant:first",
                "type": "assistant_message",
                "status": "completed",
                "text": "before",
                "createdAt": "2026-09-09T08:00:00+00:00",
            }
        )
    )
    state.append_text_delta("assistant:live", "partial")

    # Durable storage has not committed the live item yet. Snapshot replacement
    # must flush and preserve it instead of dropping the pending fragment list.
    state.apply_snapshot(
        _snapshot(
            {
                "id": "assistant:first",
                "type": "assistant_message",
                "status": "completed",
                "text": "before",
                "createdAt": "2026-09-09T08:00:00+00:00",
            }
        )
    )

    assert [(entry.key, entry.text) for entry in state.entries()] == [
        ("assistant:first", "before"),
        ("assistant:live", "partial"),
    ]
