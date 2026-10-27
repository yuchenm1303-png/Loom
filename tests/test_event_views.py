"""Views of a session log that skip what their reader never uses.

On a long session most of events.jsonl is model request payloads, and every thread
switch used to parse all of them to show a transcript or a context meter. The views
below read the same log; these tests pin that they say the same thing as a full read.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from app.agent_runtime import storage
from app.agent_runtime.contracts import AgentEvent, AgentEventKind as E
from app.agent_runtime.storage import FileAgentSessionStore

SESSION = "abcdef"
BODY = "REQUEST-BODY-SENTINEL"


def _event(index: int, kind: E, turn: str = "turn-1", **data) -> AgentEvent:
    return AgentEvent(f"event-{index}", SESSION, turn, kind, f"2026-10-08T00:00:{index % 60:02d}.000+00:00", data)


def _request(index: int, turn: str = "turn-1", filler: int = 20) -> AgentEvent:
    layout = {"sections": [{"role": "user", "name": "history", "estimated_tokens": n} for n in range(filler)], "note": BODY}
    return _event(index, E.MODEL_REQUESTED, turn, request_layout=layout, message_count=filler, profile_id="p")


def _log(store: FileAgentSessionStore) -> None:
    store.append_event(_event(0, E.TURN_STARTED, source="user"))
    store.append_event(_event(1, E.USER_MESSAGE, text="hello"))
    store.append_event(_request(2))
    store.append_event(_event(3, E.MODEL_RESPONSE, text="hi", usage={"total_tokens": 4}))
    store.append_event(_event(4, E.TOOL_REQUESTED, call_id="a", tool="read", arguments={"path": "x"}))
    store.append_event(_event(5, E.TOOL_COMPLETED, call_id="a", ok=True, content="done"))
    store.append_event(_request(6))
    store.append_event(_event(7, E.TURN_COMPLETED, text="finished"))


@pytest.fixture
def store(tmp_path) -> FileAgentSessionStore:
    store = FileAgentSessionStore(tmp_path)
    _log(store)
    return store


def _spy_on_json(monkeypatch) -> list[bytes]:
    parsed: list[bytes] = []
    real = json.loads

    def loads(value, *args, **kwargs):
        parsed.append(bytes(value) if isinstance(value, (bytes, bytearray)) else str(value).encode())
        return real(value, *args, **kwargs)

    monkeypatch.setattr(storage.json, "loads", loads)
    return parsed


def test_presentation_view_keeps_every_record_and_drops_only_request_bodies(store, tmp_path, monkeypatch):
    full = store.events(SESSION)
    parsed = _spy_on_json(monkeypatch)
    view = store.presentation_events(SESSION)

    assert not any(BODY.encode() in text for text in parsed), "the request body was parsed"
    assert len(view) == len(full)
    for kept, original in zip(view, full):
        assert (kept.event_id, kept.session_id, kept.turn_id, kept.kind, kept.created_at) == (
            original.event_id, original.session_id, original.turn_id, original.kind, original.created_at)
        assert kept.data == ({} if original.kind is E.MODEL_REQUESTED else original.data)
    # The control: an ordinary read of the same log does parse them.
    parsed.clear()
    FileAgentSessionStore(tmp_path).events(SESSION)
    assert any(BODY.encode() in text for text in parsed)


def test_records_that_do_not_look_like_a_header_are_parsed_in_full(tmp_path):
    store = FileAgentSessionStore(tmp_path)
    path = store.session_dir(SESSION) / "events.jsonl"
    path.parent.mkdir(parents=True)
    body = {"note": BODY}
    lines = [
        # data first: the kind cannot be known without reading on
        {"data": body, "event_id": "1", "session_id": SESSION, "turn_id": "t", "kind": "model_requested", "created_at": "c"},
        # kind present, but the id of the turn only follows the body
        {"event_id": "2", "session_id": SESSION, "kind": "model_requested", "created_at": "c", "data": body, "turn_id": "t"},
        # a field that merely mentions the kind
        {"event_id": "3", "session_id": SESSION, "turn_id": 'x","kind":"model_requested', "kind": "tool_started", "created_at": "c", "data": body},
    ]
    path.write_text("".join(json.dumps(line, separators=(",", ":")) + "\n" for line in lines), encoding="utf-8")

    view = store.presentation_events(SESSION)
    assert [event.data for event in view] == [body, body, body]
    assert [event.turn_id for event in view] == ["t", "t", 'x","kind":"model_requested']
    assert [event.kind for event in view] == [E.MODEL_REQUESTED, E.MODEL_REQUESTED, E.TOOL_STARTED]


def test_presentation_view_reparses_only_what_was_appended_and_never_shows_a_partial_record(store):
    assert len(store.presentation_events(SESSION)) == 8
    before = store._event_cache.metrics()["parsed_records"]
    assert len(store.presentation_events(SESSION)) == 8
    assert store._event_cache.metrics()["parsed_records"] == before

    store.append_event(_event(8, E.TURN_STARTED, "turn-2"))
    assert len(store.presentation_events(SESSION)) == 9
    assert store._event_cache.metrics()["parsed_records"] == before + 1

    # The header of a request is readable long before the line is whole.
    path = store.session_dir(SESSION) / "events.jsonl"
    whole = json.dumps({"event_id": "event-9", "session_id": SESSION, "turn_id": "turn-2", "kind": "model_requested",
                        "created_at": "c", "data": {"note": BODY}}, separators=(",", ":"))
    with path.open("ab") as handle:
        handle.write(whole[:-20].encode())
    assert len(store.presentation_events(SESSION)) == 9
    with path.open("ab") as handle:
        handle.write((whole[-20:] + "\n").encode())
    assert [event.event_id for event in store.presentation_events(SESSION)][-1] == "event-9"


def test_checkpoint_view_holds_only_checkpoints(store, monkeypatch):
    store.append_event(_event(8, E.CONTEXT_CHECKPOINTED, checkpoint_id="one", context_after_compaction={"used": 1}))
    store.append_event(_request(9))
    store.append_event(_event(10, E.CONTEXT_CHECKPOINTED, checkpoint_id="two"))
    parsed = _spy_on_json(monkeypatch)
    checkpoints = store.checkpoint_events(SESSION)

    assert [(event.event_id, event.data.get("checkpoint_id")) for event in checkpoints] == [("event-8", "one"), ("event-10", "two")]
    assert checkpoints[0].data["context_after_compaction"] == {"used": 1}
    assert len(parsed) == 2, "only the checkpoints themselves are parsed"
    store.append_event(_event(11, E.TOOL_STARTED))
    assert [event.event_id for event in store.checkpoint_events(SESSION)] == ["event-8", "event-10"]
    assert len(parsed) == 2


def _reference_state(events):
    checkpoints, newest = [], None
    for event in events:
        if event.kind is E.CONTEXT_CHECKPOINTED:
            checkpoints.append(event)
            newest = event
        elif event.kind is E.MODEL_REQUESTED:
            newest = event
    return tuple(checkpoints), newest


def _raw(event: AgentEvent) -> str:
    return json.dumps({"event_id": event.event_id, "session_id": event.session_id, "turn_id": event.turn_id,
                       "kind": event.kind.value, "created_at": event.created_at, "data": event.data}, separators=(",", ":"))


def _write(store, lines: list[str], tail: str = "") -> None:
    path = store.session_dir(SESSION) / "events.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(line + "\n" for line in lines) + tail, encoding="utf-8")


def _arrangements():
    start, user = _event(0, E.TURN_STARTED), _event(1, E.USER_MESSAGE, text="hi")
    request, later = _request(2), _request(5)
    checkpoint = _event(3, E.CONTEXT_CHECKPOINTED, checkpoint_id="c", replacement_estimated_tokens=7)
    tool = _event(4, E.TOOL_COMPLETED, call_id="a", content="x")
    huge = _event(6, E.TOOL_COMPLETED, call_id="b", content="y" * 600_000)
    yield "no requests", [start, user, tool], ""
    yield "only requests", [start, request, tool, later, tool], ""
    yield "checkpoint is newest", [start, request, checkpoint, tool], ""
    yield "request after checkpoint", [start, request, checkpoint, tool, later, tool], ""
    yield "two checkpoints", [start, checkpoint, request, _event(7, E.CONTEXT_CHECKPOINTED, checkpoint_id="d"), tool], ""
    yield "newest is far from the end", [start, request, later, huge, tool], ""
    yield "valid unterminated request", [start, request], _raw(later)
    yield "partial unterminated line", [start, request, tool], _raw(later)[:-15]


@pytest.mark.parametrize("name,events,tail", list(_arrangements()), ids=[name for name, *_ in _arrangements()])
def test_context_state_matches_the_scan_it_replaces(tmp_path, name, events, tail):
    store = FileAgentSessionStore(tmp_path)
    _write(store, [_raw(event) for event in events], tail)

    assert store.context_state(SESSION) == _reference_state(store.events(SESSION))
    # and again once the cached checkpoint view exists
    assert store.context_state(SESSION) == _reference_state(store.events(SESSION))


def test_context_state_of_a_session_without_a_log_is_empty(tmp_path):
    assert FileAgentSessionStore(tmp_path).context_state(SESSION) == ((), None)


def test_context_state_does_not_read_the_whole_log_to_find_a_recent_request(tmp_path, monkeypatch):
    store = FileAgentSessionStore(tmp_path)
    _write(store, [_raw(_event(index, E.TOOL_COMPLETED, call_id=str(index), content="z" * 2000)) for index in range(400)]
           + [_raw(_request(500)), _raw(_event(501, E.TOOL_STARTED))])
    parsed = _spy_on_json(monkeypatch)

    checkpoints, newest = store.context_state(SESSION)

    assert checkpoints == () and newest.event_id == "event-500"
    assert sum(len(text) for text in parsed) < 100_000, "the 800 KB of older tool output must not be parsed"


def test_context_state_waits_for_the_log_lock(tmp_path):
    """Reads share the commit lock, so a writer in the middle of a commit is never half-seen."""
    import threading

    from app.agent_runtime.journal import session_lock

    store = FileAgentSessionStore(tmp_path)
    _write(store, [_raw(_request(1))])
    finished = threading.Event()
    result = []

    def read():
        result.append(store.context_state(SESSION))
        finished.set()

    with session_lock(store.session_dir(SESSION)):
        worker = threading.Thread(target=read)
        worker.start()
        assert not finished.wait(0.3)
    assert finished.wait(3)
    worker.join()
    assert result[0][1].event_id == "event-1"


@pytest.mark.parametrize("name,events,tail", list(_arrangements()), ids=[name for name, *_ in _arrangements()])
def test_last_event_matches_a_reverse_scan_of_the_whole_log(tmp_path, name, events, tail):
    store = FileAgentSessionStore(tmp_path)
    _write(store, [_raw(event) for event in events], tail)
    full = store.events(SESSION)

    for kinds in ((E.MODEL_REQUESTED,), (E.CONTEXT_CHECKPOINTED,), (E.TOOL_COMPLETED,), (E.MODEL_REQUESTED, E.CONTEXT_CHECKPOINTED),
                  (E.TURN_COMPLETED,), ("tool_completed", "user_message")):
        wanted = {getattr(kind, "value", kind) for kind in kinds}
        expected = next((event for event in reversed(full) if event.kind.value in wanted), None)
        assert store.last_event(SESSION, kinds) == expected, kinds


def test_last_event_of_a_session_without_a_log_is_none(tmp_path):
    assert FileAgentSessionStore(tmp_path).last_event(SESSION, (E.TURN_COMPLETED,)) is None


def test_last_event_reads_the_end_of_the_log_not_all_of_it(tmp_path, monkeypatch):
    store = FileAgentSessionStore(tmp_path)
    _write(store, [_raw(_event(0, E.TURN_COMPLETED))]
           + [_raw(_event(index, E.TOOL_COMPLETED, call_id=str(index), content="z" * 3000)) for index in range(1, 300)])
    parsed = _spy_on_json(monkeypatch)

    event = store.last_event(SESSION, (E.TURN_COMPLETED,))

    assert event.event_id == "event-0"
    # Finding the only completed turn at the very start does need the whole file; asking for
    # the newest tool result must not.
    parsed.clear()
    assert store.last_event(SESSION, (E.TOOL_COMPLETED,)).event_id == "event-299"
    assert sum(len(text) for text in parsed) < 10_000


def _second_turn(store):
    """A second turn, so that narrowing by turn has something to leave out."""
    store.append_event(_event(8, E.TURN_STARTED, "turn-2", source="user"))
    store.append_event(_event(9, E.MODEL_REQUESTED, "turn-2", model="m"))
    store.append_event(_event(10, E.TOOL_STARTED, "turn-2", call_id="b", tool="read", effect="read_only", call_fingerprint="f", repeat_count=0))
    store.append_event(_event(11, E.MODEL_RESPONSE, "turn-2", text="again", usage={"total_tokens": 9}))
    store.append_event(_event(12, E.PLAN_UPDATED, "turn-2", plan=[{"step": "a", "status": "pending"}]))
    store.append_event(_event(13, E.TOOL_STARTED, "turn-2", call_id="c", tool="read", effect="read_only", call_fingerprint="g", repeat_count=1))
    store.append_event(_event(14, E.TURN_COMPLETED, "turn-2", text="done"))


@pytest.mark.parametrize("turn_id, kinds, last", [
    ("turn-2", None, None),
    (None, (E.TOOL_STARTED, E.PLAN_UPDATED), None),
    (None, None, 3),
    ("turn-2", (E.TOOL_STARTED,), None),
    ("turn-2", (E.TOOL_STARTED, E.MODEL_REQUESTED), 2),
    ("turn-1", ("model_requested", "model_response"), None),
    (None, (E.CONTEXT_CHECKPOINTED,), None),
    ("no-such-turn", None, None),
    ("", None, None),
    (None, None, 1000),
])
def test_a_selection_is_what_the_same_filters_give_on_the_whole_view(store, turn_id, kinds, last):
    _second_turn(store)
    full = store.context_events(SESSION)
    expected = [e for e in full
                if (turn_id is None or e.turn_id == turn_id) and (kinds is None or e.kind in {E(k) for k in kinds})]
    expected = expected[-last:] if last else expected
    chosen = store.context_select(SESSION, turn_id=turn_id, kinds=kinds, last=last)
    assert [e.event_id for e in chosen] == [e.event_id for e in expected]
    assert [(e.turn_id, e.kind, e.created_at, e.data) for e in chosen] == [(e.turn_id, e.kind, e.created_at, e.data) for e in expected]


def test_nothing_asked_for_gives_nothing(store):
    assert store.context_select(SESSION, last=0) == () and store.context_select(SESSION, kinds=()) == ()


def test_a_selection_builds_only_the_events_it_keeps(store, monkeypatch):
    _second_turn(store)
    store.context_events(SESSION)  # parse once, as a running session has
    built = []
    real = storage._build_event
    monkeypatch.setattr(storage, "_build_event", lambda record, shared: built.append(record["event_id"]) or real(record, shared))
    kept = store.context_select(SESSION, turn_id="turn-2", kinds=(E.TOOL_STARTED,))
    assert [e.event_id for e in kept] == ["event-10", "event-13"] and built == ["event-10", "event-13"]
    built.clear()
    store.context_select(SESSION, last=3)
    assert built == ["event-12", "event-13", "event-14"]


def test_a_selection_follows_the_log_as_it_grows(store):
    before = [event.event_id for event in store.context_select(SESSION, last=2)]
    store.append_event(_event(8, E.USER_MESSAGE, text="next"))
    store.append_event(_event(9, E.TURN_STARTED, source="user"))
    after = [event.event_id for event in store.context_select(SESSION, last=2)]
    assert before == ["event-6", "event-7"] and after == ["event-8", "event-9"]


def test_a_caller_cannot_change_what_a_selection_shares_with_the_cache(store):
    for event in store.context_select(SESSION, last=2):
        event.data["injected"] = True
    for event in store.context_select(SESSION, turn_id="turn-1", kinds=(E.TURN_COMPLETED,)):
        event.data["injected"] = True
    assert all("injected" not in event.data for event in store.context_events(SESSION))


def test_the_calibration_questions_read_the_tail_not_the_whole_log(store, monkeypatch):
    """Three accounting questions per model step are about the newest events only."""
    from types import SimpleNamespace
    from app.agent_runtime import context_budget

    monkeypatch.setattr(context_budget, "_CALIBRATION_EVENT_SCAN_LIMIT", 3)
    rt = SimpleNamespace(store=store)
    session = SimpleNamespace(session_id=SESSION, profile_id="p")
    monkeypatch.setattr(store, "context_events", lambda *args, **kwargs: pytest.fail("the whole view was built"))
    events = context_budget._calibration_events(rt, session)
    assert [event.event_id for event in events] == ["event-5", "event-6", "event-7"]


class _WithoutSelect:
    """A store that only has the older reads, which every narrowed read must still work on."""

    def __init__(self, backing):
        self.backing = backing

    def events(self, session_id, **kwargs):
        return self.backing.events(session_id, **kwargs)

    def context_events(self, session_id):
        return self.backing.context_events(session_id)


def _runtime_on(store):
    from types import SimpleNamespace
    from app.agent_runtime import AgentRuntime

    runtime = object.__new__(AgentRuntime)
    runtime.store = store
    runtime.development_verification = SimpleNamespace(context=lambda session: None)
    return runtime


def test_the_runtime_reads_the_same_events_with_and_without_a_store_that_narrows(store):
    _second_turn(store)
    narrowing, plain = _runtime_on(store), _runtime_on(_WithoutSelect(store))
    for kwargs in ({}, {"turn_id": "turn-2"}, {"kinds": (E.PLAN_UPDATED,)}, {"turn_id": "turn-2", "kinds": (E.TOOL_STARTED,)}):
        assert [e.event_id for e in narrowing._lifecycle_events(SESSION, **kwargs)] == [
            e.event_id for e in plain._lifecycle_events(SESSION, **kwargs)]
    assert [e.event_id for e in narrowing._lifecycle_events(SESSION, turn_id="turn-2", kinds=(E.TOOL_STARTED,))] == ["event-10", "event-13"]


def test_the_execution_context_is_the_same_with_and_without_a_store_that_narrows(store):
    from types import SimpleNamespace

    _second_turn(store)
    session = SimpleNamespace(session_id=SESSION, current_turn_id="turn-2", workspace_dir=".")
    with_select = _runtime_on(store)._execution_context(session)
    without = _runtime_on(_WithoutSelect(store))._execution_context(session)
    assert [m.content for m in with_select[0]] == [m.content for m in without[0]] and with_select[1] == without[1]
    # the second turn has a repeated read-only call and an active plan, so both halves of the answer are real
    assert {m.name for m in with_select[0]} == {"loom_execution_guidance", "loom_task_plan"}


def test_provider_usage_and_rollover_are_the_same_with_and_without_a_store_that_narrows(store):
    from types import SimpleNamespace
    from app.agent_runtime import context_budget

    session = SimpleNamespace(session_id=SESSION, profile_id="p", current_turn_id="turn-2")

    def answers(backing):
        rt = SimpleNamespace(store=backing)
        from app.agent_runtime.context_tools import pending_context_rollover
        select = getattr(backing, "context_select", None)
        rollover = pending_context_rollover(
            select(SESSION, kinds=("context_checkpointed", "context_rollover_requested")) if select else backing.context_events(SESSION), "turn-2")
        return context_budget._latest_provider_context_tokens(rt, session), rollover

    _second_turn(store)
    assert answers(store) == answers(_WithoutSelect(store)) == (9, False)
    store.append_event(_event(15, E.CONTEXT_ROLLOVER_REQUESTED, "turn-2"))
    assert answers(store) == answers(_WithoutSelect(store)) == (9, True)
    store.append_event(_event(16, E.CONTEXT_CHECKPOINTED, "turn-2"))
    assert answers(store) == answers(_WithoutSelect(store)) == (None, False)
    store.append_event(_event(17, E.MODEL_RESPONSE, "turn-2", usage={"input_tokens": 40, "output_tokens": 2}))
    assert answers(store) == answers(_WithoutSelect(store)) == (42, False)
    # A response the runtime rejected still tells what the provider counted.
    store.append_event(_event(18, E.MODEL_RESPONSE_REJECTED, "turn-2", reason="empty", usage={"total_tokens": 77}))
    assert answers(store) == answers(_WithoutSelect(store)) == (77, False)


def test_a_model_step_never_builds_the_whole_lifecycle_view(tmp_path):
    """Per step the runtime asks about one turn, some kinds, or the newest few: never for every event there is."""
    from app.agent_runtime import AgentRuntime, AgentTool, SandboxManager, SandboxPolicy, ToolRegistry, ToolResult
    from app.agent_runtime.contracts import PermissionMode
    from app.ai import ModelResponse, ToolCall

    class Counting(FileAgentSessionStore):
        def __init__(self, root):
            super().__init__(root)
            self.whole_view_reads, self.selections = 0, []

        def context_events(self, session_id):
            self.whole_view_reads += 1
            return super().context_events(session_id)

        def context_select(self, session_id, **kwargs):
            self.selections.append(kwargs)
            return super().context_select(session_id, **kwargs)

    class Scripted:
        def __init__(self, responses):
            self.responses = iter(responses)

        def execute_chat(self, profile, request):
            return next(self.responses)

    tool = AgentTool("echo", "echo text", {"type": "object"}, lambda context, arguments: ToolResult(ok=True, content="ok"))
    platform = Scripted([
        ModelResponse(tool_calls=(ToolCall("a", "echo", {"text": "one"}),)),
        ModelResponse(tool_calls=(ToolCall("b", "echo", {"text": "two"}),)),
        ModelResponse(text="done"),
    ])
    store = Counting(tmp_path)
    runtime = AgentRuntime(platform=platform, store=store, tools=ToolRegistry((tool,)),
                           sandbox_manager=SandboxManager(policy=SandboxPolicy.OFF))
    try:
        session = runtime.create_session("agent.fast", permission_mode=PermissionMode.FULL_ACCESS)
        runtime.start_turn(session.session_id, "use the tool twice")
    finally:
        runtime.close()
    # The browser runtime reads the log whole once, at the first model request of a turn, to find leases that were
    # left open; no later step of the turn does.
    assert store.whole_view_reads <= 1
    assert any(selection.get("turn_id") for selection in store.selections)
    assert any(selection.get("kinds") for selection in store.selections)
