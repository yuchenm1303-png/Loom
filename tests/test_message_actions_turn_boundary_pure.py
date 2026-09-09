from __future__ import annotations

from dataclasses import dataclass

from app.desktop.message_actions_turn_boundary import _terminal_assistant_keys


@dataclass
class Entry:
    key: str
    kind: str


def test_active_tail_has_no_terminal_footer():
    entries = [
        Entry("u1", "user"),
        Entry("a1", "assistant"),
        Entry("tool1", "tool"),
    ]
    assert _terminal_assistant_keys(entries, turn_active=True) == set()


def test_completed_turn_only_marks_last_assistant():
    entries = [
        Entry("u1", "user"),
        Entry("a1", "assistant"),
        Entry("tool1", "tool"),
        Entry("a2", "assistant"),
    ]
    assert _terminal_assistant_keys(entries, turn_active=False) == {"a2"}


def test_previous_completed_turn_remains_terminal_during_next_active_turn():
    entries = [
        Entry("u1", "user"),
        Entry("a1", "assistant"),
        Entry("u2", "user"),
        Entry("a2", "assistant"),
    ]
    assert _terminal_assistant_keys(entries, turn_active=True) == {"a1"}
