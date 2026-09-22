from __future__ import annotations

from app.agent_runtime.memory_store import (
    MemoryCandidate,
    MemoryCategory,
    MemoryScope,
    MemoryStore,
)


def _seed(store: MemoryStore, workspace, *, session: str, turn: str, candidates):
    store.add_extraction(
        source_session_id=session,
        source_turn_id=turn,
        workspace=workspace,
        summary="seed",
        candidates=candidates,
    )
    return store.consolidate_pending()


def test_memory_v3_index_routing_usage_and_skill_candidates(tmp_path):
    workspace = tmp_path / "project"
    workspace.mkdir()
    store = MemoryStore(tmp_path / "memory")

    records = _seed(
        store,
        workspace,
        session="s1",
        turn="t1",
        candidates=(
            MemoryCandidate(
                text="Production deployment uses GitHub main and Docker.",
                scope=MemoryScope.WORKSPACE,
                category=MemoryCategory.DECISION,
                importance=5,
                evidence="User chose GitHub main and Docker.",
            ),
            MemoryCandidate(
                text="The project uses pytest for backend tests.",
                scope=MemoryScope.WORKSPACE,
                category=MemoryCategory.PROJECT,
                importance=4,
                evidence="User confirmed pytest.",
            ),
            MemoryCandidate(
                text="The user prefers concise answers across projects.",
                scope=MemoryScope.GLOBAL,
                category=MemoryCategory.PREFERENCE,
                importance=4,
                evidence="User stated a global preference.",
            ),
        ),
    )
    deploy = next(record for record in records if "Production deployment" in record.text)

    _seed(
        store,
        workspace,
        session="s2",
        turn="t2",
        candidates=(
            MemoryCandidate(
                text="Production deployment uses GitHub main and Docker.",
                scope=MemoryScope.WORKSPACE,
                category=MemoryCategory.DECISION,
                importance=5,
                evidence="The same deployment decision was confirmed again.",
            ),
        ),
    )

    index = store.compact_index(workspace=workspace)
    assert index["version"] == 3
    assert index["active"] == 3
    assert index["categories"]["decision"] == 1
    assert "decisions" in index["summary"]
    assert "Production deployment" in index["summary"]

    hits = store.search_hits(
        "GitHub Docker deployment",
        workspace=workspace,
        require_query_match=True,
    )
    assert hits
    assert hits[0].record.memory_id == deploy.memory_id
    assert hits[0].score > 0
    assert hits[0].reasons
    assert store.search_hits(
        "watermelon orchestra",
        workspace=workspace,
        require_query_match=True,
    ) == ()

    store.record_usage(
        (deploy.memory_id,),
        source_session_id="s1",
        source_turn_id="turn-live",
        route="auto_route",
        scores={deploy.memory_id: hits[0].score},
        reasons={deploy.memory_id: ",".join(hits[0].reasons)},
    )
    store.record_usage(
        (deploy.memory_id,),
        source_session_id="s1",
        source_turn_id="turn-live",
        route="auto_route",
    )
    refreshed = store.get(deploy.memory_id)
    assert refreshed is not None
    assert refreshed.usage_count == 1
    events = store.usage_events(deploy.memory_id)
    assert len(events) == 1
    assert events[0].route == "auto_route"

    candidates = store.skill_candidates(workspace=workspace)
    assert [record.memory_id for record in candidates] == [deploy.memory_id]


def test_memory_v3_archive_restore_and_reactivation(tmp_path):
    workspace = tmp_path / "project"
    workspace.mkdir()
    store = MemoryStore(tmp_path / "memory")

    records = _seed(
        store,
        workspace,
        session="s1",
        turn="t1",
        candidates=(
            MemoryCandidate(
                text="Temporary scratch fact that is safe to age out.",
                scope=MemoryScope.WORKSPACE,
                category=MemoryCategory.FACT,
                importance=1,
            ),
            MemoryCandidate(
                text="Keep deploys on GitHub main.",
                scope=MemoryScope.WORKSPACE,
                category=MemoryCategory.DECISION,
                importance=1,
            ),
        ),
    )
    scratch = next(record for record in records if record.category is MemoryCategory.FACT)
    decision = next(record for record in records if record.category is MemoryCategory.DECISION)

    with store._lock, store._connect() as connection:
        connection.execute(
            "UPDATE memories SET updated_at = ? WHERE memory_id IN (?, ?)",
            ("2020-01-01T00:00:00+00:00", scratch.memory_id, decision.memory_id),
        )

    archived_ids = store.maintain_lifecycle(workspace=workspace, inactive_days=30)
    assert scratch.memory_id in archived_ids
    assert decision.memory_id not in archived_ids
    assert store.get(scratch.memory_id).status == "archived"

    archived_records = store.list_records(
        workspace=workspace,
        include_global=False,
        status="archived",
    )
    assert [record.memory_id for record in archived_records] == [scratch.memory_id]

    assert store.restore(scratch.memory_id) is True
    assert store.get(scratch.memory_id).status == "active"
    assert store.archive(scratch.memory_id, note="manual test archive") is True
    archived = store.get(scratch.memory_id)
    assert archived is not None
    assert archived.status == "archived"
    assert archived.lifecycle_note == "manual test archive"

    _seed(
        store,
        workspace,
        session="s2",
        turn="t2",
        candidates=(
            MemoryCandidate(
                text="Temporary scratch fact that is safe to age out.",
                scope=MemoryScope.WORKSPACE,
                category=MemoryCategory.FACT,
                importance=2,
            ),
        ),
    )
    reactivated = store.get(scratch.memory_id)
    assert reactivated is not None
    assert reactivated.status == "active"
    assert reactivated.archived_at == ""
    assert reactivated.lifecycle_note == ""


def test_memory_v3_delete_removes_usage_history(tmp_path):
    workspace = tmp_path / "project"
    workspace.mkdir()
    store = MemoryStore(tmp_path / "memory")
    record = _seed(
        store,
        workspace,
        session="s1",
        turn="t1",
        candidates=(
            MemoryCandidate(
                text="Remember the release checklist.",
                scope=MemoryScope.WORKSPACE,
                category=MemoryCategory.PROJECT,
                importance=4,
            ),
        ),
    )[0]
    store.record_usage(
        (record.memory_id,),
        source_session_id="s1",
        source_turn_id="t2",
        route="search",
    )
    assert store.usage_events(record.memory_id)
    assert store.delete(record.memory_id) is True
    assert store.usage_events(record.memory_id) == ()
