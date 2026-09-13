from __future__ import annotations

from app.ai import AIMessage, ChatRequest, MessageRole, ModelResponse, ModelUsage, ToolChoice

from .context_compaction import SUMMARIZATION_PROMPT, build_compacted_history
from .context_state import (
    ContextCheckpoint,
    ContextCheckpointStore,
    WorldStateEnvelope,
    build_world_state_envelope,
)
from .contracts import AgentEventKind, AgentRunResult, AgentSession, AgentStatus
from .history import HistoryRepair, repair_tool_history
from .instructions import ProjectInstructionSnapshotStore, TurnScopedInstructionLoader
from .response_language import communication_language_message, infer_user_language
from .runtime import CancellationToken
from .sandbox_runtime import SandboxAgentRuntime


# Backwards-compatible import name. The content is Codex's current compaction
# user prompt; it is no longer injected as a Loom-authored system instruction.
_COMPACTION_SYSTEM_PROMPT = SUMMARIZATION_PROMPT


class ContextAgentRuntime(SandboxAgentRuntime):
    """Sandbox/durable runtime with Codex-compatible model-window checkpoints.

    Loom keeps its durable checkpoint archive and world-state product features,
    but compaction replaces only the active model window. The replacement window
    contains retained real user messages plus a contextual-user summary, matching
    Codex rather than promoting the summary to system instructions.
    """

    def __init__(self, *args, checkpoint_store: ContextCheckpointStore | None = None, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.checkpoint_store = checkpoint_store or ContextCheckpointStore(self.store.root)
        self.instruction_snapshot_store = ProjectInstructionSnapshotStore(self.store.root)
        self.instruction_loader = TurnScopedInstructionLoader(
            self.instruction_loader,
            self.instruction_snapshot_store,
        )

    def _goal_payload(self, session_id: str) -> dict[str, object] | None:
        try:
            goal = self.get_goal(session_id)
        except Exception:
            return None
        if goal is None:
            return None
        return {
            "objective": goal.objective,
            "status": goal.status.value,
            "token_budget": goal.token_budget,
            "tokens_used": goal.tokens_used,
        }

    def _context_envelope(self, session: AgentSession, step) -> WorldStateEnvelope:
        try:
            queue_pending = len(self.list_queued_turns(session.session_id))
        except Exception:
            queue_pending = 0
        diff = self.diff_trackers.snapshot(session.session_id, session.current_turn_id)
        return build_world_state_envelope(
            step,
            goal=self._goal_payload(session.session_id),
            queue_pending=queue_pending,
            diff_revision=diff.revision,
            changed_paths=diff.paths,
        )

    def _prepare_compaction_locked(
        self,
        session: AgentSession,
        *,
        keep_recent: int,
    ) -> tuple[HistoryRepair, tuple[AIMessage, ...], tuple[AIMessage, ...]]:
        """Return the complete canonical model window to compact.

        ``keep_recent`` remains in the public Loom API for compatibility but is
        intentionally not used to retain an arbitrary assistant/tool suffix.
        Codex rebuilds compacted history from real user messages plus the summary.
        """
        _ = keep_recent
        repaired = repair_tool_history(
            session.messages,
            max_tool_result_chars=self.limits.max_tool_result_chars,
        )
        messages = tuple(repaired.messages)
        if not messages:
            raise ValueError("not enough safely compactable history")
        return repaired, messages, ()

    def _commit_compaction_locked(
        self,
        session: AgentSession,
        *,
        summary: str,
        repaired: HistoryRepair,
        archived: tuple[AIMessage, ...],
        retained: tuple[AIMessage, ...],
        summary_source: str,
        summary_usage: ModelUsage | None = None,
    ) -> ContextCheckpoint:
        text = str(summary or "").strip()
        if not text:
            raise ValueError("context summary must not be empty")

        canonical_before = tuple((*archived, *retained))
        if not canonical_before:
            raise ValueError("context checkpoint must archive canonical history")
        communication_language = infer_user_language(
            canonical_before,
            fallback=session.communication_language,
        )
        session.communication_language = communication_language
        step = self._build_step_context(session, next_model_step=False)
        envelope = self._context_envelope(session, step)

        from .context_budget import estimate_tokens

        replacement = build_compacted_history(
            canonical_before,
            text,
            token_counter=lambda messages: estimate_tokens(messages),
        )
        checkpoint = self.checkpoint_store.create(
            session_id=session.session_id,
            summary=text,
            archived_messages=canonical_before,
            retained_message_count=max(0, len(replacement) - 1),
            world_state_digest=envelope.digest,
        )
        session.messages = list(replacement)
        if summary_usage is not None:
            session.usage = _add_usage(session.usage, summary_usage)
        self._record(
            session,
            AgentEventKind.CONTEXT_CHECKPOINTED,
            data={
                "checkpoint_id": checkpoint.checkpoint_id,
                "archived_messages": checkpoint.archived_message_count,
                "retained_messages": checkpoint.retained_message_count,
                "replacement_messages": len(replacement),
                "world_state_digest": checkpoint.world_state_digest,
                "history_repaired": repaired.changed,
                "summary_source": summary_source,
                "communication_language": communication_language,
                "summary_usage": (
                    {
                        "input_tokens": summary_usage.input_tokens,
                        "output_tokens": summary_usage.output_tokens,
                        "total_tokens": summary_usage.total_tokens,
                    }
                    if summary_usage is not None
                    else None
                ),
            },
        )
        return checkpoint

    def compact_context(
        self,
        session_id: str,
        summary: str,
        *,
        keep_recent: int = 24,
    ) -> ContextCheckpoint:
        lock = self._session_lock(session_id)
        with lock:
            session = self.store.load(session_id)
            if session.status in {AgentStatus.RUNNING, AgentStatus.WAITING_APPROVAL}:
                raise RuntimeError("cannot compact context while a turn is active")
            repaired, archived, retained = self._prepare_compaction_locked(
                session,
                keep_recent=keep_recent,
            )
            return self._commit_compaction_locked(
                session,
                summary=summary,
                repaired=repaired,
                archived=archived,
                retained=retained,
                summary_source="caller",
            )

    def compact_context_with_model(
        self,
        session_id: str,
        *,
        keep_recent: int = 24,
    ) -> ContextCheckpoint:
        """Run a standalone manual compaction task against canonical history."""
        lock = self._session_lock(session_id)
        with lock:
            session = self.store.load(session_id)
            if session.status in {AgentStatus.RUNNING, AgentStatus.WAITING_APPROVAL}:
                raise RuntimeError("cannot compact context while a turn is active")
            repaired, archived, retained = self._prepare_compaction_locked(
                session,
                keep_recent=keep_recent,
            )
            communication_language = infer_user_language(
                archived,
                fallback=session.communication_language,
            )
            session.communication_language = communication_language

            request_messages: list[AIMessage] = [
                AIMessage(role=MessageRole.SYSTEM, content=session.system_prompt)
            ]
            project_instructions = self.instruction_loader.load(session.workspace_dir)
            if project_instructions:
                request_messages.append(
                    AIMessage(
                        role=MessageRole.USER,
                        name="loom_project_instructions",
                        content=project_instructions,
                    )
                )
            request_messages.extend(archived)
            request_messages.append(
                AIMessage(role=MessageRole.USER, content=SUMMARIZATION_PROMPT)
            )
            request = ChatRequest(
                messages=tuple(request_messages),
                tools=(),
                tool_choice=ToolChoice.NONE,
                max_output_tokens=self.limits.output_reserve_tokens,
            )
            response = self.platform.execute_chat(session.profile_id, request)
            if not isinstance(response, ModelResponse):
                raise TypeError("agent model platform must return ModelResponse")
            if response.tool_calls:
                raise RuntimeError("context compaction model returned unexpected tool calls")
            summary = str(response.text or "").strip()
            if not summary:
                raise RuntimeError("context compaction model returned an empty summary")
            return self._commit_compaction_locked(
                session,
                summary=summary,
                repaired=repaired,
                archived=archived,
                retained=retained,
                summary_source="model",
                summary_usage=response.usage,
            )

    def list_context_checkpoints(self, session_id: str) -> tuple[ContextCheckpoint, ...]:
        self.store.load(session_id)
        return self.checkpoint_store.list(session_id)

    def _request_context_messages(
        self,
        session: AgentSession,
        step,
        envelope: WorldStateEnvelope,
    ) -> tuple[AIMessage, ...]:
        """Return transient base/runtime context for one model sampling request."""
        _ = step
        return (
            AIMessage(role=MessageRole.SYSTEM, content=session.system_prompt),
            AIMessage(
                role=MessageRole.SYSTEM,
                name="loom_runtime_state",
                content=envelope.text,
            ),
            communication_language_message(
                session.messages,
                fallback=session.communication_language,
            ),
        )

    def _prepare_model_request(self, session, step, token):
        from .context_budget import prepare_context

        with self.instruction_loader.bind_turn(
            session_id=session.session_id,
            turn_id=session.current_turn_id,
            workspace=session.workspace_dir,
        ):
            return prepare_context(self, session, step, token)


def _add_usage(left: ModelUsage, right: ModelUsage) -> ModelUsage:
    return ModelUsage(
        input_tokens=left.input_tokens + right.input_tokens,
        output_tokens=left.output_tokens + right.output_tokens,
        total_tokens=left.total_tokens + right.total_tokens,
    )


__all__ = ["ContextAgentRuntime"]
