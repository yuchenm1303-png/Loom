from __future__ import annotations

import json
import threading
import time

from app.agent_runtime import (
    AgentRuntime,
    AgentStatus,
    FileAgentSessionStore,
    MemoryCandidate,
    MemoryCategory,
    MemoryScope,
    MemoryStore,
    SandboxManager,
    SandboxPolicy,
)
from app.agent_runtime.memory_semantic import (
    MemorySemanticStore,
    SemanticBundle,
    SemanticMemorySnapshot,
    validate_semantic_plan,
)
from app.agent_runtime.workspace_tools import loom_default_tools
from app.ai import AGENT_FAST_ROLE, ModelResponse, ModelUsage


def _wait_until(predicate, *, timeout=3.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("condition did not become true before timeout")


def _seed(
    store: MemoryStore,
    workspace,
    *,
    session_id: str,
    turn_id: str,
    text: str,
    evidence: str,
    category: MemoryCategory = MemoryCategory.DECISION,
):
    extraction = store.add_extraction(
        source_session_id=session_id,
        source_turn_id=turn_id,
        workspace=workspace,
        summary="seed",
        candidates=(
            MemoryCandidate(
                text=text,
                scope=MemoryScope.WORKSPACE,
                category=category,
                importance=4,
                evidence=evidence,
            ),
        ),
    )
    record = store.consolidate_pending()[0]
    return extraction, record


def test_semantic_merge_moves_evidence_and_aggregates_sources(tmp_path):
    workspace = tmp_path / "project"
    workspace.mkdir()
    store = MemoryStore(tmp_path / "memory")
    semantic = MemorySemanticStore(store)

    _, old = _seed(
        store,
        workspace,
        session_id="session-old",
        turn_id="turn-old",
        text="The project testing framework is pytest.",
        evidence="The user selected pytest as the testing framework.",
    )
    extraction, new = _seed(
        store,
        workspace,
        session_id="session-new",
        turn_id="turn-new",
        text="Use pytest as the required test runner for this workspace.",
        evidence="The user confirmed pytest should be used for project tests.",
    )

    assert semantic.enqueue(extraction.extraction_id, "session-new") is True
    job = semantic.claim(extraction.extraction_id)
    assert job is not None
    bundle = semantic.bundle(extraction.extraction_id)
    assert {item.memory_id for item in bundle.related_memories} >= {old.memory_id}

    plan = validate_semantic_plan(
        {
            "operations": [
                {
                    "action": "merge",
                    "target_memory_id": new.memory_id,
                    "source_memory_ids": [old.memory_id],
                    "text": "This workspace uses pytest as its required test framework and runner.",
                    "importance": 5,
                    "basis_memory_ids": [new.memory_id],
                    "reason": "Both memories express the same durable testing decision.",
                }
            ]
        },
        bundle,
    )
    semantic.apply_and_complete(
        job,
        plan,
        expected_versions=bundle.expected_versions,
        usage=ModelUsage(10, 5, 15),
    )

    merged = store.get(new.memory_id)
    superseded = store.get(old.memory_id)
    assert merged is not None and superseded is not None
    assert merged.status == "active"
    assert merged.source_count == 2
    assert merged.importance == 5
    assert superseded.status == "superseded"
    assert len(store.evidence(merged.memory_id)) == 2
    visible = store.list_records(workspace=workspace)
    assert [record.memory_id for record in visible] == [merged.memory_id]
    completed = semantic.job(extraction.extraction_id)
    assert completed is not None and completed.status == "completed"
    assert completed.operation_count == 1


def test_semantic_validator_rejects_cross_scope_merge(tmp_path):
    _ = tmp_path
    global_memory = SemanticMemorySnapshot(
        memory_id="global-1",
        scope=MemoryScope.GLOBAL,
        scope_key="global",
        category=MemoryCategory.PREFERENCE,
        text="The user prefers concise answers.",
        importance=5,
        source_count=1,
        usage_count=0,
        created_at="2026-01-01T00:00:00+00:00",
        updated_at="2026-01-01T00:00:00+00:00",
    )
    workspace_memory = SemanticMemorySnapshot(
        memory_id="workspace-1",
        scope=MemoryScope.WORKSPACE,
        scope_key="workspace-key",
        category=MemoryCategory.PREFERENCE,
        text="Keep this project's release notes concise.",
        importance=3,
        source_count=1,
        usage_count=0,
        created_at="2026-01-02T00:00:00+00:00",
        updated_at="2026-01-02T00:00:00+00:00",
    )
    bundle = SemanticBundle(
        extraction_id="extraction",
        new_memories=(workspace_memory,),
        related_memories=(global_memory,),
    )

    try:
        validate_semantic_plan(
            {
                "operations": [
                    {
                        "action": "merge",
                        "target_memory_id": "workspace-1",
                        "source_memory_ids": ["global-1"],
                        "text": "Prefer concise answers.",
                        "importance": 5,
                        "basis_memory_ids": ["workspace-1"],
                    }
                ]
            },
            bundle,
        )
    except RuntimeError as exc:
        assert "scope or category boundary" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("semantic merge must not cross a memory scope boundary")


class SemanticRoutingPlatform:
    def __init__(self, *, fail_semantic: bool = False):
        self.fail_semantic = fail_semantic
        self.requests = []
        self._lock = threading.Lock()

    def execute_chat(self, profile_id, request):
        with self._lock:
            self.requests.append((profile_id, request))
        system = str(request.messages[0].content or "")
        if "long-term memory extraction stage" in system:
            return ModelResponse(
                text=json.dumps(
                    {
                        "summary": "Testing decision changed.",
                        "memories": [
                            {
                                "text": "This workspace now uses unittest instead of pytest.",
                                "scope": "workspace",
                                "category": "decision",
                                "importance": 5,
                                "evidence": "User: switch this project from pytest to unittest.",
                            }
                        ],
                    }
                ),
                usage=ModelUsage(12, 8, 20),
            )
        if "semantic long-term memory consolidation stage" in system:
            if self.fail_semantic:
                return ModelResponse(text="not-json", usage=ModelUsage(8, 2, 10))
            user_text = str(request.messages[-1].content or "")
            payload = json.loads(user_text[user_text.find("{") :])
            new_id = payload["new_memory_ids"][0]
            old_id = next(
                item["memory_id"]
                for item in payload["related_memories"]
                if "pytest" in item["text"].casefold()
            )
            return ModelResponse(
                text=json.dumps(
                    {
                        "operations": [
                            {
                                "action": "supersede",
                                "replacement_memory_id": new_id,
                                "old_memory_ids": [old_id],
                                "basis_memory_ids": [new_id],
                                "reason": "The user explicitly replaced the earlier testing decision.",
                            }
                        ]
                    }
                ),
                usage=ModelUsage(10, 5, 15),
            )
        return ModelResponse(text="foreground done", usage=ModelUsage(5, 2, 7))


def _runtime(tmp_path, platform):
    store = FileAgentSessionStore(tmp_path / "state")
    runtime = AgentRuntime(
        platform=platform,
        store=store,
        tools=loom_default_tools(),
        sandbox_manager=SandboxManager(policy=SandboxPolicy.OFF),
        memory_auto_extract=True,
        memory_idle_seconds=0.01,
        memory_semantic_auto=True,
    )
    return runtime, store


def test_default_runtime_semantically_supersedes_old_decision(tmp_path):
    platform = SemanticRoutingPlatform()
    runtime, _ = _runtime(tmp_path, platform)
    workspace = tmp_path / "project"
    workspace.mkdir()
    session = runtime.create_session(AGENT_FAST_ROLE.role_id, workspace_dir=workspace)

    _, old = _seed(
        runtime.memory_store,
        workspace,
        session_id="seed-session",
        turn_id="seed-turn",
        text="This workspace uses pytest for its test suite.",
        evidence="The user previously chose pytest.",
    )

    result = runtime.start_turn(
        session.session_id,
        "Switch this project from pytest to unittest and remember that decision.",
    )
    assert result.status is AgentStatus.COMPLETED

    _wait_until(
        lambda: runtime.memory_status(session.session_id)["semantic_completed"] >= 1,
        timeout=4.0,
    )
    old_after = runtime.memory_store.get(old.memory_id)
    assert old_after is not None and old_after.status == "superseded"
    active = runtime.list_memory(session.session_id)
    assert len(active) == 1
    assert "unittest" in active[0].text
    assert not any("pytest for its test suite" in item.text for item in active)
    runtime.close()


def test_semantic_failure_does_not_repeat_stage_one_extraction(tmp_path):
    platform = SemanticRoutingPlatform(fail_semantic=True)
    runtime, _ = _runtime(tmp_path, platform)
    workspace = tmp_path / "project"
    workspace.mkdir()
    session = runtime.create_session(AGENT_FAST_ROLE.role_id, workspace_dir=workspace)

    _seed(
        runtime.memory_store,
        workspace,
        session_id="seed-session",
        turn_id="seed-turn",
        text="This workspace uses pytest for its test suite.",
        evidence="The user previously chose pytest.",
    )

    result = runtime.start_turn(
        session.session_id,
        "Switch this project from pytest to unittest and remember that decision.",
    )
    assert result.status is AgentStatus.COMPLETED

    _wait_until(
        lambda: runtime.memory_status(session.session_id)["semantic_retry"] >= 1,
        timeout=4.0,
    )
    state = runtime.memory_store.thread_state(session.session_id)
    assert state.last_turn_id == result.turn_id
    assert state.last_event_id

    records_before = runtime.list_memory(session.session_id)
    source_counts_before = {record.memory_id: record.source_count for record in records_before}
    request_count = len(platform.requests)

    # Stage 1 already advanced its durable event checkpoint even though Stage 2
    # failed, so this must be a no-op and must not duplicate source evidence.
    assert runtime.extract_memory_from_events(session.session_id) is None
    assert len(platform.requests) == request_count
    records_after = runtime.list_memory(session.session_id)
    assert {record.memory_id: record.source_count for record in records_after} == source_counts_before
    runtime.close()
