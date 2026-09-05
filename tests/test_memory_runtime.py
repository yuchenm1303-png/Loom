from __future__ import annotations

import json

from app.agent_runtime import (
    AgentEventKind,
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
from app.agent_runtime.workspace_tools import loom_default_tools
from app.ai import AGENT_FAST_ROLE, AIMessage, MessageRole, ModelResponse, ModelUsage


class ScriptedPlatform:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def execute_chat(self, profile_id, request):
        self.requests.append((profile_id, request))
        if not self.responses:
            raise AssertionError("scripted platform ran out of responses")
        return self.responses.pop(0)


def _runtime(tmp_path, responses=()):
    store = FileAgentSessionStore(tmp_path / "state")
    platform = ScriptedPlatform(responses)
    runtime = AgentRuntime(
        platform=platform,
        store=store,
        tools=loom_default_tools(),
        sandbox_manager=SandboxManager(policy=SandboxPolicy.OFF),
    )
    return runtime, store, platform


def test_memory_extraction_filters_system_context_and_redacts_secrets(tmp_path):
    extraction_json = json.dumps(
        {
            "summary": "User prefers concise answers.",
            "memories": [
                {
                    "text": "The user prefers concise, direct answers.",
                    "scope": "global",
                    "category": "preference",
                    "importance": 5,
                },
                {
                    "text": "Project API_KEY=super-secret-value uses SQLite for durable state.",
                    "scope": "workspace",
                    "category": "project",
                    "importance": 4,
                },
            ],
        }
    )
    runtime, store, platform = _runtime(
        tmp_path,
        [ModelResponse(text=extraction_json, usage=ModelUsage(20, 10, 30))],
    )
    workspace = tmp_path / "project"
    workspace.mkdir()
    session = runtime.create_session(AGENT_FAST_ROLE.role_id, workspace_dir=workspace)
    session.status = AgentStatus.COMPLETED
    session.messages = [
        AIMessage(
            role=MessageRole.SYSTEM,
            name="loom_runtime_state",
            content="SYSTEM SECRET API_KEY=do-not-copy",
        ),
        AIMessage(
            role=MessageRole.USER,
            content="Please keep answers concise. API_KEY=user-secret-value",
        ),
        AIMessage(
            role=MessageRole.ASSISTANT,
            content="Understood. This project uses SQLite for durable state.",
        ),
    ]
    store.save(session)

    result = runtime.extract_memory_from_thread(session.session_id)

    assert result.extraction.candidate_count == 2
    assert len(result.consolidated) == 2
    extraction_request = platform.requests[0][1]
    supplied = extraction_request.messages[-1].content
    assert isinstance(supplied, str)
    assert "do-not-copy" not in supplied
    assert "user-secret-value" not in supplied
    assert "SYSTEM SECRET" not in supplied
    assert "[REDACTED]" in supplied
    records = runtime.list_memory(session.session_id)
    assert any("concise" in record.text for record in records)
    assert all("super-secret-value" not in record.text for record in records)
    assert any("[REDACTED]" in record.text for record in records)
    assert store.load(session.session_id).usage.total_tokens == 30
    events = store.events(session.session_id)
    assert any(event.kind is AgentEventKind.MEMORY_EXTRACTED for event in events)
    assert any(event.kind is AgentEventKind.MEMORY_CONSOLIDATED for event in events)
    runtime.close()


def test_relevant_memory_is_transiently_injected_into_later_model_request(tmp_path):
    runtime, store, platform = _runtime(tmp_path, [ModelResponse(text="done")])
    workspace = tmp_path / "project"
    workspace.mkdir()
    session = runtime.create_session(AGENT_FAST_ROLE.role_id, workspace_dir=workspace)
    runtime.memory_store.add_extraction(
        source_session_id=session.session_id,
        source_turn_id="seed",
        workspace=workspace,
        summary="seed",
        candidates=(
            MemoryCandidate(
                text="The user prefers concise, direct answers.",
                scope=MemoryScope.GLOBAL,
                category=MemoryCategory.PREFERENCE,
                importance=5,
            ),
        ),
    )
    runtime.memory_store.consolidate_pending()

    result = runtime.start_turn(session.session_id, "Please answer concisely about this change.")

    assert result.status is AgentStatus.COMPLETED
    request = platform.requests[0][1]
    memory_messages = [message for message in request.messages if message.name == "loom_memory"]
    assert len(memory_messages) == 1
    assert "prefers concise" in memory_messages[0].content
    loaded = store.load(session.session_id)
    assert all(message.name != "loom_memory" for message in loaded.messages)
    runtime.close()


def test_workspace_memory_is_isolated_but_global_memory_crosses_workspaces(tmp_path):
    runtime, _, _ = _runtime(tmp_path)
    workspace_a = tmp_path / "a"
    workspace_b = tmp_path / "b"
    workspace_a.mkdir()
    workspace_b.mkdir()
    session_a = runtime.create_session(AGENT_FAST_ROLE.role_id, workspace_dir=workspace_a)
    session_b = runtime.create_session(AGENT_FAST_ROLE.role_id, workspace_dir=workspace_b)
    runtime.memory_store.add_extraction(
        source_session_id=session_a.session_id,
        source_turn_id="seed",
        workspace=workspace_a,
        summary="seed",
        candidates=(
            MemoryCandidate(
                text="This workspace uses a custom migrations folder named db_migrate.",
                scope=MemoryScope.WORKSPACE,
                category=MemoryCategory.PROJECT,
                importance=4,
            ),
            MemoryCandidate(
                text="The user prefers short explanations.",
                scope=MemoryScope.GLOBAL,
                category=MemoryCategory.PREFERENCE,
                importance=5,
            ),
        ),
    )
    runtime.memory_store.consolidate_pending()

    a_results = runtime.search_memory(session_a.session_id, "migrations short explanations")
    b_results = runtime.search_memory(session_b.session_id, "migrations short explanations")

    assert any("db_migrate" in record.text for record in a_results)
    assert any("short explanations" in record.text for record in a_results)
    assert not any("db_migrate" in record.text for record in b_results)
    assert any("short explanations" in record.text for record in b_results)
    runtime.close()


def test_duplicate_candidates_consolidate_to_one_record_with_source_count(tmp_path):
    store = MemoryStore(tmp_path / "runtime")
    workspace = tmp_path / "project"
    workspace.mkdir()
    candidate = MemoryCandidate(
        text="Use pytest for the project test suite.",
        scope=MemoryScope.WORKSPACE,
        category=MemoryCategory.DECISION,
        importance=4,
    )
    for index in range(2):
        store.add_extraction(
            source_session_id=f"session-{index}",
            source_turn_id=f"turn-{index}",
            workspace=workspace,
            summary="",
            candidates=(candidate,),
        )
    records = store.consolidate_pending()
    listed = store.list_records(workspace=workspace)

    assert len(set(record.memory_id for record in records)) == 1
    assert len(listed) == 1
    assert listed[0].source_count == 2


def test_memory_store_survives_runtime_restart(tmp_path):
    workspace = tmp_path / "project"
    workspace.mkdir()
    runtime1, store1, _ = _runtime(tmp_path)
    session = runtime1.create_session(AGENT_FAST_ROLE.role_id, workspace_dir=workspace)
    runtime1.memory_store.add_extraction(
        source_session_id=session.session_id,
        source_turn_id="seed",
        workspace=workspace,
        summary="seed",
        candidates=(
            MemoryCandidate(
                text="The project architecture uses a durable Thread as the persistence boundary.",
                scope=MemoryScope.WORKSPACE,
                category=MemoryCategory.PROJECT,
                importance=5,
            ),
        ),
    )
    runtime1.memory_store.consolidate_pending()
    runtime1.close()

    runtime2 = AgentRuntime(
        platform=ScriptedPlatform([]),
        store=FileAgentSessionStore(tmp_path / "state"),
        tools=loom_default_tools(),
        sandbox_manager=SandboxManager(policy=SandboxPolicy.OFF),
    )
    records = runtime2.search_memory(session.session_id, "durable Thread persistence")

    assert len(records) == 1
    assert "durable Thread" in records[0].text
    assert store1.load(session.session_id).session_id == session.session_id
    runtime2.close()


def test_forget_memory_enforces_workspace_boundary_and_removes_candidate_copies(tmp_path):
    runtime, store, _ = _runtime(tmp_path)
    workspace_a = tmp_path / "a"
    workspace_b = tmp_path / "b"
    workspace_a.mkdir()
    workspace_b.mkdir()
    session_a = runtime.create_session(AGENT_FAST_ROLE.role_id, workspace_dir=workspace_a)
    session_b = runtime.create_session(AGENT_FAST_ROLE.role_id, workspace_dir=workspace_b)
    runtime.memory_store.add_extraction(
        source_session_id=session_a.session_id,
        source_turn_id="seed",
        workspace=workspace_a,
        summary="seed",
        candidates=(
            MemoryCandidate(
                text="Workspace A uses release branch stable-a.",
                scope=MemoryScope.WORKSPACE,
                category=MemoryCategory.DECISION,
                importance=5,
            ),
        ),
    )
    records = runtime.memory_store.consolidate_pending()
    memory_id = records[0].memory_id

    try:
        runtime.forget_memory(session_b.session_id, memory_id)
    except PermissionError as exc:
        assert "different workspace" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("a different workspace must not delete workspace memory")

    assert runtime.forget_memory(session_a.session_id, memory_id) is True
    assert runtime.memory_store.get(memory_id) is None
    assert runtime.memory_store.consolidate_pending() == ()
    assert not runtime.search_memory(session_a.session_id, "stable-a")
    assert any(
        event.kind is AgentEventKind.MEMORY_FORGOTTEN
        for event in store.events(session_a.session_id)
    )
    runtime.close()
