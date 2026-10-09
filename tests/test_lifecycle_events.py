"""Per-call bookkeeping must read the lifecycle projection, never the whole transcript.

A tool call is preceded by a repeat check and a turn ends with a latency summary.
Both used to parse every tool result and model request body in the session log,
which is megabytes on a long conversation. They only need what the projection keeps.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.agent_runtime.contracts import AgentEvent, AgentEventKind as E, ToolEffect
from app.agent_runtime.execution_guidance import recent_read_only_repeat_count, recent_tool_repeat_count
from app.agent_runtime.runtime import AgentRuntime
from app.agent_runtime.storage import FileAgentSessionStore
from app.agent_runtime.turn_timing import turn_timing_metadata
from app.ai import ToolCall

SESSION = "abcdef"
BULK = "observation " * 4000


def _event(index: int, kind: E, turn: str = "turn-1", second: int | None = None, **data) -> AgentEvent:
    stamp = f"2026-10-08T00:00:{(index if second is None else second):02d}.000+00:00"
    return AgentEvent(f"event-{index}", SESSION, turn, kind, stamp, data)


def _history() -> list[AgentEvent]:
    return [
        _event(0, E.TURN_STARTED, "turn-0", text="earlier"),
        _event(1, E.TOOL_STARTED, "turn-0", effect="read_only", call_fingerprint="old", tool="read"),
        _event(2, E.TURN_COMPLETED, "turn-0", text="done"),
        _event(3, E.TURN_STARTED, text="now"),
        _event(4, E.MODEL_REQUESTED, request_layout={"sections": [{"role": "user", "n": index} for index in range(50)]},
               context_limits={"window": 1}, profile_id="p"),
        _event(5, E.MODEL_RESPONSE, usage={"total_tokens": 7}, text=BULK),
        _event(6, E.TOOL_REQUESTED, call_id="a", arguments={"path": BULK}),
        _event(7, E.TOOL_STARTED, call_id="a", effect="read_only", call_fingerprint="same", tool="read", repeat_count=0),
        _event(8, E.TOOL_COMPLETED, call_id="a", ok=True, content=BULK),
        _event(9, E.TOOL_STARTED, call_id="b", effect="read_only", call_fingerprint="same", tool="read", repeat_count=1),
        _event(10, E.TOOL_COMPLETED, call_id="b", ok=True, content=BULK),
        _event(11, E.TOOL_STARTED, call_id="c", effect="sensitive", call_fingerprint="run", tool="exec", repeat_count=0),
        _event(12, E.TOOL_STARTED, call_id="d", effect="sensitive", call_fingerprint="run", tool="exec", repeat_count=1),
        _event(13, E.MODEL_RESPONSE_REJECTED, reason="empty_response", usage={"total_tokens": 1}),
        _event(14, E.TOOL_STARTED, call_id="e", effect="read_only", call_fingerprint="same", tool="read", repeat_count=2),
    ]


@pytest.fixture
def store(tmp_path):
    store = FileAgentSessionStore(tmp_path)
    for item in _history():
        store.append_event(item)
    return store


def test_projection_keeps_everything_the_lifecycle_readers_use(store):
    full, projected = store.events(SESSION), store.context_events(SESSION)
    assert len(projected) == len(full)
    for fingerprint in ("same", "run", "old", "absent"):
        for turn in ("turn-0", "turn-1", "missing"):
            assert recent_read_only_repeat_count(projected, turn_id=turn, fingerprint=fingerprint) == (
                recent_read_only_repeat_count(full, turn_id=turn, fingerprint=fingerprint))
            assert recent_tool_repeat_count(projected, turn_id=turn, fingerprint=fingerprint) == (
                recent_tool_repeat_count(full, turn_id=turn, fingerprint=fingerprint))
    for kind in (E.MODEL_REQUESTED, E.TOOL_REQUESTED, E.TURN_COMPLETED, E.LIMIT_REACHED):
        for turn in ("turn-0", "turn-1"):
            assert turn_timing_metadata(projected, turn_id=turn, kind=kind, now="2026-10-08T00:00:59.000+00:00") == (
                turn_timing_metadata(full, turn_id=turn, kind=kind, now="2026-10-08T00:00:59.000+00:00"))
    # The counts are not trivially zero, so equality above compares real answers.
    assert recent_read_only_repeat_count(projected, turn_id="turn-1", fingerprint="same") == 1
    assert recent_tool_repeat_count(projected, turn_id="turn-1", fingerprint="run") == 2


class SpyStore:
    """Counts which read each hot path asks for, and answers from a real log."""

    def __init__(self, backing, *, projection=True):
        self.backing, self.calls = backing, []
        if projection:
            self.context_events = self._context_events

    def events(self, session_id, **kwargs):
        self.calls.append("events")
        return self.backing.events(session_id, **kwargs)

    def _context_events(self, session_id):
        self.calls.append("context_events")
        return self.backing.context_events(session_id)


def _runtime(store) -> AgentRuntime:
    runtime = object.__new__(AgentRuntime)
    runtime.store = store
    return runtime


def _prepared(effect: ToolEffect) -> SimpleNamespace:
    return SimpleNamespace(call=ToolCall(call_id="x", name="read", arguments={"path": "a"}), tool=SimpleNamespace(effect=effect))


@pytest.mark.parametrize("effect", [ToolEffect.READ_ONLY, ToolEffect.SENSITIVE])
def test_repeat_check_before_a_tool_call_reads_the_projection(store, effect):
    spy = SpyStore(store)
    session = SimpleNamespace(session_id=SESSION, current_turn_id="turn-1")
    fingerprint, repeats = _runtime(spy)._tool_repeat_metadata(session, _prepared(effect))
    assert spy.calls == ["context_events"]
    assert len(fingerprint) == 64 and repeats == 0


def test_repeat_check_still_works_on_a_store_without_the_projection(store):
    spy = SpyStore(store, projection=False)
    session = SimpleNamespace(session_id=SESSION, current_turn_id="turn-1")
    _runtime(spy)._tool_repeat_metadata(session, _prepared(ToolEffect.READ_ONLY))
    assert spy.calls == ["events"]


def test_terminal_turn_summary_reads_the_projection(store):
    spy = SpyStore(store)
    runtime = _runtime(spy)
    recorded = []
    runtime._project_event_payload = lambda session, kind, data: dict(data)
    runtime._record = lambda session, kind, *, data: recorded.append((kind, dict(data))) or SimpleNamespace(kind=kind)
    runtime._on_turn_finished = lambda session, event: None
    session = SimpleNamespace(session_id=SESSION, current_turn_id="turn-1")
    runtime._emit_event(session, E.TURN_COMPLETED, data={"text": "ok"})
    assert spy.calls == ["context_events"]
    assert recorded[0][1]["first_model_request_latency_ms"] >= 0 and recorded[0][1]["text"] == "ok"
