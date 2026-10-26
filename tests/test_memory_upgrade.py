from __future__ import annotations

import json

import pytest

from app.agent_runtime import AgentEventKind, MemoryCandidate, MemoryCategory, MemoryScope, ToolContext
from app.ai import AGENT_FAST_ROLE, ModelResponse
from app.agent_runtime.memory_runtime import _render_memory_handoffs
from test_memory_pipeline import ScriptedPlatform, _runtime, _wait_until


def test_handoff_context_budget_does_not_grow_with_large_paths_or_evidence():
    item = dict(session_id="session", turn_id="turn", request="q" * 4000,
                evidence="e" * 12000, paths=["p" * 1000] * 100, outcome="turn_failed")
    rendered = _render_memory_handoffs([item, item])
    assert len(rendered) < 4200
    assert "handoff:session:turn" in rendered


def test_failed_work_is_recalled_without_extraction_model_and_is_workspace_scoped(tmp_path):
    platform = ScriptedPlatform([ModelResponse(text="done")])
    runtime, store = _runtime(tmp_path, platform, auto_extract=False)
    workspace = tmp_path / "project"
    workspace.mkdir()
    session = runtime.create_session(AGENT_FAST_ROLE.role_id, workspace_dir=workspace)
    runtime._emit_external_event(session.session_id, AgentEventKind.USER_MESSAGE,
                                 {"text": "改造首页，保持编辑式风格 API_KEY=secret-value"}, turn_id="work")
    runtime._emit_external_event(session.session_id, AgentEventKind.TURN_DIFF_UPDATED,
                                 {"paths": ["desktop-react/EditorialHome.tsx"]}, turn_id="work")
    runtime._emit_external_event(session.session_id, AgentEventKind.TURN_FAILED,
                                 {"error": "402 insufficient balance"}, turn_id="work")
    hits = runtime.memory_store.search_handoffs("homepage editorial", workspace=workspace)
    assert len(hits) == 1
    assert hits[0]["outcome"] == "turn_failed"
    assert hits[0]["paths"] == ["desktop-react/EditorialHome.tsx"]
    assert "secret-value" not in json.dumps(hits)
    assert "402" in hits[0]["evidence"]
    memory_id = f"handoff:{session.session_id}:work"
    assert runtime.memory_store.read_handoff(memory_id, workspace=workspace)
    assert runtime.memory_store.read_handoff(memory_id, workspace=tmp_path / "other") is None
    assert runtime.memory_store.search_handoffs("homepage", workspace=tmp_path / "other") == []
    # Idempotent capture and restart preserve a single handoff.
    terminal = store.last_event(session.session_id, (AgentEventKind.TURN_FAILED,))
    runtime._capture_memory_handoff(terminal)
    assert len(runtime.memory_store.search_handoffs("homepage", workspace=workspace)) == 1
    later = runtime.create_session(AGENT_FAST_ROLE.role_id, workspace_dir=workspace)
    runtime.start_turn(later.session_id, "之前的首页改版到哪里了")
    injected = next(message.content for message in platform.requests[0][1].messages if message.name == "loom_memory")
    assert "PROJECT_HANDOFFS" in injected
    assert "EditorialHome.tsx" in injected
    assert "turn_failed" in injected
    context = ToolContext(session_id=later.session_id, turn_id="read", workspace=workspace)
    result = runtime.tools.get("search_memory").handler(context, {"query": "homepage"})
    assert result.data["handoffs"][0]["memory_id"] == memory_id
    result = runtime.tools.get("read_memory").handler(context, {"memory_id": memory_id})
    assert result.ok and result.data["handoff"]["outcome"] == "turn_failed"
    runtime.configure_memory(memory_enabled=False)
    assert not runtime.tools.get("read_memory").handler(context, {"memory_id": memory_id}).ok
    runtime.configure_memory(memory_enabled=True, auto_extract=False)
    assert runtime.forget_memory(later.session_id, memory_id)
    runtime._capture_memory_handoff(terminal)
    assert runtime.memory_store.read_handoff(memory_id, workspace=workspace) is None
    runtime.close()


def test_restart_backfills_failed_turn_before_model_extraction(tmp_path):
    runtime, store = _runtime(tmp_path, ScriptedPlatform([]), auto_extract=False)
    session = runtime.create_session(AGENT_FAST_ROLE.role_id, workspace_dir=tmp_path)
    runtime.memory_enabled = False  # Simulate an older host which did not capture handoffs.
    runtime._emit_external_event(session.session_id, AgentEventKind.USER_MESSAGE,
                                 {"text": "homepage redesign"}, turn_id="interrupted")
    terminal = runtime._emit_external_event(session.session_id, AgentEventKind.TURN_INTERRUPTED,
                                           {"error": "host stopped"}, turn_id="interrupted")
    runtime.close()
    platform = ScriptedPlatform([ModelResponse(text='{"summary":"partial work","memories":[]}')])
    restarted, _ = _runtime(tmp_path, platform)
    restarted.memory_status(session.session_id)
    _wait_until(lambda: restarted.memory_store.thread_state(session.session_id).last_event_id == terminal.event_id)
    hits = restarted.memory_store.search_handoffs("homepage", workspace=tmp_path)
    assert hits[0]["outcome"] == "turn_interrupted"
    restarted.close()


def test_extraction_range_is_idempotent_at_store_boundary(tmp_path):
    runtime, _ = _runtime(tmp_path, ScriptedPlatform([]), auto_extract=False)
    candidate = MemoryCandidate(text="Use pytest", scope=MemoryScope.WORKSPACE,
                                category=MemoryCategory.DECISION, evidence="User chose pytest")
    arguments = dict(source_session_id="s", source_turn_id="t", workspace=tmp_path,
                     summary="observed", candidates=[candidate],
                     source_start_event_id="start", source_end_event_id="end")
    first = runtime.memory_store.add_extraction(**arguments)
    second = runtime.memory_store.add_extraction(**arguments)
    assert first.extraction_id == second.extraction_id
    records = runtime.memory_store.consolidate_pending()
    assert len(records) == 1 and records[0].source_count == 1
    runtime.memory_store.save_handoff(session_id="s", turn_id="t", workspace=str(tmp_path),
        terminal_event_id="end", outcome="turn_failed", request="Use pytest", evidence="User chose pytest",
        paths=[], updated_at="2026-10-26T00:00:00+00:00")
    assert runtime.memory_store.delete(records[0].memory_id)
    assert runtime.memory_store.search_handoffs("pytest", workspace=tmp_path) == []
    runtime.close()


def test_extraction_chunks_do_not_skip_later_events(tmp_path):
    platform = ScriptedPlatform([ModelResponse(text='{"summary":"chunk","memories":[]}') for _ in range(10)])
    runtime, store = _runtime(tmp_path, platform, auto_extract=False)
    runtime.memory_max_extraction_chars = 4000
    runtime.memory_auto_extract = True
    session = runtime.create_session(AGENT_FAST_ROLE.role_id, workspace_dir=tmp_path)
    for index in range(6):
        runtime._emit_external_event(session.session_id, AgentEventKind.USER_MESSAGE,
            {"text": f"UNIQUE_{index} " + "observable " * 120}, turn_id="long-work")
    terminal = runtime._emit_external_event(session.session_id, AgentEventKind.TURN_CANCELLED,
        {"reason": "cancelled"}, turn_id="long-work")
    for _ in range(10):
        if runtime.extract_memory_from_events(session.session_id) is None:
            break
    supplied = "\n".join(str(request.messages[-1].content) for _, request in platform.requests)
    for index in range(6):
        assert f"UNIQUE_{index}" in supplied
    assert len(platform.requests) > 1
    assert runtime.memory_store.thread_state(session.session_id).last_event_id == terminal.event_id
    assert "turn_cancelled" in supplied
    runtime.close()


def test_retry_after_persisted_extraction_does_not_call_model_or_duplicate_sources(tmp_path, monkeypatch):
    platform = ScriptedPlatform([ModelResponse(text='{"summary":"saved","memories":[]}')])
    runtime, _ = _runtime(tmp_path, platform, auto_extract=False)
    runtime.memory_auto_extract = True
    session = runtime.create_session(AGENT_FAST_ROLE.role_id, workspace_dir=tmp_path)
    runtime._emit_external_event(session.session_id, AgentEventKind.USER_MESSAGE, {"text": "Use pytest"})
    runtime._emit_external_event(session.session_id, AgentEventKind.TURN_COMPLETED, {"text": "done"})
    original = runtime.memory_store.mark_thread_success
    monkeypatch.setattr(runtime.memory_store, "mark_thread_success", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("crash")))
    with pytest.raises(RuntimeError, match="crash"):
        runtime.extract_memory_from_events(session.session_id)
    monkeypatch.setattr(runtime.memory_store, "mark_thread_success", original)
    runtime.extract_memory_from_events(session.session_id)
    assert len(platform.requests) == 1
    runtime.close()


def test_bilingual_homepage_ranks_above_old_domain_and_frequent_noise(tmp_path):
    runtime, _ = _runtime(tmp_path, ScriptedPlatform([]), auto_extract=False)
    store = runtime.memory_store
    store.add_extraction(source_session_id="old", source_turn_id="t", workspace=tmp_path,
        summary="seed", candidates=tuple(MemoryCandidate(text=text, scope=MemoryScope.WORKSPACE,
            category=MemoryCategory.PROJECT, importance=importance) for text, importance in [
                ("The homepage redesign uses an editorial skin in WebPortal.tsx with Newsreader font.", 3),
                ("loom.smirel.com is the old OAuth gateway domain.", 5),
                ("用户拥有代理服务，代码调试要求精准定位问题。", 5),
            ]))
    records = store.consolidate_pending()
    noise = next(record for record in records if "代理" in record.text)
    for _ in range(20):
        store.mark_used([noise.memory_id])
    hits = store.search_hits("看看我loom首页https://loom.smirel.com/的代码吧，之前改版", workspace=tmp_path,
                             require_query_match=True)
    assert "editorial" in hits[0].record.text
    assert all(hit.record.memory_id != noise.memory_id for hit in hits)
    assert store.search_hits("无关西瓜乐团", workspace=tmp_path, require_query_match=True) == ()
    runtime.close()
