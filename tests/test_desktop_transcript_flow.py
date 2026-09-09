from __future__ import annotations

from dataclasses import dataclass, field

import pytest

pytest.importorskip("PySide6")

from app.desktop.transcript_flow import _semantic_entries


@dataclass
class Entry:
    key: str
    kind: str
    streaming: bool = False
    item: dict = field(default_factory=dict)


def _keys(entries):
    return [entry.key for entry in entries]


def test_completed_turn_places_trailing_task_flow_before_final_reply():
    entries = [
        Entry("u1", "user"),
        Entry("a1", "assistant"),
        Entry("p1", "process"),
        Entry("t1", "tool"),
        Entry("d1", "diff"),
    ]

    assert _keys(_semantic_entries(entries, turn_active=False)) == [
        "u1",
        "p1",
        "t1",
        "d1",
        "a1",
    ]


def test_intermediate_narration_stays_interleaved_and_only_last_reply_moves():
    entries = [
        Entry("u1", "user"),
        Entry("a1", "assistant"),
        Entry("p1", "process"),
        Entry("a2", "assistant"),
        Entry("p2", "process"),
        Entry("d1", "diff"),
    ]

    assert _keys(_semantic_entries(entries, turn_active=False)) == [
        "u1",
        "a1",
        "p1",
        "p2",
        "d1",
        "a2",
    ]


def test_active_turn_keeps_literal_live_order():
    entries = [
        Entry("u1", "user"),
        Entry("a1", "assistant"),
        Entry("p1", "process"),
    ]

    assert _keys(_semantic_entries(entries, turn_active=True)) == ["u1", "a1", "p1"]


def test_error_turn_is_not_editorially_reordered():
    entries = [
        Entry("u1", "user"),
        Entry("a1", "assistant"),
        Entry("p1", "process"),
        Entry("e1", "error"),
    ]

    assert _keys(_semantic_entries(entries, turn_active=False)) == [
        "u1",
        "a1",
        "p1",
        "e1",
    ]


def test_previous_completed_turn_is_fixed_while_new_turn_is_active():
    entries = [
        Entry("u1", "user"),
        Entry("a1", "assistant"),
        Entry("p1", "process"),
        Entry("u2", "user"),
        Entry("a2", "assistant"),
        Entry("p2", "process"),
    ]

    assert _keys(_semantic_entries(entries, turn_active=True)) == [
        "u1",
        "p1",
        "a1",
        "u2",
        "a2",
        "p2",
    ]
