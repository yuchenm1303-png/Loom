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
from .runtime import CancellationToken
from .sandbox_runtime import SandboxAgentRuntime


_COMPACTION_SYSTEM_PROMPT = (
    "You are compacting earlier canonical conversation history for a continuing Loom agent thread. "
    "Return a concise plain-text summary that preserves user goals, constraints, decisions, important facts, "
    "files or symbols touched, tool outcomes, errors, and unresolved work. Distinguish observed tool results "
    "from proposals. Do not invent facts and do not include private chain-of-thought."
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
            request = ChatRequest(
                messages=(
                    AIMessage(role=MessageRole.SYSTEM, content=_COMPACTION_SYSTEM_PROMPT),
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
        # Validate the Loom session before exposing checkpoint storage.
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
        _ = step
        return (
            AIMessage(role=MessageRole.SYSTEM, content=session.system_prompt),
            AIMessage(
                role=MessageRole.SYSTEM,
                name="loom_runtime_state",
                content=envelope.text,
            ),
        )

    def _drive(self, session: AgentSession, token: CancellationToken) -> AgentRunResult:
        try:
            while True:
                if self._cancel_if_requested(session, token):
                    return self._result(session)
                if session.pending_tool_calls:
                    if not self._process_pending_tools(session, token):
                        return self._result(session)
                if session.model_steps >= self.limits.max_model_steps:
                    return self._limit(session, "model step limit reached")

                step = self._build_step_context(session, next_model_step=True)
                envelope = self._context_envelope(session, step)
                messages = [
                    *self._request_context_messages(session, step, envelope),
                    *session.messages,
                ]
                if len(messages) > self.limits.max_messages:
                    return self._limit(
                        session,
                        "context message limit reached; run a context compaction task before continuing",
                    )

                self._record(
                    session,
                    AgentEventKind.MODEL_REQUESTED,
                    data={
                        "profile_id": session.profile_id,
                        "step": step.model_step,
                        "step_id": step.step_id,
                        "message_count": len(messages),
                        "tool_count": len(step.tool_router.all()),
                        "permission_mode": step.world_state.permission_mode.value,
                        "context_digest": envelope.digest,
                    },
                )
                response = self.platform.execute_chat(
                    session.profile_id,
                    ChatRequest(
                        messages=tuple(messages),
                        tools=step.tool_router.definitions(),
                        tool_choice=ToolChoice.AUTO,
                    ),
                )
                if not isinstance(response, ModelResponse):
                    raise TypeError("agent model platform must return ModelResponse")
                if not response.text and not response.tool_calls:
                    raise RuntimeError("agent model response contained neither text nor tool calls")
                if self._cancel_if_requested(session, token):
                    return self._result(session)

                session.model_steps += 1
                session.usage = _add_usage(session.usage, response.usage)
                assistant = AIMessage(
                    role=MessageRole.ASSISTANT,
                    content=response.text,
                    tool_calls=response.tool_calls,
                )
                session.messages.append(assistant)
                self._record(
                    session,
                    AgentEventKind.MODEL_RESPONSE,
                    data={
                        "step_id": step.step_id,
                        "text": response.text,
                        "finish_reason": response.finish_reason,
                        "response_id": response.response_id,
                        "tool_calls": [
                            {"call_id": call.call_id, "name": call.name, "arguments": call.arguments}
                            for call in response.tool_calls
                        ],
                        "usage": {
                            "input_tokens": response.usage.input_tokens,
                            "output_tokens": response.usage.output_tokens,
                            "total_tokens": response.usage.total_tokens,
                        },
                    },
                )

                if response.tool_calls:
                    session.tool_calls += len(response.tool_calls)
                    if session.tool_calls > self.limits.max_tool_calls:
                        return self._limit(session, "tool call limit reached")
                    session.pending_tool_calls.extend(response.tool_calls)
                    session.pending_step_id = step.step_id
                    for call in response.tool_calls:
                        self._record(
                            session,
                            AgentEventKind.TOOL_REQUESTED,
                            data={
                                "call_id": call.call_id,
                                "tool": call.name,
                                "arguments": call.arguments,
                                "step_id": step.step_id,
                            },
                        )
                    if not self._process_pending_tools(session, token, step=step):
                        return self._result(session)
                    continue

                session.status = AgentStatus.COMPLETED
                session.final_text = response.text
                session.error = ""
                diff = self.diff_trackers.snapshot(session.session_id, session.current_turn_id)
                self._record(
                    session,
                    AgentEventKind.TURN_COMPLETED,
                    data={
                        "text": response.text,
                        "diff_revision": diff.revision,
                        "changed_paths": list(diff.paths),
                    },
                )
                return self._result(session)
        except Exception as exc:
            session.status = AgentStatus.FAILED
            session.error = f"{type(exc).__name__}: {exc}"
            self._record(session, AgentEventKind.TURN_FAILED, data={"error": session.error})
            return self._result(session)


def _add_usage(left: ModelUsage, right: ModelUsage) -> ModelUsage:
    return ModelUsage(
        input_tokens=left.input_tokens + right.input_tokens,
        output_tokens=left.output_tokens + right.output_tokens,
        total_tokens=left.total_tokens + right.total_tokens,
    )


__all__ = ["ContextAgentRuntime"]
