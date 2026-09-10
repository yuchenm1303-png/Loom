"""Token-aware request budgeting and safe in-turn compaction."""
from __future__ import annotations

import json
import math
import time

from app.ai import AIMessage, ChatRequest, MessageRole, ModelResponse, ModelUsage, ToolChoice
from app.ai.errors import AITransportError
from app.ai.execution_control import ModelCancelled
from .history import repair_tool_history


# Open-source Codex treats compaction as a normal model turn. It retries transient
# stream failures, trims the oldest compact-input history when the provider says
# the context window is too large, and succeeds once the model response completes.
# Loom uses Chat Completions rather than Codex's Responses stream, so these are the
# equivalent provider-neutral controls at our ModelResponse boundary.
_COMPACTION_STREAM_RETRIES = 2
_COMPACTION_FIT_MARGIN = 256
_CONTEXT_WINDOW_ERROR_MARKERS = (
    "context_length_exceeded",
    "maximum context length",
    "context window exceeded",
    "context window is too large",
    "too many tokens",
    "max context",
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


def _safe_front_boundary(messages) -> int:
    """Return the first trim boundary that does not split a tool call/result group."""
    pending = set()
    for i, message in enumerate(messages):
        pending.update(call.call_id for call in message.tool_calls)
        if message.role is MessageRole.TOOL:
            pending.discard(message.tool_call_id)
        if not pending:
            return i + 1
    return 0


def _drop_oldest_safe_group(messages):
    boundary = _safe_front_boundary(messages)
    if boundary <= 0 or boundary >= len(messages):
        return ()
    return tuple(messages[boundary:])


def _is_context_window_error(exc: BaseException) -> bool:
    text = str(exc or "").casefold()
    return any(marker in text for marker in _CONTEXT_WINDOW_ERROR_MARKERS)


def _add_usage(left: ModelUsage, right: ModelUsage) -> ModelUsage:
    return ModelUsage(
        input_tokens=left.input_tokens + right.input_tokens,
        output_tokens=left.output_tokens + right.output_tokens,
        total_tokens=left.total_tokens + right.total_tokens,
    )


def _record_failed_compaction_usage(session, usage: ModelUsage) -> None:
    if not (usage.input_tokens or usage.output_tokens or usage.total_tokens):
        return
    from .runtime import _add_usage as runtime_add_usage
    session.usage = runtime_add_usage(session.usage, usage)


def _partition_candidates(history):
    """Yield safe partitions from least to most aggressive compaction."""
    seen = set()
    initial_keep = min(12, max(2, len(history) // 3))
    for keep in (initial_keep, max(2, initial_keep // 2), 2):
        if keep in seen:
            continue
        seen.add(keep)
        split, last_user = safe_split(history, keep=keep)
        if not split:
            continue
        archived = tuple(history[:split])
        retained = tuple(history[split:])
        # Preserve the active user instruction verbatim even during a long turn.
        if 0 <= last_user < split:
            retained = (history[last_user], *retained)
        yield archived, retained


def _summary_request(compact_input, *, max_output_tokens: int) -> ChatRequest:
    from .context_runtime import _COMPACTION_SYSTEM_PROMPT
    return ChatRequest(
        messages=(
            AIMessage(role=MessageRole.SYSTEM, content=_COMPACTION_SYSTEM_PROMPT),
            *compact_input,
        ),
        tools=(),
        tool_choice=ToolChoice.NONE,
        max_output_tokens=max_output_tokens,
    )


def _fit_compaction_input(compact_input, *, budget: int, max_output_tokens: int):
    """Codex-style oldest-first trimming before the helper request is sent."""
    compact_input = tuple(compact_input)
    while compact_input:
        request = _summary_request(compact_input, max_output_tokens=max_output_tokens)
        if estimate_tokens(request.messages) <= budget:
            return compact_input, request
        compact_input = _drop_oldest_safe_group(compact_input)
    raise RuntimeError("context compaction input exceeds request budget after trimming oldest history")


def _run_compaction_turn(rt, session, compact_input, *, budget: int, max_output_tokens: int, token):
    """Run one compact turn using the recovery loop used by open-source Codex.

    Codex waits for response.completed rather than interpreting provider-specific
    finish-reason strings. OpenAI-compatible Chat Completions already returns a
    final ModelResponse object, so non-empty text with no tool call is our matching
    success boundary. This intentionally accepts values such as ``length`` and
    ``eos_token`` instead of throwing the old false-negative RuntimeError.
    """
    compact_input, request = _fit_compaction_input(
        compact_input,
        budget=budget,
        max_output_tokens=max_output_tokens,
    )
    retries = 0
    usage = ModelUsage()

    while True:
        try:
            response = rt.model_executor.execute(rt.platform, session.profile_id, request, token)
        except ModelCancelled:
            _record_failed_compaction_usage(session, usage)
            raise
        except (AITransportError, TimeoutError) as exc:
            # Codex handles context-window failure separately by removing the
            # oldest history item and immediately retrying the compact turn.
            if _is_context_window_error(exc):
                trimmed = _drop_oldest_safe_group(compact_input)
                if not trimmed:
                    _record_failed_compaction_usage(session, usage)
                    raise RuntimeError(
                        "context window exceeded while compacting after oldest-history trimming"
                    ) from exc
                compact_input = trimmed
                compact_input, request = _fit_compaction_input(
                    compact_input,
                    budget=budget,
                    max_output_tokens=max_output_tokens,
                )
                retries = 0
                continue

            if retries < _COMPACTION_STREAM_RETRIES:
                retries += 1
                time.sleep(0.15 * (2 ** (retries - 1)))
                continue
            _record_failed_compaction_usage(session, usage)
            raise

        if not isinstance(response, ModelResponse):
            _record_failed_compaction_usage(session, usage)
            raise TypeError("compaction model must return ModelResponse")
        usage = _add_usage(usage, response.usage)

        summary = str(response.text or "").strip()
        if summary and not response.tool_calls:
            return summary, usage, retries + 1

        # A no-tools compaction request should not normally produce tool calls or
        # empty text. Retry it like Codex retries a failed compact stream instead
        # of failing the user's main turn on the first malformed helper response.
        if retries < _COMPACTION_STREAM_RETRIES:
            retries += 1
            time.sleep(0.15 * (2 ** (retries - 1)))
            continue

        _record_failed_compaction_usage(session, usage)
        if response.tool_calls:
            raise RuntimeError("context compaction model returned unexpected tool calls")
        raise RuntimeError("context compaction model returned an empty summary")


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
    partitions = tuple(_partition_candidates(history))
    if not partitions:
        raise RuntimeError("context budget exceeded with no safely compactable history")

    # The previous 2048-token helper cap was a local Loom choice and was one way
    # a valid summary could end with finish_reason=length. Codex does not impose
    # that extra small cap, so use Loom's already-reserved output budget here.
    max_output_tokens = max(1, rt.limits.output_reserve_tokens)
    last_error = "compacted context still exceeds request budget"

    for archived, retained in partitions:
        # Before spending another model request, make sure this retained suffix
        # leaves meaningful room for a summary in the normal agent request.
        probe = [
            *transient,
            AIMessage(role=MessageRole.SYSTEM, content="context summary"),
            *retained,
        ]
        if estimate_tokens(probe, tools) + _COMPACTION_FIT_MARGIN > budget:
            continue
        if len(probe) > rt.limits.max_messages:
            last_error = "compacted context still exceeds message limit"
            continue

        summary, usage, attempts = _run_compaction_turn(
            rt,
            session,
            archived,
            budget=budget,
            max_output_tokens=max_output_tokens,
            token=token,
        )
        candidate = [
            *transient,
            AIMessage(role=MessageRole.SYSTEM, content=summary),
            *retained,
        ]
        if (
            estimate_tokens(candidate, tools) + _COMPACTION_FIT_MARGIN <= budget
            and len(candidate) <= rt.limits.max_messages
        ):
            rt._commit_compaction_locked(
                session,
                summary=summary,
                repaired=repair,
                archived=archived,
                retained=retained,
                summary_source="auto" if attempts == 1 else "auto_retry",
                summary_usage=usage,
            )
            return [*transient, *session.messages], {
                "context_digest": envelope.digest,
                "auto_compacted": True,
                "compaction_attempts": attempts,
            }

        # The summary completed but was too large for this retained suffix. Try
        # the next safe, more aggressive partition rather than rejecting a valid
        # provider response because of local sizing.
        _record_failed_compaction_usage(session, usage)
        last_error = "compacted context still exceeds request budget"

    raise RuntimeError(last_error)
