"""Token-aware request budgeting and safe in-turn compaction."""
from __future__ import annotations

import json
import math
from typing import Any, Sequence

from app.ai import AIMessage, ChatRequest, MessageRole, ModelResponse, ModelUsage, ToolChoice
from .history import repair_tool_history


_COMPACTION_RETRY_LIMIT = 3
_COMPACTION_SAFETY_TOKENS = 256
_INCOMPLETE_FINISH_MARKERS = (
    "length",
    "max_token",
    "max_output",
    "token_limit",
    "context_limit",
    "truncat",
    "incomplete",
    "tool_call",
    "function_call",
    "content_filter",
    "cancel",
    "interrupt",
    "error",
)


def estimate_tokens(messages, tools=()) -> int:
    # Conservative UTF-8 estimate, including schemas and image allowance. Providers
    # can replace this with their tokenizer without changing context ownership.
    from .storage import _message_to_dict
    data = [_message_to_dict(m) for m in messages]
    schemas = [{"name": t.name, "description": t.description, "parameters": t.input_schema} for t in tools]
    text = json.dumps([data, schemas], ensure_ascii=False)
    return math.ceil(len(text.encode("utf-8")) / 3) + 8 * len(messages) + sum(4096 for m in messages if m.uses_vision)


def safe_split(messages, keep=12):
    """Retain the latest user and never separate tool calls from their outputs."""
    last_user = max((i for i, m in enumerate(messages) if m.role is MessageRole.USER), default=-1)
    pending = set()
    candidates = []
    for i, message in enumerate(messages):
        pending.update(c.call_id for c in message.tool_calls)
        if message.role is MessageRole.TOOL:
            pending.discard(message.tool_call_id)
        if not pending and 0 < i + 1 <= len(messages) - 2:
            candidates.append(i + 1)
    desired = max(1, len(messages) - keep)
    return max((i for i in candidates if i <= desired), default=0), last_user


def _normalized_finish_reason(value: Any) -> str:
    return str(value or "").strip().casefold().replace("-", "_").replace(" ", "_")


def _finish_reason_is_incomplete(value: Any) -> bool:
    """Reject only reasons that clearly mean truncation/failure.

    OpenAI-compatible providers do not share one finish-reason vocabulary. The
    previous strict allow-list rejected perfectly valid values such as
    ``eos_token``. Treat unknown non-error reasons as complete and explicitly
    reject the families that mean truncation, tool handoff, filtering or abort.
    """

    reason = _normalized_finish_reason(value)
    if not reason:
        return False
    return any(marker in reason for marker in _INCOMPLETE_FINISH_MARKERS)


def _summary_response_is_complete(response: ModelResponse) -> bool:
    return bool(
        str(response.text or "").strip()
        and not response.tool_calls
        and not _finish_reason_is_incomplete(response.finish_reason)
    )


def _add_usage(left: ModelUsage, right: ModelUsage) -> ModelUsage:
    return ModelUsage(
        input_tokens=left.input_tokens + right.input_tokens,
        output_tokens=left.output_tokens + right.output_tokens,
        total_tokens=left.total_tokens + right.total_tokens,
    )


def _retained_for_split(history: Sequence[AIMessage], split: int, last_user: int) -> tuple[AIMessage, ...]:
    retained = tuple(history[split:])
    # During a long active turn the most recent user instruction may be on the
    # archived side of a safe tool boundary. Keep that instruction verbatim in
    # active context even though the canonical copy is also checkpointed.
    if 0 <= last_user < split:
        retained = (history[last_user], *retained)
    return retained


def _partition_for_budget(rt, history, transient, tools, budget):
    """Choose the least aggressive safe split whose recent suffix can fit.

    Old code picked one fixed keep-count and could fail even though archiving a
    few more complete turns would have created ample room. We progressively
    compact more history but never split an assistant tool-call group.
    """

    initial_keep = min(12, max(2, len(history) // 3))
    keep_candidates = []
    for keep in (initial_keep, max(2, initial_keep // 2), 2):
        if keep not in keep_candidates:
            keep_candidates.append(keep)

    best = None
    for keep in keep_candidates:
        split, last_user = safe_split(history, keep=keep)
        if not split:
            continue
        archived = tuple(history[:split])
        retained = _retained_for_split(history, split, last_user)
        best = (archived, retained)
        probe = [
            *transient,
            AIMessage(role=MessageRole.SYSTEM, content="Earlier context summary."),
            *retained,
        ]
        if (
            estimate_tokens(probe, tools) + _COMPACTION_SAFETY_TOKENS <= budget
            and len(probe) <= rt.limits.max_messages
        ):
            return archived, retained

    if best is None:
        raise RuntimeError("context budget exceeded with no safely compactable history")

    # Even the most aggressive legal split leaves too much recent state. This
    # cannot be solved by making the summary shorter; the retained messages,
    # runtime envelope or tool schemas themselves exceed the request budget.
    archived, retained = best
    probe = [
        *transient,
        AIMessage(role=MessageRole.SYSTEM, content="Context compacted."),
        *retained,
    ]
    if estimate_tokens(probe, tools) + _COMPACTION_SAFETY_TOKENS > budget:
        raise RuntimeError(
            "context budget is exhausted by recent messages or tool schemas after maximum safe compaction"
        )
    return archived, retained


def _compaction_prompt(max_output_tokens: int, attempt: int) -> str:
    from .context_runtime import _COMPACTION_SYSTEM_PROMPT

    # The model receives a generous token cap so it can finish cleanly, but the
    # prose target gets stricter on retry. This specifically prevents the common
    # `finish_reason=length` loop without accepting a visibly truncated summary.
    if attempt <= 0:
        words = min(650, max(180, int(max_output_tokens * 0.34)))
        cjk_chars = min(1400, max(420, int(max_output_tokens * 0.72)))
    elif attempt == 1:
        words = min(420, max(130, int(max_output_tokens * 0.23)))
        cjk_chars = min(900, max(300, int(max_output_tokens * 0.48)))
    else:
        words = min(260, max(90, int(max_output_tokens * 0.15)))
        cjk_chars = min(600, max(220, int(max_output_tokens * 0.32)))

    retry_note = (
        " This is a retry because a prior compaction response was incomplete or too large."
        if attempt
        else ""
    )
    return (
        f"{_COMPACTION_SYSTEM_PROMPT}{retry_note} "
        f"Hard size target: at most {words} English words or {cjk_chars} CJK characters. "
        "Finish the summary completely within that size. Do not call tools, emit JSON, or add commentary."
    )


def _build_summary_request(archived, *, max_output_tokens: int, attempt: int) -> ChatRequest:
    return ChatRequest(
        messages=(
            AIMessage(
                role=MessageRole.SYSTEM,
                content=_compaction_prompt(max_output_tokens, attempt),
            ),
            *archived,
        ),
        tools=(),
        tool_choice=ToolChoice.NONE,
        temperature=0.1,
        max_output_tokens=max_output_tokens,
    )


def _message_excerpt(message: AIMessage, *, per_message_chars: int = 700) -> str:
    role = message.role.value.upper()
    if message.role is MessageRole.TOOL and message.name:
        role = f"TOOL[{message.name}]"

    pieces: list[str] = []
    if isinstance(message.content, str):
        text = " ".join(message.content.split())
        if text:
            pieces.append(text)
    else:
        for part in message.content:
            text = str(getattr(part, "text", "") or "").strip()
            if text:
                pieces.append(" ".join(text.split()))
            elif getattr(part, "image_url", None):
                pieces.append("[image]")

    if message.tool_calls:
        calls = []
        for call in message.tool_calls:
            arguments = json.dumps(call.arguments, ensure_ascii=False, separators=(",", ":"))
            if len(arguments) > 260:
                arguments = arguments[:257] + "..."
            calls.append(f"{call.name}({arguments})")
        pieces.append("tool calls: " + "; ".join(calls))

    text = " | ".join(piece for piece in pieces if piece).strip()
    if not text:
        return ""
    if len(text) > per_message_chars:
        text = text[: per_message_chars - 1].rstrip() + "…"
    return f"{role}: {text}"


def _extractive_fallback_summary(archived, *, max_chars: int) -> str:
    """Build a truthful bounded fallback when the model cannot finish a summary.

    The full canonical archive is still written to the checkpoint. This fallback
    only supplies enough verbatim context for the next model step so a flaky
    compaction sub-request cannot kill the user's active turn.
    """

    max_chars = max(220, int(max_chars))
    header = (
        "Compaction fallback: verbatim excerpts from earlier canonical history. "
        "Full archived messages remain preserved in the Loom checkpoint."
    )
    lines = [_message_excerpt(message) for message in archived]
    lines = [line for line in lines if line]
    if not lines:
        return header

    full = header + "\n" + "\n".join(lines)
    if len(full) <= max_chars:
        return full

    available = max(0, max_chars - len(header) - 48)
    selected: dict[int, str] = {}
    left = 0
    right = len(lines) - 1
    take_front = True
    while left <= right and available > 40:
        index = left if take_front else right
        line = lines[index]
        cost = len(line) + 1
        if cost <= available:
            selected[index] = line
            available -= cost
        if take_front:
            left += 1
        else:
            right -= 1
        take_front = not take_front

    ordered = [selected[index] for index in sorted(selected)]
    omitted = len(lines) - len(ordered)
    marker = f"[{omitted} archived messages omitted from this active excerpt; preserved in checkpoint]"
    text = header + "\n" + "\n".join(ordered)
    if omitted:
        text += "\n" + marker
    return text[:max_chars].rstrip()


def _fallback_that_fits(archived, transient, retained, tools, budget, summary_token_budget):
    # Start generous, then reduce the extractive window until the exact same
    # estimator used by the main request proves the candidate fits.
    char_limit = min(6000, max(600, int(summary_token_budget * 1.8)))
    for _ in range(8):
        summary = _extractive_fallback_summary(archived, max_chars=char_limit)
        candidate = [*transient, AIMessage(role=MessageRole.SYSTEM, content=summary), *retained]
        if estimate_tokens(candidate, tools) + _COMPACTION_SAFETY_TOKENS <= budget:
            return summary
        char_limit = max(220, int(char_limit * 0.62))

    minimal = (
        "Earlier canonical history was compacted because the model summarizer could not finish reliably. "
        "The complete archived messages are preserved in the Loom checkpoint; recent messages remain verbatim."
    )
    candidate = [*transient, AIMessage(role=MessageRole.SYSTEM, content=minimal), *retained]
    if estimate_tokens(candidate, tools) + _COMPACTION_SAFETY_TOKENS <= budget:
        return minimal
    raise RuntimeError(
        "context budget is exhausted by recent messages or tool schemas; even a minimal compaction marker cannot fit"
    )


def _apply_uncommitted_usage(session, usage: ModelUsage) -> None:
    if not (usage.input_tokens or usage.output_tokens or usage.total_tokens):
        return
    from .runtime import _add_usage as runtime_add_usage
    session.usage = runtime_add_usage(session.usage, usage)


def prepare_context(rt, session, step, token):
    envelope = rt._context_envelope(session, step)
    transient = list(rt._request_context_messages(session, step, envelope))
    instructions = rt.instruction_loader.load(session.workspace_dir)
    if instructions:
        transient.append(AIMessage(role=MessageRole.SYSTEM, name="loom_project_instructions", content=instructions))
    tools = step.tool_router.definitions()
    budget = rt.limits.context_window_tokens - rt.limits.output_reserve_tokens
    messages = [*transient, *session.messages]
    if estimate_tokens(messages, tools) <= budget and len(messages) <= rt.limits.max_messages:
        return messages, {"context_digest": envelope.digest}

    repair = repair_tool_history(session.messages, max_tool_result_chars=rt.limits.max_tool_result_chars)
    history = tuple(repair.messages)
    archived, retained = _partition_for_budget(rt, history, transient, tools, budget)

    # Reserve enough room for the retained transcript and tool schemas first;
    # the summary is then explicitly bounded to the remaining request capacity.
    base_candidate = [
        *transient,
        AIMessage(role=MessageRole.SYSTEM, content="x"),
        *retained,
    ]
    base_tokens = estimate_tokens(base_candidate, tools)
    summary_token_budget = budget - base_tokens - _COMPACTION_SAFETY_TOKENS
    if summary_token_budget < 64:
        raise RuntimeError("context budget leaves no usable space for a compaction summary")
    max_summary_output = max(
        64,
        min(2048, rt.limits.output_reserve_tokens, summary_token_budget),
    )

    total_usage = ModelUsage()
    last_failure = ""
    attempted_model = False

    for attempt in range(_COMPACTION_RETRY_LIMIT):
        summary_request = _build_summary_request(
            archived,
            max_output_tokens=max_summary_output,
            attempt=attempt,
        )
        # If the archive itself cannot fit into a legal summarization request,
        # do not send a request that the provider must reject. Fall back to
        # bounded verbatim excerpts; the full history remains in the checkpoint.
        if estimate_tokens(summary_request.messages) > budget:
            last_failure = "summary_request_input_exceeded_budget"
            break

        attempted_model = True
        response = rt.model_executor.execute(rt.platform, session.profile_id, summary_request, token)
        if not isinstance(response, ModelResponse):
            raise TypeError("compaction model must return ModelResponse")
        total_usage = _add_usage(total_usage, response.usage)

        if not _summary_response_is_complete(response):
            if not str(response.text or "").strip():
                last_failure = "empty_summary"
            elif response.tool_calls:
                last_failure = "unexpected_tool_calls"
            else:
                last_failure = f"incomplete_finish:{_normalized_finish_reason(response.finish_reason) or 'unknown'}"
            continue

        summary = str(response.text or "").strip()
        candidate = [*transient, AIMessage(role=MessageRole.SYSTEM, content=summary), *retained]
        if (
            estimate_tokens(candidate, tools) + _COMPACTION_SAFETY_TOKENS <= budget
            and len(candidate) <= rt.limits.max_messages
        ):
            rt._commit_compaction_locked(
                session,
                summary=summary,
                repaired=repair,
                archived=archived,
                retained=retained,
                summary_source="auto" if attempt == 0 else "auto_retry",
                summary_usage=total_usage,
            )
            return [*transient, *session.messages], {
                "context_digest": envelope.digest,
                "auto_compacted": True,
                "compaction_attempts": attempt + 1,
                "compaction_fallback": False,
            }
        last_failure = "summary_still_too_large"

    # A compaction helper request is not allowed to take down the user's main
    # turn merely because a provider returned `length`, a provider-specific
    # finish reason, an empty answer, or an overlong summary. Use a bounded,
    # truthful extractive summary and preserve the entire archive in checkpoint
    # storage. The next normal model call can continue immediately.
    fallback = _fallback_that_fits(
        archived,
        transient,
        retained,
        tools,
        budget,
        summary_token_budget,
    )
    rt._commit_compaction_locked(
        session,
        summary=fallback,
        repaired=repair,
        archived=archived,
        retained=retained,
        summary_source="auto_fallback",
        summary_usage=total_usage if attempted_model else None,
    )
    return [*transient, *session.messages], {
        "context_digest": envelope.digest,
        "auto_compacted": True,
        "compaction_attempts": _COMPACTION_RETRY_LIMIT if attempted_model else 0,
        "compaction_fallback": True,
        "compaction_fallback_reason": last_failure or "model_summary_unavailable",
    }
