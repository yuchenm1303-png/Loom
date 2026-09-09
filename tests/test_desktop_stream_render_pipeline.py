from __future__ import annotations

from dataclasses import dataclass

import pytest

pytest.importorskip("PySide6")

from app.desktop.stream_render_pipeline import (
    _entries_for_keys,
    _schedule_stream_frame,
    _update_existing_entries,
)


@dataclass
class _Entry:
    key: str
    value: str

    def signature(self):
        return (self.value,)


class _View:
    def __init__(self):
        self._widgets = {"assistant:1": object()}
        self._signatures = {"assistant:1": ("old",)}
        self.applied: list[tuple[object, _Entry]] = []

    def _apply(self, widget, entry):
        self.applied.append((widget, entry))


class _State:
    def __init__(self, entries):
        self._entries = entries

    def entries(self):
        return list(self._entries)


class _Timer:
    def __init__(self):
        self.active = False
        self.starts = 0

    def isActive(self):  # noqa: N802 - Qt-shaped test double
        return self.active

    def start(self):
        self.active = True
        self.starts += 1


class _Window:
    def __init__(self):
        self._stream_dirty_keys: set[str] = set()
        self._stream_terminal_dirty = False
        self._stream_frame_timer = _Timer()


def test_content_only_lane_updates_existing_widget_without_reconcile():
    view = _View()
    entry = _Entry("assistant:1", "new")

    assert _update_existing_entries(view, [entry]) is True
    assert [item.key for _widget, item in view.applied] == ["assistant:1"]
    assert view._signatures["assistant:1"] == ("new",)
    # The content-only lane owns no order/layout state at all.
    assert not hasattr(view, "_order")


def test_content_only_lane_refuses_missing_widget_before_partial_apply():
    view = _View()
    existing = _Entry("assistant:1", "new")
    missing = _Entry("assistant:2", "new")

    assert _update_existing_entries(view, [existing, missing]) is False
    assert view.applied == []
    assert view._signatures["assistant:1"] == ("old",)


def test_stream_scheduler_coalesces_many_deltas_into_one_pending_frame():
    window = _Window()

    _schedule_stream_frame(window, "assistant:1")
    _schedule_stream_frame(window, "assistant:1")
    _schedule_stream_frame(window, "process:1", terminal=True)

    assert window._stream_dirty_keys == {"assistant:1", "process:1"}
    assert window._stream_terminal_dirty is True
    assert window._stream_frame_timer.starts == 1


def test_dirty_entry_selection_preserves_transcript_order():
    entries = [
        _Entry("assistant:1", "a"),
        _Entry("process:1", "p"),
        _Entry("assistant:2", "b"),
    ]
    state = _State(entries)

    selected = _entries_for_keys(state, {"assistant:2", "process:1"})

    assert [entry.key for entry in selected] == ["process:1", "assistant:2"]
