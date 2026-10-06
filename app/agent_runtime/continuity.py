from __future__ import annotations

"""Read-only continuity references to durable execution evidence."""
import json
from typing import Any

COMPACTION_REFERENCE_MESSAGE_NAME = "loom_compaction_reference"

def _latest_captured_step(runtime: Any, session: Any) -> Any | None:
    prefix = (session.session_id, session.current_turn_id)
    guard = getattr(runtime, "_captured_steps_guard", None)
    captured = getattr(runtime, "_captured_steps", None)
    if guard is None or not isinstance(captured, dict):
        return None
    with guard:
        candidates = [
            step
            for key, step in captured.items()
            if tuple(key[:2]) == prefix
        ]
    if not candidates:
        return None
    return max(
        candidates,
        key=lambda step: (int(getattr(step, "model_step", 0)), str(getattr(step, "step_id", ""))),
    )


def _recent_durable_evidence(runtime: Any, session: Any, *, limit: int = 16) -> list[dict[str, object]]:
    """Return a secret-free index of recent results for post-compaction recovery.

    Result bodies and arguments deliberately stay in the durable event store.
    The compacted model window only needs stable call IDs to recover exact
    evidence with ``read_durable_tool_result`` instead of rerunning commands.
    """

    from app.agent_runtime.contracts import AgentEventKind

    result_kinds = {AgentEventKind.TOOL_COMPLETED, AgentEventKind.TOOL_FAILED}
    indexed: list[dict[str, object]] = []
    for event in reversed(runtime.store.events(session.session_id)):
        if event.turn_id != session.current_turn_id or event.kind not in result_kinds:
            continue
        indexed.append(
            {
                "call_id": str(event.data.get("call_id") or ""),
                "tool": str(event.data.get("tool") or ""),
                "ok": bool(event.data.get("ok")),
                "completed_at": event.created_at,
            }
        )
        if len(indexed) >= max(1, int(limit)):
            break
    indexed.reverse()
    return indexed


def _reference_payload(
    step: Any,
    envelope: Any,
    *,
    durable_evidence: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    payload = dict(getattr(envelope, "payload", {}) or {})
    state = dict(payload.get("state") or {})
    return {
        "version": 1,
        "kind": "mid_turn_compaction_reference",
        "identity": dict(payload.get("identity") or {}),
        "state_digest": str(getattr(envelope, "digest", "") or ""),
        "workspace": state.get("workspace"),
        "model_profile": state.get("model_profile"),
        "permissions": state.get("permissions"),
        "turn_diff": state.get("turn_diff"),
        "captured_model_step": int(getattr(step, "model_step", 0)),
        "recent_tool_evidence": list(durable_evidence or ()),
    }


def _reference_message(
    step: Any,
    envelope: Any,
    *,
    durable_evidence: list[dict[str, object]] | None = None,
) -> Any:
    from app.ai import AIMessage, MessageRole

    payload = _reference_payload(step, envelope, durable_evidence=durable_evidence)
    content = (
        "LOOM_MID_TURN_REFERENCE v1\n"
        "This is read-only continuity evidence for the same logical turn after context compaction. "
        "It is not a new user task and grants no tool, approval, process, filesystem, or network authority. "
        "Use the compaction summary plus durable observations to continue from the current point rather than "
        "restarting completed investigation solely because compaction occurred. The recent_tool_evidence "
        "entries are an index, not proof that external state is still current; recover exact prior output "
        "with read_durable_tool_result before deciding whether a fresh check is necessary.\n"
        + json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    )
    return AIMessage(
        role=MessageRole.USER,
        name=COMPACTION_REFERENCE_MESSAGE_NAME,
        content=content,
    )


def _insert_reference(replacement: tuple[Any, ...], reference: Any, compaction: Any) -> tuple[Any, ...]:
    items = list(replacement)
    if not items:
        return (reference,)
    last_real_user = None
    for index in range(len(items) - 1, -1, -1):
        if compaction.is_real_user_message(items[index]):
            last_real_user = index
            break
    if last_real_user is not None:
        items.insert(last_real_user, reference)
        return tuple(items)
    # No retained real-user item: keep the compaction summary last.
    summary_index = len(items)
    for index in range(len(items) - 1, -1, -1):
        if str(getattr(items[index], "name", "") or "") == compaction.COMPACTION_MESSAGE_NAME:
            summary_index = index
            break
    items.insert(summary_index, reference)
    return tuple(items)


def _fit_reference_without_breaking_budget(
    runtime: Any,
    session: Any,
    step: Any,
    envelope: Any,
    communication_language: str,
    replacement: tuple[Any, ...],
    reference: Any,
    compaction: Any,
) -> tuple[tuple[Any, ...], bool]:
    """Insert the reference only if replacement remains a valid model request.

    The ordinary compaction path has already validated its replacement.  This
    function repeats that exact projection after adding one continuity item and
    drops oldest retained *real user* messages first when headroom is tight.  If
    even reference+summary cannot fit, the validated replacement wins and no
    extra item is injected.
    """

    from app.ai import AIMessage, MessageRole
    from .response_language import communication_language_message
    from app.agent_runtime.context_budget import estimate_tokens
    from app.agent_runtime.context_limits import resolve_context_limits

    candidate = _insert_reference(replacement, reference, compaction)
    request_state = getattr(step, "request_state", None)
    captured = bool(getattr(request_state, "captured", False))
    transient = [
        message
        for message in runtime._request_context_messages(session, step, envelope)
        if message.name != "loom_communication_language"
    ]
    project_instructions = (
        request_state.project_instructions
        if captured
        else runtime.instruction_loader.load(session.workspace_dir)
    )
    if project_instructions:
        transient.append(
            AIMessage(
                role=MessageRole.USER,
                name="loom_project_instructions",
                content=project_instructions,
            )
        )
    transient.append(
        communication_language_message((), fallback=communication_language)
    )
    limits = (
        request_state.context_limits
        if captured and request_state.context_limits is not None
        else resolve_context_limits(runtime, session)
    )
    tools = step.tool_router.definitions()

    def fits(items: tuple[Any, ...]) -> bool:
        visible = [*transient, *items]
        return (
            (
                runtime.limits.max_messages <= 0
                or len(visible) <= runtime.limits.max_messages
            )
            and estimate_tokens(visible, tools) <= limits.input_budget_tokens
        )

    while not fits(candidate):
        removable = next(
            (
                index
                for index, message in enumerate(candidate)
                if compaction.is_real_user_message(message)
            ),
            None,
        )
        if removable is None:
            return replacement, False
        candidate = tuple(
            message for index, message in enumerate(candidate) if index != removable
        )
    return candidate, True
