from __future__ import annotations

"""Safe reconstruction of a durable active turn after its executor disappears.

The durable transcript is authoritative; process-local execution state is not.
A handoff may therefore resume model sampling only when every previously
requested tool has a durable terminal observation. Approval waiters and tool
calls with an unknown outcome fail closed instead of being replayed.

Retry-exhausted ``AITransportError`` turns are also resumable only when their
terminal failure event durably records ``retryable_transport=true``. This keeps
provider/credential rejections terminal while genuine network loss can rebuild
the exact model request from the last safe durable boundary.
"""

from typing import Any

from .contracts import AgentEvent, AgentEventKind, AgentRunResult, AgentStatus
from .durable_runtime import DurableAgentRuntime
from .turn_response_validation import COMPLETE_FINISH_REASONS


_TOOL_TERMINAL = {
    AgentEventKind.TOOL_COMPLETED,
    AgentEventKind.TOOL_FAILED,
    AgentEventKind.TOOL_DENIED,
}
_TOOL_NONTERMINAL = {
    AgentEventKind.TOOL_REQUESTED,
    AgentEventKind.TOOL_APPROVAL_REQUIRED,
    AgentEventKind.TOOL_APPROVED,
    AgentEventKind.TOOL_STARTED,
}
_AFTER_RESPONSE_REQUIRES_CONTINUATION = {
    AgentEventKind.USER_MESSAGE,
    AgentEventKind.MODEL_REQUESTED,
    AgentEventKind.MODEL_RESPONSE_REJECTED,
    AgentEventKind.TOOL_REQUESTED,
}
_INSTALLED = False


def _turn_events(runtime: DurableAgentRuntime, session_id: str, turn_id: str) -> list[AgentEvent]:
    return [event for event in runtime.store.events(session_id) if event.turn_id == turn_id]


def _unresolved_tool_calls(events: list[AgentEvent]) -> set[str]:
    unresolved: set[str] = set()
    for event in events:
        call_id = str(event.data.get("call_id") or "").strip()
        if not call_id:
            continue
        if event.kind in _TOOL_NONTERMINAL:
            unresolved.add(call_id)
        elif event.kind in _TOOL_TERMINAL:
            unresolved.discard(call_id)
    return unresolved


def _terminal_response(events: list[AgentEvent]) -> AgentEvent | None:
    """Return a durably observed terminal response that only missed turn commit."""

    candidate_index = -1
    candidate: AgentEvent | None = None
    for index, event in enumerate(events):
        if event.kind is AgentEventKind.MODEL_RESPONSE:
            candidate_index = index
            candidate = event
    if candidate is None:
        return None

    finish_reason = str(candidate.data.get("finish_reason") or "").casefold()
    tool_calls = candidate.data.get("tool_calls") or []
    if finish_reason not in COMPLETE_FINISH_REASONS or tool_calls:
        return None

    # Steering or a later sample request means this response was not the final
    # durable boundary even though its provider finish reason was complete.
    if any(
        event.kind in _AFTER_RESPONSE_REQUIRES_CONTINUATION
        for event in events[candidate_index + 1 :]
    ):
        return None
    return candidate


def _live_process_token(runtime: DurableAgentRuntime, session_id: str) -> bool:
    with runtime._active_tokens_guard:
        token = runtime._active_tokens.get(session_id)
    return token is not None and not token.cancelled


def _live_approval_context(runtime: DurableAgentRuntime, session: Any) -> bool:
    if session.status is not AgentStatus.WAITING_APPROVAL or session.pending_approval is None:
        return False
    try:
        runtime._captured_step_context(session, step_id=session.pending_step_id or None)
    except RuntimeError:
        return False
    return True


def _transport_failed(runtime: DurableAgentRuntime, session: Any, turn_id: str) -> bool:
    """Whether the terminal failure is durably proven retryable transport loss."""

    if (
        session.status is not AgentStatus.FAILED
        or not str(session.error or "").startswith("AITransportError:")
    ):
        return False
    for event in reversed(_turn_events(runtime, session.session_id, turn_id)):
        if event.kind is AgentEventKind.TURN_FAILED:
            return event.data.get("retryable_transport") is True
    return False


def recover_turn_if_idle(
    runtime: DurableAgentRuntime,
    session_id: str,
    turn_id: str,
) -> AgentRunResult:
    """Resume one logical turn from its last safe durable boundary.

    This method is intentionally idempotent. A live executor/rejoin is a no-op,
    terminal user cancellation is never restarted, and an unresolved tool or a
    lost approval capability is converted to ``INTERRUPTED`` rather than replayed.
    A retry-exhausted transport failure may resume the same logical turn only
    when its durable failure event explicitly marks the transport as retryable.
    """

    resolved_session_id = str(session_id or "").strip()
    resolved_turn_id = str(turn_id or "").strip()
    if not resolved_session_id or not resolved_turn_id:
        raise ValueError("session_id and turn_id must not be empty")

    fail_closed = False
    result: AgentRunResult | None = None
    transport_retry = False
    recovery_usage_start: int | None = None
    lock = runtime._session_lock(resolved_session_id)
    with lock:
        session = runtime.store.load(resolved_session_id)
        runtime.durable_state.reconcile_dispatches(session.session_id, session.current_turn_id)
        if session.current_turn_id != resolved_turn_id:
            raise ValueError("turn_id does not match the unfinished turn")

        transport_retry = _transport_failed(runtime, session, resolved_turn_id)
        if transport_retry:
            # The failed invocation already returned through DurableAgentRuntime
            # and therefore already contributed its usage to a durable goal.
            # Recovery must account only for tokens consumed after this point.
            recovery_usage_start = session.usage.total_tokens

        # Explicit Stop/Cancel and non-retryable terminal states stay terminal.
        if session.status not in {AgentStatus.RUNNING, AgentStatus.WAITING_APPROVAL} and not transport_retry:
            return runtime._result(session)

        # The app-server can reconnect while an executor is still alive. Never
        # create a second sampler for the same logical turn.
        if _live_process_token(runtime, session.session_id):
            return runtime._result(session)

        # A same-process approval can be rejoined because its immutable StepContext
        # still exists. Across a process restart that capability is gone, so replay
        # would authorize a potentially changed action and must fail closed.
        if session.status is AgentStatus.WAITING_APPROVAL:
            if _live_approval_context(runtime, session):
                return runtime._result(session)
            fail_closed = True
        else:
            events = _turn_events(runtime, session.session_id, resolved_turn_id)
            unresolved = _unresolved_tool_calls(events)
            pending_ids = {
                str(call.call_id or "").strip()
                for call in session.pending_tool_calls
                if str(call.call_id or "").strip()
            }

            # A transport-failed turn is terminal only because its bounded retry
            # budget expired. Move it back to RUNNING before either safe resume or
            # fail-closed finalization so recovery owns the next transition.
            if transport_retry:
                session.status = AgentStatus.RUNNING
                session.error = ""
                runtime.store.save(session)

            if unresolved or pending_ids:
                fail_closed = True
            else:
                if transport_retry:
                    # Publish the backend transition so clients never need to
                    # manufacture a speculative running state on their own.
                    runtime._record(
                        session,
                        AgentEventKind.TURN_STARTED,
                        data={
                            "source": "network_recovery",
                            "recovered": True,
                            "usage_start": session.usage.total_tokens,
                        },
                    )
                terminal = _terminal_response(events)
                if terminal is not None:
                    # MODEL_RESPONSE was durable, but the process died in the tiny
                    # window before TURN_COMPLETED. Commit that exact response
                    # instead of sampling a duplicate answer.
                    session.status = AgentStatus.COMPLETED
                    session.final_text = str(terminal.data.get("text") or "")
                    session.error = ""
                    session.pending_approval = None
                    session.pending_tool_calls.clear()
                    session.pending_step_id = ""
                    session.pending_bindings.clear()
                    runtime._release_session_steps(session.session_id)
                    diff = runtime.diff_trackers.snapshot(session.session_id, session.current_turn_id)
                    runtime.store.save(session)
                    runtime._record(
                        session,
                        AgentEventKind.TURN_COMPLETED,
                        data={
                            "text": session.final_text,
                            "diff_revision": diff.revision,
                            "changed_paths": list(diff.paths),
                            "recovered": True,
                        },
                    )
                    result = runtime._result(session)
                else:
                    # No externally-effecting action is unresolved. Rebuild a fresh
                    # model execution stack from the durable transcript and keep the
                    # original turn id; do not append the user's message again.
                    session.status = AgentStatus.RUNNING
                    session.error = ""
                    session.pending_approval = None
                    session.pending_tool_calls.clear()
                    session.pending_step_id = ""
                    session.pending_bindings.clear()
                    runtime._release_session_steps(session.session_id)
                    token = runtime._activate(session.session_id)
                    try:
                        result = runtime._drive(session, token)
                    finally:
                        runtime._deactivate(session.session_id, token)

    if fail_closed:
        return runtime.recover_interrupted(resolved_session_id)
    if result is None:
        raise RuntimeError("safe handoff recovery produced no result")

    result = runtime._track_goal_usage(result, before_tokens=recovery_usage_start) if transport_retry else runtime._track_goal_usage(result)
    if runtime.auto_drain_queue and result.status is AgentStatus.COMPLETED:
        drained = runtime._drain_queue(resolved_session_id, result)
        if drained is not None:
            return drained
    return result


def install() -> None:
    """Install the recovery primitive on the durable runtime base class."""

    global _INSTALLED
    if _INSTALLED:
        return
    if not callable(getattr(DurableAgentRuntime, "recover_turn_if_idle", None)):
        DurableAgentRuntime.recover_turn_if_idle = recover_turn_if_idle  # type: ignore[attr-defined]
    _INSTALLED = True


__all__ = ["install", "recover_turn_if_idle"]
