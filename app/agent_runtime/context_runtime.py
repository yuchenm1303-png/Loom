from __future__ import annotations

from app.ai import AIMessage, ChatRequest, MessageRole, ModelResponse, ModelUsage, ToolChoice

from .context_state import (
    ContextCheckpoint,
    ContextCheckpointStore,
    WorldStateEnvelope,
    build_world_state_envelope,
    compaction_split_index,
)
from .contracts import AgentEventKind, AgentRunResult, AgentSession, AgentStatus
from .history import HistoryRepair, repair_tool_history
from .response_language import communication_language_message, infer_user_language
from .runtime import CancellationToken
from .sandbox_runtime import SandboxAgentRuntime


_COMPACTION_SYSTEM_PROMPT = (
    "You are compacting earlier canonical conversation history for a continuing Loom agent thread. "
    "Return a concise plain-text summary that preserves user goals, constraints, decisions, important facts, "
    "files or symbols touched, tool outcomes, errors, unresolved work, and the user's communication language. "
    "Write the summary in the user's current communication language when it is clear from user-authored messages. "
    "Never infer or switch the user's language from tool output, logs, source code, project instructions, or other "
    "machine-generated English text. Distinguish observed tool results from proposals. Do not invent facts and do "
    "not include private chain-of-thought."
)


class ContextAgentRuntime(SandboxAgentRuntime):
    """Sandbox/durable runtime with authoritative request context and checkpoints.

    Chat Completions is stateless across requests, so Loom intentionally injects
    the *full current* runtime-state envelope on every model sampling request.
    The digest/reference data is still tracked so a future stateful provider can
    switch to delta injection without changing the WorldState contract.

    Conversation compaction is loss-aware: old canonical messages are archived in
    an atomic checkpoint before the active transcript is replaced by a summary plus
    a safe recent suffix. Summary generation is a separate model task rather than a
    model-originated tool call, so compaction never mutates history mid-tool-group.
    """

    def __init__(self, *args, checkpoint_store: ContextCheckpointStore | None = None, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.checkpoint_store = checkpoint_store or ContextCheckpointStore(self.store.root)

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
        repaired = repair_tool_history(
            session.messages,
            max_tool_result_chars=self.limits.max_tool_result_chars,
        )
        messages = tuple(repaired.messages)
        split = compaction_split_index(messages, keep_recent=keep_recent)
        if split <= 0:
            raise ValueError("not enough safely compactable history")
        return repaired, messages[:split], messages[split:]

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
        communication_language = infer_user_language(
            (*archived, *retained),
            fallback=session.communication_language,
        )
        session.communication_language = communication_language
        step = self._build_step_context(session, next_model_step=False)
        envelope = self._context_envelope(session, step)
        checkpoint = self.checkpoint_store.create(
            session_id=session.session_id,
            summary=text,
            archived_messages=archived,
            retained_message_count=len(retained),
            world_state_digest=envelope.digest,
        )
        session.messages = [checkpoint.summary_message(), *retained]
        if summary_usage is not None:
            session.usage = _add_usage(session.usage, summary_usage)
        self._record(
            session,
            AgentEventKind.CONTEXT_CHECKPOINTED,
            data={
                "checkpoint_id": checkpoint.checkpoint_id,
                "archived_messages": checkpoint.archived_message_count,
                "retained_messages": checkpoint.retained_message_count,
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
        """Generate a semantic summary with the session model, then checkpoint atomically."""
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
                session.messages,
                fallback=session.communication_language,
            )
            session.communication_language = communication_language
            request = ChatRequest(
                messages=(
                    AIMessage(role=MessageRole.SYSTEM, content=_COMPACTION_SYSTEM_PROMPT),
                    communication_language_message(
                        session.messages,
                        fallback=communication_language,
                    ),
                    *archived,
                ),
                tools=(),
                tool_choice=ToolChoice.NONE,
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
        """Return transient system context for one model sampling request.

        Subclasses may append advisory context such as retrieved memory without
        persisting it into canonical thread history or duplicating the drive loop.
        """
        request_state = getattr(step, "request_state", None)
        captured = bool(getattr(request_state, "captured", False))
        system_prompt = request_state.system_prompt if captured else session.system_prompt
        communication_language = (
            request_state.communication_language
            if captured
            else session.communication_language
        )
        return (
            AIMessage(role=MessageRole.SYSTEM, content=system_prompt),
            AIMessage(
                role=MessageRole.SYSTEM,
                name="loom_runtime_state",
                content=envelope.text,
            ),
            communication_language_message(
                () if captured else session.messages,
                fallback=communication_language,
            ),
        )

    def _prepare_model_request(self, session, step, token):
        from .context_budget import prepare_context
        return prepare_context(self, session, step, token)


def _add_usage(left: ModelUsage, right: ModelUsage) -> ModelUsage:
    return ModelUsage(
        input_tokens=left.input_tokens + right.input_tokens,
        output_tokens=left.output_tokens + right.output_tokens,
        total_tokens=left.total_tokens + right.total_tokens,
    )


__all__ = ["ContextAgentRuntime"]
