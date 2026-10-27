"""Startup work scales with selected state, not the size of the conversation log."""
import json
from types import SimpleNamespace
import pytest

from app.agent_runtime.contracts import AgentEvent, AgentEventKind as E
from app.agent_runtime.storage import FileAgentSessionStore
from app.ai import ModelResponse
from test_development_feedback import make_runtime


def test_plain_question_does_not_scan_workspace_or_old_check_history(tmp_path, monkeypatch):
    from test_automatic_verification import Responses
    import app.agent_runtime.development_verification as verification
    runtime, session, _ = make_runtime(tmp_path, Responses([ModelResponse(text="Hello")]))
    def forbidden(*args, **kwargs):
        raise AssertionError("an ordinary answer must not scan workspace files")
    monkeypatch.setattr(verification, "git_state", forbidden)
    monkeypatch.setattr(verification, "subject_snapshot", forbidden)
    try:
        assert runtime.start_turn(session.session_id, "Hello").final_text == "Hello"
    finally:
        runtime.close()


def test_selected_context_read_does_not_decode_unselected_event_bodies(tmp_path, monkeypatch):
    store = FileAgentSessionStore(tmp_path / "sessions")
    for i in range(40):
        store.append_event(AgentEvent(str(i), "abcdef", f"turn-{i // 4}", E.TOOL_COMPLETED,
            "2026-10-27T00:00:00+00:00", {"content": "large tool body" * 1000}))
    for i in range(3):
        store.append_event(AgentEvent(f"check-{i}", "abcdef", "current", E.CHECK_RECORDED,
            "2026-10-27T00:00:00+00:00", {"case_id": str(i), "verdict": "passed"}))
    import app.agent_runtime.storage as storage
    original = storage.json.loads
    decoded = []
    def tracked(raw, *args, **kwargs):
        if isinstance(raw, bytes) and b'"event_id"' in raw:
            decoded.append(raw)
        return original(raw, *args, **kwargs)
    monkeypatch.setattr(storage.json, "loads", tracked)
    result = store.context_select("abcdef", kinds=(E.CHECK_RECORDED,), last=1)
    assert [e.event_id for e in result] == ["check-2"]
    assert len(decoded) == 1
    assert b"large tool body" not in decoded[0]
    # Selection has exactly the projection semantics of a whole-history read.
    monkeypatch.setattr(storage.json, "loads", original)
    expected = tuple(e for e in store.context_events("abcdef") if e.kind is E.CHECK_RECORDED)
    assert store.context_select("abcdef", turn_id="current", kinds=(E.CHECK_RECORDED,)) == expected
    assert store.context_select("abcdef", turn_id="missing") == ()
    store.append_event(AgentEvent("standalone", "abcdef", "", E.CONTEXT_SUMMARY_PREPARED,
        "2026-10-27T00:00:01+00:00", {"usage": {}}))
    assert [e.event_id for e in store.context_select("abcdef", turn_id="")] == ["standalone"]
    # Incremental growth and external replacement both invalidate correctly.
    store.append_event(AgentEvent("new", "abcdef", "current", E.CHECK_RECORDED,
        "2026-10-27T00:00:01+00:00", {"case_id": "new"}))
    assert store.context_select("abcdef", kinds=(E.CHECK_RECORDED,), last=1)[0].event_id == "new"
    path = store.session_dir("abcdef") / "events.jsonl"
    path.write_text(json.dumps({"event_id": "replacement", "session_id": "abcdef", "turn_id": "new",
        "kind": "check_recorded", "created_at": "2026-10-27T00:00:02+00:00", "data": {}}) + "\n")
    assert store.context_select("abcdef", last=1)[0].event_id == "replacement"


def test_preparation_phase_is_committed_before_model_request(tmp_path):
    from test_automatic_verification import Responses
    runtime, session, _ = make_runtime(tmp_path, Responses([ModelResponse(text="Hello")]))
    try:
        runtime.start_turn(session.session_id, "Hello")
        events = runtime.store.events(session.session_id)
        phase = next(i for i, e in enumerate(events) if e.kind is E.TURN_PHASE_CHANGED)
        request = next(i for i, e in enumerate(events) if e.kind is E.MODEL_REQUESTED)
        assert events[phase].data["phase"] == "preparing"
        assert phase < request
    finally:
        runtime.close()


@pytest.mark.parametrize("reason", ["first_answer", "tool_continuation", "provider_overflow", "large_history"])
def test_only_a_modest_first_answer_can_defer_the_soft_context_target(reason):
    from app.ai import ModelContextLimits
    from app.agent_runtime.context_budget import prepare_context, request_forced_compaction
    from test_context_budget_codex_compaction import FakeRuntime, Session, Step, _history
    runtime = FakeRuntime([ModelResponse(text="The prior requirements and results are recorded. Continue the task.")])
    runtime.limits.working_context_tokens = 8_000
    profile = SimpleNamespace(context_limits=ModelContextLimits(context_window_tokens=100_000,
        effective_context_percent=100, output_reserve_tokens=1000, auto_compact_token_limit=90_000))
    runtime.platform = runtime.session_platform = SimpleNamespace(registry=SimpleNamespace(get=lambda _: profile))
    session = Session(_history(pairs=4, chars=4000 if reason != "large_history" else 12_000))
    step = Step()
    step.model_step = 2 if reason == "tool_continuation" else 1
    if reason == "provider_overflow":
        request_forced_compaction(runtime, session)
    _, metadata = prepare_context(runtime, session, step, SimpleNamespace(cancelled=False))
    if reason == "first_answer":
        assert metadata["soft_compaction_deferred"] is True
        assert not runtime.model_executor.requests
        assert not runtime.commits
    else:
        assert metadata["auto_compacted"] is True
        assert runtime.model_executor.requests
