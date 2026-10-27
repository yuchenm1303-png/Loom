import threading
from dataclasses import replace
from types import SimpleNamespace

import pytest

from app.ai import AIMessage, MessageRole, ModelResponse, ModelUsage, ToolCall
from app.agent_runtime.contracts import AgentEvent, AgentEventKind as E
from app.agent_runtime.idle_compaction import ReadySummary, _identity
from app.agent_runtime.runtime import CancellationToken
from test_compaction_task import _runtime


def ready(runtime, session):
    session.messages = [message for i in range(8) for message in (
        AIMessage(role=MessageRole.USER, content=f"Requirement number {i}"),
        AIMessage(role=MessageRole.ASSISTANT, content="Historical result. " * 1800))]
    runtime.store.save(session)
    candidate = ReadySummary(tuple(session.messages), runtime.platform_for_session(session.session_id),
        _identity(session), "latin", "Requirements 0 through 7 were discussed. Continue with the latest user request.",
        ModelUsage(input_tokens=100, output_tokens=20, total_tokens=120))
    runtime.idle_compaction._ready[session.session_id] = candidate
    return candidate


def test_ready_summary_installs_without_sampling_and_preserves_new_input(tmp_path):
    runtime, platform, store, session = _runtime(tmp_path, [])
    try:
        old = ready(runtime, session)
        newest = AIMessage(role=MessageRole.USER, content="Correction: keep all original files and explain only.")
        session.messages.append(newest)
        assert runtime.idle_compaction.apply_ready(session)
        assert session.messages[-1] == newest
        assert any(m.name == "loom_compaction" for m in session.messages)
        assert not platform.requests
        assert store.load(session.session_id).messages[-1] == newest
        assert runtime.list_context_checkpoints(session.session_id)[0].archived_message_count == len(old.history) + 1
    finally:
        runtime.close()


@pytest.mark.parametrize("change", ["history", "language", "model", "prompt", "platform"])
def test_stale_candidates_never_replace_changed_history_or_configuration(tmp_path, change):
    runtime, _, _, session = _runtime(tmp_path, [])
    try:
        ready(runtime, session)
        if change == "history":
            session.messages[0] = AIMessage(role=MessageRole.USER, content="An edited requirement")
        elif change == "language":
            session.messages.append(AIMessage(role=MessageRole.USER, content="请用中文回答最新的问题。"))
        elif change == "model":
            session.model = "another-model"
        elif change == "prompt":
            session.system_prompt += "\nNew policy."
        else:
            runtime.set_session_model(session.session_id, object())
        before = tuple(session.messages)
        assert not runtime.idle_compaction.apply_ready(session)
        assert tuple(session.messages) == before
    finally:
        runtime.close()


def test_speculation_never_owns_session_or_streams_and_cancel_does_not_wait(tmp_path, monkeypatch):
    runtime, _, store, session = _runtime(tmp_path, [])
    entered, finish, done = threading.Event(), threading.Event(), threading.Event()
    ready(runtime, session)
    runtime.idle_compaction._ready.clear()
    snapshot = tuple(session.messages)
    def generate(_self, _platform, _profile, request, token):
        assert not request.tools
        entered.set()
        assert finish.wait(5)
        return ModelResponse(text="A valid summary.", usage=ModelUsage(total_tokens=10))
    monkeypatch.setattr("app.agent_runtime.idle_compaction.ModelExecutor.execute", generate)
    token = CancellationToken()
    service = runtime.idle_compaction
    service._active = (session.session_id, token)
    def run():
        try:
            service._generate(session.session_id, snapshot, runtime.platform, _identity(session), "en", token)
        finally:
            done.set()
    worker = threading.Thread(target=run, daemon=True)
    try:
        worker.start()
        assert entered.wait(5)
        service.cancel()
        assert token.cancelled
        assert not done.is_set()  # cancellation returned without joining worker
        assert tuple(store.load(session.session_id).messages) == snapshot
        finish.set()
        assert done.wait(5)
        assert session.session_id not in service._ready
        assert tuple(store.load(session.session_id).messages) == snapshot
        assert not any(e.kind is E.CONTEXT_CHECKPOINTED for e in store.events(session.session_id))
    finally:
        finish.set()
        worker.join(5)
        runtime.close()


def test_unused_background_usage_survives_restart_and_is_accounted_once(tmp_path):
    runtime, _, store, session = _runtime(tmp_path, [])
    try:
        store.append_event(AgentEvent("receipt", session.session_id, "", E.CONTEXT_SUMMARY_PREPARED,
            "2026-10-27T00:00:00+00:00", {"valid": False, "usage": {
                "input_tokens": 100, "output_tokens": 20, "total_tokens": 120, "cached_input_tokens": 10}}))
        from app.agent_runtime.idle_compaction import IdleCompaction
        service = IdleCompaction(runtime)
        before = session.usage.total_tokens
        assert not service.apply_ready(session)
        assert session.usage.total_tokens == before + 120
        session = store.load(session.session_id)
        IdleCompaction(runtime).apply_ready(session)
        assert session.usage.total_tokens == before + 120
    finally:
        runtime.close()


def test_idle_scheduler_warms_one_summary_without_mutating_the_snapshot(tmp_path, monkeypatch):
    runtime, platform, store, session = _runtime(tmp_path, [])
    runtime.limits = replace(runtime.limits, working_context_tokens=64_000)
    done = threading.Event()
    try:
        candidate = ready(runtime, session)
        service = runtime.idle_compaction
        service._ready.clear()
        requests = []
        def execute(_self, _platform, _profile, request, token):
            requests.append(request)
            return ModelResponse(text="Requirements were discussed and the requested work remains recorded.",
                usage=ModelUsage(input_tokens=100, output_tokens=20, total_tokens=120))
        monkeypatch.setattr("app.agent_runtime.idle_compaction.ModelExecutor.execute", execute)
        original = service._generate
        def generate(*args):
            try:
                original(*args)
            finally:
                done.set()
        monkeypatch.setattr(service, "_generate", generate)
        service.schedule(session, 60_000)
        assert done.wait(5)
        assert len(requests) == 1
        assert tuple(store.load(session.session_id).messages) == candidate.history
        assert session.session_id in service._ready
        service.schedule(session, 60_000)
        assert len(requests) == 1
        assert service.apply_ready(session)
        assert session.usage.total_tokens == 120
        service.apply_ready(session)
        assert session.usage.total_tokens == 120
    finally:
        runtime.close()


@pytest.mark.parametrize("changed_evidence", [False, True])
def test_persisted_candidate_survives_host_restart_but_not_edited_tool_evidence(tmp_path, changed_evidence):
    from app.agent_runtime.idle_compaction import IdleCompaction, _configuration_key
    runtime, platform, store, session = _runtime(tmp_path, [])
    try:
        platform.registry = SimpleNamespace(get=lambda _: SimpleNamespace(as_safe_dict=lambda: {"model": "mock"}))
        candidate = ready(runtime, session)
        # Transport call IDs alone must not count as proof of unchanged evidence.
        tool = AIMessage(role=MessageRole.TOOL, tool_call_id="old-call", content="Original tool output")
        session.messages.extend((AIMessage(role=MessageRole.ASSISTANT, content="", tool_calls=(
            ToolCall("old-call", "echo", {"value": "Original tool output"}),)), tool))
        candidate = replace(candidate, history=tuple(session.messages),
            configuration=_configuration_key(_identity(session), platform))
        runtime.idle_compaction._persist(session.session_id, candidate)
        runtime.idle_compaction._ready.clear()
        if changed_evidence:
            session.messages[-1] = replace(tool, content="Edited tool output")
        session.messages.append(AIMessage(role=MessageRole.USER, content="Explain the recorded outcome."))
        service = IdleCompaction(runtime)
        assert service.apply_ready(session) is (not changed_evidence)
        assert session.messages[-1].content == "Explain the recorded outcome."
        assert not service._candidate_path(session.session_id).exists()
    finally:
        runtime.close()


@pytest.mark.parametrize("response", [ModelResponse(text=""), ModelResponse(text="Partial summary", finish_reason="length"), ModelResponse(text="Summary", tool_calls=(
    ToolCall("bad", "exec", {"argv": ["untrusted"]}),))])
def test_invalid_summary_is_not_cached_and_cannot_execute_tools(tmp_path, monkeypatch, response):
    runtime, platform, store, session = _runtime(tmp_path, [])
    try:
        monkeypatch.setattr("app.agent_runtime.idle_compaction.ModelExecutor.execute", lambda *args: response)
        runtime.idle_compaction._generate(session.session_id, tuple(session.messages), platform,
            _identity(session), "en", CancellationToken())
        assert session.session_id not in runtime.idle_compaction._ready
        receipt = store.last_event(session.session_id, (E.CONTEXT_SUMMARY_PREPARED,))
        assert receipt.data["valid"] is False
    finally:
        runtime.close()
