from __future__ import annotations

from app.ai import AIMessage, ChatRequest, MessageRole, ModelResponse, ModelUsage, ToolChoice

from .context_compaction import SUMMARIZATION_PROMPT, build_compacted_history, summarization_prompt
from .context_state import (
    ContextCheckpoint,
    ContextCheckpointStore,
    WorldStateEnvelope,
    build_world_state_envelope,
)
from .contracts import AgentEventKind, AgentRunResult, AgentSession, AgentStatus
from .history import HistoryRepair, repair_tool_history
from .instructions import AppliedInstructionCache
from .response_language import communication_language_message, infer_user_language
from .runtime import CancellationToken
from typing import Any
from .continuity import (_latest_captured_step, _recent_durable_evidence,
                         _reference_message, _reference_payload, _fit_reference_without_breaking_budget)
from .compaction_fallback import _invalid_compaction_error, build_deterministic_compaction_summary
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
        from .context_tools import new_context_tool
        if self.tools.get("new_context") is None:
            self.tools.register(new_context_tool())
        self.checkpoint_store = checkpoint_store or ContextCheckpointStore(self.store.root)
        # Codex keeps one applied repository-instruction snapshot while the
        # environment/trust selection is unchanged. Loom currently exposes the
        # resolved workspace as that key. This cache is intentionally in-memory;
        # restart recovery belongs to the owner that restores the entire Step.
        self.instruction_loader = AppliedInstructionCache(self.instruction_loader)

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
        self: Any,
        session: Any,
        *,
        summary: str,
        repaired: Any,
        archived: tuple[Any, ...],
        retained: tuple[Any, ...],
        summary_source: str,
        summary_usage: Any | None = None,
        replacement_override: tuple[Any, ...] | None = None,
    ) -> Any:
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

        mid_turn = (
            str(summary_source) == "auto"
            and getattr(getattr(session, "status", None), "value", "") == "running"
        )
        step = _latest_captured_step(self, session) if mid_turn else None
        if step is None:
            # Standalone/manual compaction and defensive fallback both use a
            # non-authorizing snapshot. Normal mid-turn auto compaction always
            # has its already-captured request Step available here.
            step = self._build_step_context(session, next_model_step=False)
        envelope = self._context_envelope(session, step)

        from app.agent_runtime.context_budget import estimate_tokens
        from app.agent_runtime import context_compaction as compaction

        replacement = (
            tuple(replacement_override)
            if replacement_override is not None
            else compaction.build_compacted_history(
                canonical_before,
                text,
                token_counter=lambda messages: estimate_tokens(messages),
            )
        )
        if not replacement:
            raise ValueError("context checkpoint replacement must not be empty")

        reference_payload: dict[str, object] | None = None
        reference_injected = False
        if mid_turn and replacement_override is not None:
            durable_evidence = _recent_durable_evidence(self, session)
            reference = _reference_message(
                step,
                envelope,
                durable_evidence=durable_evidence,
            )
            replacement, reference_injected = _fit_reference_without_breaking_budget(
                self,
                session,
                step,
                envelope,
                communication_language,
                replacement,
                reference,
                compaction,
            )
            if reference_injected:
                reference_payload = _reference_payload(
                    step,
                    envelope,
                    durable_evidence=durable_evidence,
                )

        # A checkpoint is useful only if its replacement leaves an operable
        # request. Prefer more headroom by dropping the oldest retained user
        # messages, but keep the newest user request and the handoff summary.
        context_after = _compacted_context_record(
            self, session, step, envelope, communication_language, replacement
        )
        limits_after = context_after["context_limits"]
        hard_budget = int(limits_after["input_budget_tokens"])
        auto_limit = int(limits_after["auto_compact_token_limit"])
        safety = int(limits_after["safety_tokens"])
        # Some test/provider profiles intentionally use a tiny explicit trigger
        # to request immediate compaction. It is not a feasible post-compaction
        # target; the actual model input budget remains the hard constraint.
        target = hard_budget - safety
        if auto_limit >= hard_budget // 2:
            target = min(target, auto_limit * 4 // 5)
        target = max(1, target)
        while int(context_after["calibrated_input_tokens_after"]) > target:
            real_users = [
                index for index, message in enumerate(replacement)
                if compaction.is_real_user_message(message)
            ]
            if len(real_users) <= 1:
                break
            oldest = real_users[0]
            replacement = tuple(message for index, message in enumerate(replacement) if index != oldest)
            context_after = _compacted_context_record(
                self, session, step, envelope, communication_language, replacement
            )
        if int(context_after["calibrated_input_tokens_after"]) > hard_budget:
            from app.agent_runtime.context_budget import ContextBudgetExceeded

            raise ContextBudgetExceeded(
                estimated_tokens=int(context_after["calibrated_input_tokens_after"]),
                input_budget_tokens=hard_budget,
                tool_schema_tokens=int(context_after["tool_schema_tokens"]),
                message_count=int(context_after["message_count"]),
                reason="compacted history still exceeds the model input budget",
            )

        retained_message_count = sum(
            1 for message in replacement if compaction.is_real_user_message(message)
        )
        checkpoint = self.checkpoint_store.create(
            session_id=session.session_id,
            summary=text,
            archived_messages=canonical_before,
            retained_message_count=retained_message_count,
            world_state_digest=envelope.digest,
        )
        from .context_composer import compact_frames
        compact_frames(self, session, len(replacement))
        session.messages = list(replacement)
        if summary_usage is not None:
            session.usage = _add_usage(session.usage, summary_usage)
        self._emit_event(
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
                "context_after_compaction": context_after,
                "compaction_phase": "mid_turn" if mid_turn else "standalone",
                "continuity_reference_injected": reference_injected,
                "continuity_reference": reference_payload,
                "communication_language": communication_language,
                "summary_usage": (
                    {
                        "input_tokens": summary_usage.input_tokens,
                        "output_tokens": summary_usage.output_tokens,
                        "total_tokens": summary_usage.total_tokens,
                        "cached_input_tokens": summary_usage.cached_input_tokens,
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

    def _compact_context_model_attempt(
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

            from .context_composer import capture_context, stable_prefix, render_request
            step = self._build_step_context(session, next_model_step=False)
            capture_context(self, session, step)
            request_messages = render_request(self, session, stable_prefix(self, session, step), archived)
            request_messages.append(AIMessage(role=MessageRole.USER,
                content=summarization_prompt(communication_language)))
            request = ChatRequest(
                messages=tuple(request_messages),
                tools=(),
                tool_choice=ToolChoice.NONE,
                max_output_tokens=(step.request_state.context_limits.output_reserve_tokens
                    if step.request_state.context_limits.output_reserve_declared else None),
                session_id=session.session_id,
            )
            response = self.platform_for_session(session.session_id).execute_chat(
                session.profile_id,
                request,
            )
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
        """Return transient base/runtime context from the captured request world."""
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

    def compact_context_with_model(self: Any, session_id: str, *, keep_recent: int = 24):
        invalid: RuntimeError | None = None
        attempts = max(1, int(self.limits.model_retries) + 1)
        for _ in range(attempts):
            try:
                return self._compact_context_model_attempt(session_id, keep_recent=keep_recent)
            except RuntimeError as exc:
                if not _invalid_compaction_error(exc):
                    raise
                invalid = exc

        lock = self._session_lock(session_id)
        with lock:
            session = self.store.load(session_id)
            if session.status in {AgentStatus.RUNNING, AgentStatus.WAITING_APPROVAL}:
                raise RuntimeError("cannot compact context while a turn is active")
            repaired, archived, retained = self._prepare_compaction_locked(
                session,
                keep_recent=keep_recent,
            )
            summary = build_deterministic_compaction_summary(tuple((*archived, *retained)))
            checkpoint = self._commit_compaction_locked(
                session,
                summary=summary,
                repaired=repaired,
                archived=archived,
                retained=retained,
                summary_source="model_fallback",
            )
        _ = invalid
        return checkpoint


def _add_usage(left: ModelUsage, right: ModelUsage) -> ModelUsage:
    return ModelUsage(
        input_tokens=left.input_tokens + right.input_tokens,
        output_tokens=left.output_tokens + right.output_tokens,
        total_tokens=left.total_tokens + right.total_tokens,
        cached_input_tokens=left.cached_input_tokens + right.cached_input_tokens,
    )


__all__ = ["ContextAgentRuntime"]


def _compacted_context_record(
    runtime: Any,
    session: Any,
    step: Any,
    envelope: Any,
    communication_language: str,
    replacement: tuple[Any, ...],
) -> dict[str, Any]:
    from app.ai import AIMessage, MessageRole
    from app.agent_runtime.context_budget import estimate_tokens, estimate_tool_schema_tokens
    from app.agent_runtime.context_limits import resolve_context_limits

    request_state = getattr(step, "request_state", None)
    captured = bool(getattr(request_state, "captured", False))
    from .context_composer import stable_prefix, render_request
    transient = stable_prefix(runtime, session, step)
    tools = step.tool_router.definitions()
    limits = (
        request_state.context_limits
        if captured and request_state.context_limits is not None
        else resolve_context_limits(runtime, session)
    )
    visible = render_request(runtime, session, transient, replacement, replacement=True)
    estimated = estimate_tokens(visible, tools)
    return {
        "context_limits": limits.as_dict(),
        "estimated_input_tokens_after": estimated,
        "calibrated_input_tokens_after": estimated,
        "active_context_tokens": estimated,
        "token_accounting_source": "post_compaction_estimate",
        "tool_schema_tokens": estimate_tool_schema_tokens(tools),
        "message_count": len(visible),
        "tool_outputs_reduced": 0,
        "tool_outputs_collapsed": 0,
        "user_messages_truncated": 0,
    }
