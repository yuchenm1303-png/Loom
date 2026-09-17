from __future__ import annotations

import json

from app.agent_continuity_contract import COMPACTION_REFERENCE_MESSAGE_NAME
from app.agent_runtime import (
    AgentStatus,
    ContextAgentRuntime,
    FileAgentSessionStore,
    repair_tool_history,
)
from app.agent_runtime.context_budget import estimate_tokens
from app.agent_runtime.context_compaction import (
    COMPACTION_MESSAGE_NAME,
    build_compacted_history,
    is_real_user_message,
)
from app.ai import AIMessage, MessageRole


class NoCallPlatform:
    def execute_chat(self, profile_id, request):  # pragma: no cover - compaction is committed directly
        raise AssertionError("model call was not expected")


def _runtime(tmp_path):
    return ContextAgentRuntime(
        platform=NoCallPlatform(),
        store=FileAgentSessionStore(tmp_path / "state"),
    )


def test_midturn_compaction_keeps_same_turn_reference_and_summary_last(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    runtime = _runtime(tmp_path)
    session = runtime.create_session("agent.fast", workspace_dir=project)
    session.current_turn_id = "turn-stays-the-same"
    session.status = AgentStatus.RUNNING
    session.messages = [
        AIMessage(role=MessageRole.USER, content="resolve the remaining merge conflicts"),
        AIMessage(role=MessageRole.ASSISTANT, content="checked git status and resolved four files"),
        AIMessage(role=MessageRole.USER, content="continue"),
    ]
    runtime.store.save(session)

    captured = runtime._capture_step_context(session, next_model_step=True, step_id="step-before-compact")
    repair = repair_tool_history(session.messages)
    replacement = build_compacted_history(
        tuple(repair.messages),
        "Four conflicts are already resolved. Continue with the two remaining files; do not redo git inspection.",
        token_counter=lambda messages: estimate_tokens(messages),
    )

    checkpoint = runtime._commit_compaction_locked(
        session,
        summary="Four conflicts are already resolved. Continue with the two remaining files; do not redo git inspection.",
        repaired=repair,
        archived=tuple(repair.messages),
        retained=(),
        summary_source="auto",
        replacement_override=replacement,
    )

    names = [str(message.name or "") for message in session.messages]
    assert names[-1] == COMPACTION_MESSAGE_NAME
    assert names.count(COMPACTION_REFERENCE_MESSAGE_NAME) == 1
    reference_index = names.index(COMPACTION_REFERENCE_MESSAGE_NAME)
    last_real_user_index = max(
        index for index, message in enumerate(session.messages) if is_real_user_message(message)
    )
    assert reference_index < last_real_user_index

    reference = session.messages[reference_index]
    marker, _, raw = str(reference.content).partition("\n")
    assert marker == "LOOM_MID_TURN_REFERENCE v1"
    payload = json.loads(raw.split("\n")[-1])
    assert payload["identity"]["turn_id"] == "turn-stays-the-same"
    assert payload["identity"]["step_id"] == captured.step_id
    assert payload["state_digest"] == checkpoint.world_state_digest
    assert payload["kind"] == "mid_turn_compaction_reference"

    events = runtime.store.events(session.session_id)
    compacted = [event for event in events if event.kind.value == "context_checkpointed"][-1]
    assert compacted.data["compaction_phase"] == "mid_turn"
    assert compacted.data["continuity_reference_injected"] is True


def test_compaction_reference_is_not_promoted_to_real_user_history(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    runtime = _runtime(tmp_path)
    session = runtime.create_session("agent.fast", workspace_dir=project)
    session.current_turn_id = "turn-1"
    session.status = AgentStatus.RUNNING
    session.messages = [AIMessage(role=MessageRole.USER, content="original task")]
    runtime.store.save(session)
    runtime._capture_step_context(session, next_model_step=True, step_id="step-1")
    repair = repair_tool_history(session.messages)
    replacement = build_compacted_history(
        tuple(repair.messages),
        "continue from the current point",
        token_counter=lambda messages: estimate_tokens(messages),
    )
    runtime._commit_compaction_locked(
        session,
        summary="continue from the current point",
        repaired=repair,
        archived=tuple(repair.messages),
        retained=(),
        summary_source="auto",
        replacement_override=replacement,
    )

    reference = next(
        message for message in session.messages if message.name == COMPACTION_REFERENCE_MESSAGE_NAME
    )
    assert is_real_user_message(reference) is False

    next_replacement = build_compacted_history(
        tuple(session.messages),
        "second checkpoint",
        token_counter=lambda messages: estimate_tokens(messages),
    )
    assert not any(
        message.name == COMPACTION_REFERENCE_MESSAGE_NAME for message in next_replacement
    )
    assert next_replacement[-1].name == COMPACTION_MESSAGE_NAME
