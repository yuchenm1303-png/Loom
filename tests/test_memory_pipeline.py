from __future__ import annotations

import json
import sqlite3
import threading
import time

from app.agent_runtime import (
    AgentRuntime,
    AgentStatus,
    FileAgentSessionStore,
    MemoryStore,
    SandboxManager,
    SandboxPolicy,
)
from app.agent_runtime.workspace_tools import loom_default_tools
from app.ai import AGENT_FAST_ROLE, ModelResponse, ModelUsage


def _memory_payload(text: str = "Use pytest for this workspace.") -> str:
    return json.dumps(
        {
            "summary": "Durable project testing preference.",
            "memories": [
                {
                    "text": text,
                    "scope": "workspace",
                    "category": "decision",
                    "importance": 4,
                    "evidence": "User asked to use pytest for the project test suite.",
                }
            ],
        }
    )


class ScriptedPlatform:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []
        self._lock = threading.Lock()

    def execute_chat(self, profile_id, request):
        with self._lock:
            self.requests.append((profile_id, request))
            if not self.responses:
                raise AssertionError("scripted platform ran out of responses")
            return self.responses.pop(0)


class BlockingExtractionPlatform:
    def __init__(self):
        self.extraction_started = threading.Event()
        self.release_extraction = threading.Event()
        self.requests = []
        self._lock = threading.Lock()

    def execute_chat(self, profile_id, request):
        with self._lock:
            self.requests.append((profile_id, request))
        first = request.messages[0].content
        if isinstance(first, str) and "long-term memory extraction stage" in first:
            self.extraction_started.set()
            if not self.release_extraction.wait(timeout=5):
                raise AssertionError("test did not release memory extraction")
            return ModelResponse(text=_memory_payload(), usage=ModelUsage(12, 8, 20))
        return ModelResponse(text="foreground done", usage=ModelUsage(5, 2, 7))


def _runtime(
    tmp_path,
    platform,
    *,
    auto_extract=True,
    idle_seconds=0.01,
):
    store = FileAgentSessionStore(tmp_path / "state")
    runtime = AgentRuntime(
        platform=platform,
        store=store,
        tools=loom_default_tools(),
        sandbox_manager=SandboxManager(policy=SandboxPolicy.OFF),
        memory_auto_extract=auto_extract,
        memory_idle_seconds=idle_seconds,
    )
    return runtime, store


def _wait_until(predicate, *, timeout=3.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("condition did not become true before timeout")


def test_completed_turn_is_extracted_in_background_with_evidence(tmp_path):
    platform = ScriptedPlatform(
        [
            ModelResponse(text="done", usage=ModelUsage(5, 2, 7)),
            ModelResponse(text=_memory_payload(), usage=ModelUsage(12, 8, 20)),
        ]
    )
    runtime, _ = _runtime(tmp_path, platform)
    workspace = tmp_path / "project"
    workspace.mkdir()
    session = runtime.create_session(AGENT_FAST_ROLE.role_id, workspace_dir=workspace)

    result = runtime.start_turn(session.session_id, "Use pytest for this project.")

    assert result.status is AgentStatus.COMPLETED
    _wait_until(lambda: runtime.memory_store.counts(workspace=workspace)["total"] == 1)

    records = runtime.list_memory(session.session_id)
    assert len(records) == 1
    evidence = runtime.memory_store.evidence(records[0].memory_id)
    assert len(evidence) == 1
    assert evidence[0].source_session_id == session.session_id
    assert evidence[0].source_turn_id == result.turn_id
    assert "pytest" in evidence[0].excerpt

    state = runtime.memory_store.thread_state(session.session_id)
    assert state.last_turn_id == result.turn_id
    assert state.last_event_id
    assert state.failure_count == 0
    request_count = len(platform.requests)

    assert runtime.extract_memory_from_events(session.session_id) is None
    assert len(platform.requests) == request_count
    runtime.close()


def test_memory_usage_and_provenance_survive_consolidation(tmp_path):
    store = MemoryStore(tmp_path / "memory")
    workspace = tmp_path / "project"
    workspace.mkdir()

    from app.agent_runtime import MemoryCandidate, MemoryCategory, MemoryScope

    store.add_extraction(
        source_session_id="session-a",
        source_turn_id="turn-a",
        workspace=workspace,
        summary="test",
        candidates=(
            MemoryCandidate(
                text="This workspace uses pytest.",
                scope=MemoryScope.WORKSPACE,
                category=MemoryCategory.DECISION,
                importance=4,
                evidence="The user selected pytest.",
            ),
        ),
    )
    record = store.consolidate_pending()[0]
    assert record.usage_count == 0
    assert store.evidence(record.memory_id)[0].excerpt == "The user selected pytest."

    store.mark_used((record.memory_id,))
    updated = store.get(record.memory_id)
    assert updated is not None
    assert updated.usage_count == 1
    assert updated.last_used_at


def test_restart_backlog_processes_completed_unextracted_turn(tmp_path):
    workspace = tmp_path / "project"
    workspace.mkdir()

    runtime1, _ = _runtime(
        tmp_path,
        ScriptedPlatform([ModelResponse(text="done")]),
        auto_extract=False,
    )
    session = runtime1.create_session(AGENT_FAST_ROLE.role_id, workspace_dir=workspace)
    result = runtime1.start_turn(session.session_id, "Remember that this project uses pytest.")
    assert result.status is AgentStatus.COMPLETED
    runtime1.close()

    platform2 = ScriptedPlatform([ModelResponse(text=_memory_payload())])
    runtime2, _ = _runtime(tmp_path, platform2, auto_extract=True)
    status = runtime2.memory_status(session.session_id)
    assert status["auto_extract"] is True

    # Extraction stores the memory first and marks the thread afterwards, so the
    # durable state is what this assertion has to wait for. Waiting on the count
    # alone let the read land between those two writes.
    _wait_until(lambda: runtime2.memory_store.counts(workspace=workspace)["total"] == 1)
    _wait_until(
        lambda: runtime2.memory_store.thread_state(session.session_id).last_turn_id
        == result.turn_id
    )
    state = runtime2.memory_store.thread_state(session.session_id)
    assert state.last_turn_id == result.turn_id
    assert len(platform2.requests) == 1
    runtime2.close()


def test_background_extraction_does_not_block_next_foreground_turn(tmp_path):
    platform = BlockingExtractionPlatform()
    runtime, _ = _runtime(tmp_path, platform, auto_extract=True, idle_seconds=0.01)
    workspace = tmp_path / "project"
    workspace.mkdir()
    session = runtime.create_session(AGENT_FAST_ROLE.role_id, workspace_dir=workspace)

    first = runtime.start_turn(session.session_id, "Remember our pytest decision.")
    assert first.status is AgentStatus.COMPLETED
    assert platform.extraction_started.wait(timeout=2)

    outcome = {}
    finished = threading.Event()

    def run_second_turn():
        outcome["result"] = runtime.start_turn(
            session.session_id,
            "Do a new foreground task while memory is still extracting.",
        )
        finished.set()

    thread = threading.Thread(target=run_second_turn)
    thread.start()
    assert finished.wait(timeout=1), "foreground turn was blocked by background memory extraction"
    assert outcome["result"].status is AgentStatus.COMPLETED

    platform.release_extraction.set()
    thread.join(timeout=2)
    _wait_until(lambda: runtime.memory_store.counts(workspace=workspace)["total"] >= 1)
    runtime.close()


def test_existing_v1_memory_database_is_migrated_in_place(tmp_path):
    root = tmp_path / "legacy"
    root.mkdir()
    path = root / "state.db"
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE memory_extractions (
            extraction_id TEXT PRIMARY KEY,
            source_session_id TEXT NOT NULL,
            source_turn_id TEXT NOT NULL,
            summary TEXT NOT NULL,
            candidate_count INTEGER NOT NULL,
            usage_total_tokens INTEGER NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE memory_candidates (
            candidate_id TEXT PRIMARY KEY,
            extraction_id TEXT NOT NULL,
            source_session_id TEXT NOT NULL,
            source_turn_id TEXT NOT NULL,
            scope TEXT NOT NULL,
            scope_key TEXT NOT NULL,
            category TEXT NOT NULL,
            text TEXT NOT NULL,
            importance INTEGER NOT NULL,
            fingerprint TEXT NOT NULL,
            state TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE memories (
            memory_id TEXT PRIMARY KEY,
            scope TEXT NOT NULL,
            scope_key TEXT NOT NULL,
            category TEXT NOT NULL,
            text TEXT NOT NULL,
            importance INTEGER NOT NULL,
            fingerprint TEXT NOT NULL UNIQUE,
            source_count INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        """
    )
    connection.commit()
    connection.close()

    MemoryStore(root)

    connection = sqlite3.connect(path)
    memory_columns = {
        row[1] for row in connection.execute("PRAGMA table_info(memories)").fetchall()
    }
    candidate_columns = {
        row[1] for row in connection.execute("PRAGMA table_info(memory_candidates)").fetchall()
    }
    extraction_columns = {
        row[1] for row in connection.execute("PRAGMA table_info(memory_extractions)").fetchall()
    }
    connection.close()

    assert {"usage_count", "last_used_at", "confidence", "status", "last_verified_at"} <= memory_columns
    assert "evidence_excerpt" in candidate_columns
    assert {"source_start_event_id", "source_end_event_id"} <= extraction_columns
