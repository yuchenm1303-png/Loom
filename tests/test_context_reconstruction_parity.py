from __future__ import annotations

from app.agent_runtime.context_compaction import SUMMARY_PREFIX
from app.agent_runtime.context_state import ContextCheckpoint
from app.ai import AIMessage, MessageRole


def test_reconstructed_checkpoint_summary_keeps_contextual_user_precedence():
    checkpoint = ContextCheckpoint(
        checkpoint_id="ctx-test",
        session_id="session-test",
        created_at="2026-09-13T00:00:00Z",
        summary="Continue from the compacted state.",
        archived_messages=(AIMessage(role=MessageRole.USER, content="original"),),
        retained_message_count=1,
        world_state_digest="digest",
    )

    message = checkpoint.summary_message()

    assert message.role is MessageRole.USER
    assert message.name == "loom_compaction"
    assert str(message.content).startswith(SUMMARY_PREFIX)
    assert "Continue from the compacted state." in str(message.content)


def test_checkpoint_archive_and_model_projection_are_distinct_objects():
    archived = (
        AIMessage(role=MessageRole.USER, content="request"),
        AIMessage(role=MessageRole.ASSISTANT, content="answer"),
    )
    checkpoint = ContextCheckpoint(
        checkpoint_id="ctx-test-2",
        session_id="session-test",
        created_at="2026-09-13T00:00:00Z",
        summary="summary",
        archived_messages=archived,
        retained_message_count=1,
        world_state_digest="digest",
    )

    projected = checkpoint.summary_message()

    assert checkpoint.archived_messages == archived
    assert projected not in checkpoint.archived_messages
    assert checkpoint.archived_messages[1].role is MessageRole.ASSISTANT
    assert projected.role is MessageRole.USER
