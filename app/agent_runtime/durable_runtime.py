from __future__ import annotations

import json
import uuid
from pathlib import Path

from app.ai import AIMessage, MessageRole

from .contracts import AgentEvent, AgentEventKind, AgentRunResult, AgentSession, AgentStatus
from .durable_state import DurableThreadStateStore, GoalStatus, QueuedTurn, ThreadGoal
from .history import HistoryRepair, repair_tool_history
from .runtime import AgentRuntime as CoreAgentRuntime
from .storage import utc_now


class DurableAgentRuntime(CoreAgentRuntime):
    """AgentRuntime with cross-turn goal, queue, and crash-recovery semantics.

    Execution stacks remain ephemeral. Durable intent lives in SQLite while the
    canonical conversation remains in ``session.json``. A queue item is claimed
    transactionally before a turn starts and acknowledged only after that turn has
    durably adopted the input.
    """

    def __init__(
        self,
        *args,
        durable_state: DurableThreadStateStore | None = None,
        auto_drain_queue: bool = True,
        max_auto_queued_turns: int = 8,
        **kwargs,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.durable_state = durable_state or DurableThreadStateStore(self.store.root.parent)
        self.auto_drain_queue = bool(auto_drain_queue)
        self.max_auto_queued_turns = max(1, int(max_auto_queued_turns))

    def get_session(self, session_id: str) -> AgentSession:
        session = self.store.load(session_id)
        self.durable_state.reconcile_dispatches(session.session_id, session.current_turn_id)
        return session

    def set_goal(
        self,
        session_id: str,
        objective: str,
        *,
        token_budget: int | None = None,
    ) -> ThreadGoal:
        self.store.load(session_id)
        goal = self.durable_state.set_goal(
            session_id,
            objective,
            token_budget=token_budget,
        )
        self._emit_external_event(
            session_id,
            AgentEventKind.GOAL_UPDATED,
            {
                "objective": goal.objective,
                "status": goal.status.value,
                "token_budget": goal.token_budget,
                "tokens_used": goal.tokens_used,
            },
        )
        return goal

    def get_goal(self, session_id: str) -> ThreadGoal | None:
        self.store.load(session_id)
        return self.durable_state.get_goal(session_id)

    def set_goal_status(self, session_id: str, status: GoalStatus | str) -> ThreadGoal:
        self.store.load(session_id)
        goal = self.durable_state.set_goal_status(session_id, status)
        self._emit_external_event(
            session_id,
            AgentEventKind.GOAL_UPDATED,
            {
                "objective": goal.objective,
                "status": goal.status.value,
                "token_budget": goal.token_budget,
                "tokens_used": goal.tokens_used,
            },
        )
        return goal

    def clear_goal(self, session_id: str) -> None:
        self.store.load(session_id)
        self.durable_state.clear_goal(session_id)
        self._emit_external_event(
            session_id,
            AgentEventKind.GOAL_UPDATED,
            {"status": "cleared"},
        )

    def enqueue_turn(self, session_id: str, text: str) -> QueuedTurn:
        self.store.load(session_id)
        item = self.durable_state.enqueue(session_id, text)
        self._emit_external_event(
            session_id,
            AgentEventKind.QUEUE_ENQUEUED,
            {
                "queue_id": item.queue_id,
                "text": item.text,
                "queue_depth": self.durable_state.pending_count(session_id),
            },
        )
        return item

    def list_queued_turns(self, session_id: str) -> tuple[QueuedTurn, ...]:
        self.store.load(session_id)
        return self.durable_state.list_queue(session_id)

    def remove_queued_turn(self, session_id: str, queue_id: str) -> bool:
        self.store.load(session_id)
        removed = self.durable_state.delete_queue_item(session_id, queue_id)
        if removed:
            self._emit_external_event(
                session_id,
                AgentEventKind.QUEUE_REMOVED,
                {"queue_id": str(queue_id)},
            )
        return removed

    def start_turn(self, session_id: str, user_text: str) -> AgentRunResult:
        result = self._start_turn_once(session_id, user_text)
        result = self._track_goal_usage(result)
        if self.auto_drain_queue and result.status is AgentStatus.COMPLETED:
            return self._drain_queue(session_id, result)
        return result

    def resume_approval(
        self,
        session_id: str,
        call_id: str,
        *,
        approved: bool,
    ) -> AgentRunResult:
        before = self.store.load(session_id).usage.total_tokens
        result = super().resume_approval(session_id, call_id, approved=approved)
        result = self._track_goal_usage(result, before_tokens=before)
        if self.auto_drain_queue and result.status is AgentStatus.COMPLETED:
            return self._drain_queue(session_id, result)
        return result

    def run_queued(self, session_id: str, *, max_turns: int | None = None) -> AgentRunResult | None:
        session = self.store.load(session_id)
        if session.status in {AgentStatus.RUNNING, AgentStatus.WAITING_APPROVAL}:
            raise RuntimeError("cannot synchronously drain queue while the thread is active")
        limit = self.max_auto_queued_turns if max_turns is None else max(1, int(max_turns))
        return self._drain_queue(session_id, None, max_turns=limit)

    def continue_goal(self, session_id: str, *, max_turns: int = 1) -> AgentRunResult:
        session = self.store.load(session_id)
        if session.status in {AgentStatus.RUNNING, AgentStatus.WAITING_APPROVAL}:
            raise RuntimeError("cannot continue goal while the thread is active")
        goal = self.durable_state.get_goal(session_id)
        if goal is None:
            raise RuntimeError("thread has no durable goal")
        if goal.status is not GoalStatus.ACTIVE:
            raise RuntimeError(f"thread goal is not active: {goal.status.value}")

        if self.durable_state.pending_count(session_id):
            queued = self.run_queued(session_id, max_turns=max_turns)
            if queued is not None:
                return queued

        result: AgentRunResult | None = None
        for _ in range(max(1, int(max_turns))):
            goal = self.durable_state.get_goal(session_id)
            if goal is None or goal.status is not GoalStatus.ACTIVE:
                break
            prompt = (
                "Continue pursuing this durable Loom goal:\n"
                f"{goal.objective}\n\n"
                "Review the existing thread history and workspace state, then take the next concrete "
                "steps. Do not restart completed work. If progress is blocked, explain the blocker clearly."
            )
            result = self._start_turn_once(session_id, prompt, source="goal")
            result = self._track_goal_usage(result)
            if result.status is not AgentStatus.COMPLETED:
                break
            if self.auto_drain_queue and self.durable_state.pending_count(session_id):
                result = self._drain_queue(session_id, result)
                if result.status is not AgentStatus.COMPLETED:
                    break
        if result is None:
            raise RuntimeError("goal continuation did not start a turn")
        return result

    def recover_interrupted(self, session_id: str) -> AgentRunResult:
        lock = self._session_lock(session_id)
        with lock:
            session = self.store.load(session_id)
            self.durable_state.reconcile_dispatches(session.session_id, session.current_turn_id)
            if session.status is not AgentStatus.RUNNING:
                return self._result(session)

            repair = repair_tool_history(
                session.messages,
                max_tool_result_chars=self.limits.max_tool_result_chars,
            )
            if repair.changed:
                session.messages = list(repair.messages)
                self._record(
                    session,
                    AgentEventKind.HISTORY_REPAIRED,
                    data={
                        "inserted_aborted_outputs": repair.inserted_aborted_outputs,
                        "removed_orphan_outputs": repair.removed_orphan_outputs,
                        "removed_duplicate_outputs": repair.removed_duplicate_outputs,
                    },
                )

            session.status = AgentStatus.INTERRUPTED
            session.pending_approval = None
            session.pending_tool_calls.clear()
            session.pending_step_id = ""
            session.error = (
                "Agent process stopped before the active turn reached a durable terminal state. "
                "Incomplete tool calls were repaired as aborted observations."
            )
            self._record(
                session,
                AgentEventKind.TURN_INTERRUPTED,
                data={"error": session.error},
            )
            return self._result(session)

    def repair_history(self, session_id: str) -> HistoryRepair:
        lock = self._session_lock(session_id)
        with lock:
            session = self.store.load(session_id)
            repair = repair_tool_history(
                session.messages,
                max_tool_result_chars=self.limits.max_tool_result_chars,
            )
            if repair.changed:
                session.messages = list(repair.messages)
                self._record(
                    session,
                    AgentEventKind.HISTORY_REPAIRED,
                    data={
                        "inserted_aborted_outputs": repair.inserted_aborted_outputs,
                        "removed_orphan_outputs": repair.removed_orphan_outputs,
                        "removed_duplicate_outputs": repair.removed_duplicate_outputs,
                    },
                )
            return repair

    def _start_turn_once(
        self,
        session_id: str,
        user_text: str,
        *,
        turn_id: str | None = None,
        source: str = "user",
        queue_item: QueuedTurn | None = None,
    ) -> AgentRunResult:
        text = str(user_text or "").strip()
        if not text:
            raise ValueError("agent turn input must not be empty")
        lock = self._session_lock(session_id)
        with lock:
            session = self.store.load(session_id)
            if session.status is AgentStatus.WAITING_APPROVAL:
                raise RuntimeError("agent session is waiting for tool approval")
            if session.status is AgentStatus.RUNNING:
                raise RuntimeError("agent session already has an active turn")

            resolved_turn_id = str(turn_id or uuid.uuid4())
            session.current_turn_id = resolved_turn_id
            session.status = AgentStatus.RUNNING
            session.model_steps = 0
            session.tool_calls = 0
            session.pending_tool_calls.clear()
            session.pending_step_id = ""
            session.pending_approval = None
            session.final_text = ""
            session.error = ""
            self.diff_trackers.for_turn(session.session_id, resolved_turn_id)
            session.messages.append(AIMessage(role=MessageRole.USER, content=text))
            start_data: dict[str, object] = {
                "permission_mode": session.permission_mode.value,
                "source": source,
            }
            if queue_item is not None:
                start_data["queue_id"] = queue_item.queue_id
            self._record(session, AgentEventKind.TURN_STARTED, data=start_data)
            self._record(
                session,
                AgentEventKind.USER_MESSAGE,
                data={"text": text, "source": source},
            )
            if queue_item is not None:
                self._record(
                    session,
                    AgentEventKind.QUEUE_DISPATCHED,
                    data={
                        "queue_id": queue_item.queue_id,
                        "turn_id": resolved_turn_id,
                    },
                )

            token = self._activate(session.session_id)
            try:
                return self._drive(session, token)
            finally:
                self._deactivate(session.session_id, token)

    def _drain_queue(
        self,
        session_id: str,
        result: AgentRunResult | None,
        *,
        max_turns: int | None = None,
    ) -> AgentRunResult | None:
        current = result
        limit = self.max_auto_queued_turns if max_turns is None else max(1, int(max_turns))
        for _ in range(limit):
            if current is not None and current.status is not AgentStatus.COMPLETED:
                break
            session = self.store.load(session_id)
            if session.status in {AgentStatus.RUNNING, AgentStatus.WAITING_APPROVAL}:
                break
            turn_id = str(uuid.uuid4())
            item = self.durable_state.claim_next(session_id, turn_id)
            if item is None:
                break
            try:
                queued_result = self._start_turn_once(
                    session_id,
                    item.text,
                    turn_id=turn_id,
                    source="queue",
                    queue_item=item,
                )
            except Exception:
                latest = self.store.load(session_id)
                if latest.current_turn_id == turn_id:
                    self.durable_state.complete_claim(item.queue_id, turn_id)
                else:
                    self.durable_state.release_claim(item.queue_id, turn_id)
                raise
            self.durable_state.complete_claim(item.queue_id, turn_id)
            current = self._track_goal_usage(queued_result)
        return current

    def _track_goal_usage(
        self,
        result: AgentRunResult,
        *,
        before_tokens: int | None = None,
    ) -> AgentRunResult:
        goal = self.durable_state.get_goal(result.session_id)
        if goal is None:
            return result
        after = self.store.load(result.session_id).usage.total_tokens
        if before_tokens is None:
            # A fresh turn resets per-turn counters but session usage is cumulative.
            # TURN_STARTED events do not persist a usage checkpoint, so derive the
            # current turn contribution from its model step events.
            turn_total = 0
            for event in self.store.events(result.session_id):
                if event.turn_id != result.turn_id or event.kind is not AgentEventKind.MODEL_RESPONSE:
                    continue
                usage = event.data.get("usage")
                if isinstance(usage, dict):
                    turn_total += int(usage.get("total_tokens") or 0)
            delta = turn_total
        else:
            delta = max(0, int(after) - int(before_tokens))
        updated = self.durable_state.add_goal_usage(result.session_id, delta)
        if updated is not None and updated.status is not goal.status:
            self._emit_external_event(
                result.session_id,
                AgentEventKind.GOAL_UPDATED,
                {
                    "objective": updated.objective,
                    "status": updated.status.value,
                    "token_budget": updated.token_budget,
                    "tokens_used": updated.tokens_used,
                },
                turn_id=result.turn_id,
            )
        return result

    def _emit_external_event(
        self,
        session_id: str,
        kind: AgentEventKind,
        data: dict[str, object],
        *,
        turn_id: str | None = None,
    ) -> AgentEvent:
        json.dumps(data, ensure_ascii=False)
        session = self.store.load(session_id)
        event = AgentEvent(
            event_id=str(uuid.uuid4()),
            session_id=session.session_id,
            turn_id=str(turn_id if turn_id is not None else session.current_turn_id),
            kind=AgentEventKind(kind),
            created_at=utc_now(),
            data=dict(data),
        )
        self.store.append_event(event)
        for listener in tuple(self._listeners):
            try:
                listener(event)
            except Exception:
                continue
        return event


__all__ = ["DurableAgentRuntime"]
