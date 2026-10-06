from __future__ import annotations

"""Deterministic handoff when a text-only compaction response is unusable."""
import json
from typing import Any, Sequence

_FALLBACK_HEADER = (
    "Deterministic Loom checkpoint created because the compaction provider did not return usable summary text. "
    "This is continuity state for the same task, not a new user request. Continue from the recorded progress and "
    "do not repeat completed investigation solely because compaction occurred."
)
_MAX_FALLBACK_CHARS = 12_000
_MAX_ENTRY_CHARS = 2_400


def _clip(text: str, limit: int) -> str:
    value = str(text or "")
    if len(value) <= limit:
        return value
    if limit <= 32:
        return value[:limit]
    head = max(1, (limit - 24) // 2)
    tail = max(1, limit - 24 - head)
    return value[:head] + "\n[... clipped ...]\n" + value[-tail:]


def _content_text(message: Any) -> str:
    content = getattr(message, "content", "")
    if isinstance(content, str):
        return content
    parts: list[str] = []
    for part in tuple(content or ()):
        text = getattr(part, "text", None)
        if text:
            parts.append(str(text))
            continue
        if getattr(part, "image_url", None):
            parts.append("[image]")
    return "\n".join(parts)


def build_deterministic_compaction_summary(
    history: Sequence[Any],
    *,
    max_chars: int = _MAX_FALLBACK_CHARS,
) -> str:
    """Build a bounded local handoff that favors the newest execution evidence."""

    budget = max(512, int(max_chars))
    entries_reversed: list[str] = []
    used = len(_FALLBACK_HEADER) + 64

    for message in reversed(tuple(history)):
        role = str(getattr(getattr(message, "role", None), "value", getattr(message, "role", "unknown")))
        name = str(getattr(message, "name", "") or "").strip()
        label = role if not name else f"{role}:{name}"
        body = _clip(_content_text(message).strip(), _MAX_ENTRY_CHARS)

        calls = []
        for call in tuple(getattr(message, "tool_calls", ()) or ()):
            call_name = str(getattr(call, "name", "") or "tool")
            arguments = getattr(call, "arguments", {}) or {}
            try:
                raw_arguments = json.dumps(arguments, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            except Exception:
                raw_arguments = repr(arguments)
            calls.append(f"{call_name}({_clip(raw_arguments, 600)})")
        if calls:
            call_text = ", ".join(calls)
            body = (body + "\n" if body else "") + f"tool calls requested: {call_text}"

        if not body:
            continue
        entry = f"[{label}]\n{body}"
        entry = _clip(entry, _MAX_ENTRY_CHARS + 160)
        cost = len(entry) + 2
        if entries_reversed and used + cost > budget:
            break
        if not entries_reversed and used + cost > budget:
            entry = _clip(entry, max(128, budget - used))
            cost = len(entry) + 2
        entries_reversed.append(entry)
        used += cost
        if used >= budget:
            break

    entries_reversed.reverse()
    if not entries_reversed:
        entries_reversed.append("[continuity]\nNo textual execution evidence was available; preserve the retained user instructions and runtime state.")
    return _FALLBACK_HEADER + "\n\nRecent canonical progress:\n\n" + "\n\n".join(entries_reversed)


def _invalid_compaction_error(exc: BaseException) -> bool:
    text = str(exc or "").casefold()
    return (
        "context compaction model repeatedly returned unexpected tool calls" in text
        or "context compaction model repeatedly returned an empty summary" in text
        or "context compaction model returned unexpected tool calls" in text
        or "context compaction model returned an empty summary" in text
    )


def _projection_context(runtime: Any, session: Any, step: Any, envelope: Any):
    from app.ai import AIMessage, MessageRole
    from app.agent_runtime.context_limits import resolve_context_limits
    from app.agent_runtime.response_language import communication_language_message, infer_user_language

    transient = [
        message
        for message in runtime._request_context_messages(session, step, envelope)
        if str(getattr(message, "name", "") or "") != "loom_communication_language"
    ]
    request_state = getattr(step, "request_state", None)
    captured = bool(getattr(request_state, "captured", False))
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
    communication_language = (
        request_state.communication_language
        if captured
        else infer_user_language(session.messages, fallback=session.communication_language)
    )
    session.communication_language = communication_language
    transient.append(
        communication_language_message(
            () if captured else session.messages,
            fallback=communication_language,
        )
    )
    limits = (
        request_state.context_limits
        if captured and request_state.context_limits is not None
        else resolve_context_limits(runtime, session)
    )
    tools = step.tool_router.definitions()
    return transient, communication_language, limits, tools


def _fit_fallback_replacement(
    runtime: Any,
    session: Any,
    step: Any,
    repair: Any,
    summary: str,
) -> tuple[tuple[Any, ...], list[Any], str, Any, Any, int]:
    from app.ai import AIMessage
    from . import context_budget as budget
    from app.agent_runtime import context_compaction as compaction

    envelope = runtime._context_envelope(session, step)
    transient, communication_language, limits, tools = _projection_context(
        runtime, session, step, envelope
    )
    replacement = compaction.build_compacted_history(
        tuple(repair.messages),
        summary,
        token_counter=lambda messages: budget.estimate_tokens(messages),
    )
    replacement = budget._fit_replacement_message_limit(
        replacement,
        transient_count=len(transient),
        max_messages=runtime.limits.max_messages,
    )

    def projected_tokens(items: Sequence[Any]) -> int:
        return budget.estimate_tokens([*transient, *items], tools)

    while (
        projected_tokens(replacement) > limits.input_budget_tokens
        or len(transient) + len(replacement) > runtime.limits.max_messages
    ) and len(replacement) > 1:
        removable = next(
            (
                index
                for index, message in enumerate(replacement[:-1])
                if compaction.is_real_user_message(message)
            ),
            None,
        )
        if removable is None:
            break
        replacement = tuple(
            message for index, message in enumerate(replacement) if index != removable
        )

    if projected_tokens(replacement) > limits.input_budget_tokens and replacement:
        summary_message = replacement[-1]
        original_content = str(getattr(summary_message, "content", "") or "")
        low, high = 64, len(original_content)
        best: str | None = None
        while low <= high:
            mid = (low + high) // 2
            candidate_message = AIMessage(
                role=summary_message.role,
                name=summary_message.name,
                content=original_content[:mid],
            )
            candidate = tuple((*replacement[:-1], candidate_message))
            if projected_tokens(candidate) <= limits.input_budget_tokens:
                best = original_content[:mid]
                low = mid + 1
            else:
                high = mid - 1
        if best is not None:
            replacement = tuple(
                (*replacement[:-1], AIMessage(role=summary_message.role, name=summary_message.name, content=best))
            )

    estimated_after = projected_tokens(replacement)
    if (
        estimated_after > limits.input_budget_tokens
        or len(transient) + len(replacement) > runtime.limits.max_messages
    ):
        raise budget.ContextBudgetExceeded(
            estimated_tokens=estimated_after,
            input_budget_tokens=limits.input_budget_tokens,
            tool_schema_tokens=budget.estimate_tool_schema_tokens(tools),
            message_count=len(transient) + len(replacement),
            reason="deterministic compaction fallback cannot fit the current model request budget",
        )
    return replacement, transient, communication_language, limits, tools, estimated_after
