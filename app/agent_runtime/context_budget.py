"""Token-aware request budgeting and Codex-style in-turn compaction."""
from __future__ import annotations

import json
import math
import time

from app.ai import AIMessage, ChatRequest, MessageRole, ModelResponse, ModelUsage, ToolChoice
from app.ai.errors import AIResponseError, AITransportError
from app.ai.execution_control import ModelCancelled
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
_CONTEXT_WINDOW_ERROR_MARKERS = (
    "context window",
    "context length",
    "maximum context",
    "max context",
    "too many tokens",
    "token limit",
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


def _normalized_finish_reason(value) -> str:
    return str(value or "").strip().casefold().replace("-", "_").replace(" ", "_")


def _finish_reason_is_incomplete(value) -> bool:
    """Only reject finish reasons that explicitly mean the response was not complete.

    Codex waits for a completed response event instead of maintaining a provider-
    specific success allow-list. OpenAI-compatible providers expose many different
    successful finish strings (for example ``eos_token``), so Loom must not reject
    an otherwise complete summary just because the success spelling is unfamiliar.
    """
    reason = _normalized_finish_reason(value)
    return bool(reason and any(marker in reason for marker in _INCOMPLETE_FINISH_MARKERS))


def _summary_is_complete(response: ModelResponse) -> bool:
    return bool(
        str(response.text or "").strip()
        and not response.tool_calls
        and not _finish_reason_is_incomplete(response.finish_reason)
    )


def _looks_like_context_window_error(exc: BaseException) -> bool:
    text = str(exc or "").casefold()
    return any(marker in text for marker in _CONTEXT_WINDOW_ERROR_MARKERS)


def _add_usage(left: ModelUsage, right: ModelUsage) -> ModelUsage:
    return ModelUsage(
        input_tokens=left.input_tokens + right.input_tokens,
        output_tokens=left.output_tokens + right.output_tokens,
        total_tokens=left.total_tokens + right.total_tokens,
    )


def _retained_for_split(history, split: int, last_user: int):
    retained = tuple(history[split:])
    # Preserve the verbatim active user instruction even during a single long turn.
    if 0 <= last_user < split:
        retained = (history[last_user], *retained)
    return retained


def _select_partition(rt, history, transient, tools, budget):
    """Choose a safe recent suffix that still leaves room for a compact summary."""
    initial_keep = min(12, max(2, len(history) // 3))
    keep_candidates = []
    for keep in (initial_keep, max(2, initial_keep // 2), 2):
        if keep not in keep_candidates:
            keep_candidates.append(keep)

    for keep in keep_candidates:
        split, last_user = safe_split(history, keep=keep)
        if not split:
            continue
        archived = tuple(history[:split])
        retained = _retained_for_split(history, split, last_user)
        probe = [
            *transient,
            AIMessage(role=MessageRole.SYSTEM, content="Compacted earlier context."),
            *retained,
        ]
        if (
            estimate_tokens(probe, tools) + _COMPACTION_SAFETY_TOKENS <= budget
            and len(probe) <= rt.limits.max_messages
        ):
            return archived, retained

    raise RuntimeError(
        "context budget is exhausted by recent messages or tool schemas after maximum safe compaction"
    )


def _trim_oldest_compaction_unit(messages):
    """Drop the oldest request item without leaving orphan tool outputs.

    Codex retries compaction after removing the oldest history item when the
    compaction request itself exceeds the model window. Loom applies the same
    policy while keeping Chat Completions tool-call pairs valid.
    """
    items = tuple(messages)
    if not items:
        return items

    first = items[0]
    if not first.tool_calls:
        return items[1:]

    pending = {call.call_id for call in first.tool_calls}
    index = 1
    while index < len(items) and pending:
        message = items[index]
        if message.role is MessageRole.TOOL:
            pending.discard(message.tool_call_id)
            index += 1
            continue
        break
    return items[index:]


def _build_summary_request(archived, *, max_output_tokens: int) -> ChatRequest:
    from .context_runtime import _COMPACTION_SYSTEM_PROMPT

    # Codex appends its synthesized compaction instruction as a user item at the
    # end of the history being compacted. Keep that shape here instead of adding
    # another permanent system-level instruction.
    return ChatRequest(
        messages=(
            *archived,
            AIMessage(role=MessageRole.USER, content=_COMPACTION_SYSTEM_PROMPT),
        ),
        tools=(),
        tool_choice=ToolChoice.NONE,
        max_output_tokens=max_output_tokens,
    )


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
    archived, retained = _select_partition(rt, history, transient, tools, budget)

    # Keep the canonical archive intact for Loom's checkpoint. Only the temporary
    # compaction request is trimmed, exactly like Codex trimming its cloned history.
    compaction_input = archived
    total_usage = ModelUsage()
    model_attempts = 0
    trimmed_messages = 0
    last_failure = ""

    # The old 2048-token ceiling was an unnecessary truncation source. Codex does
    # not impose that extra cap, so use Loom's full reserved model-output budget.
    max_summary_output = max(1, rt.limits.output_reserve_tokens)

    while model_attempts < _COMPACTION_RETRY_LIMIT:
        summary_request = _build_summary_request(
            compaction_input,
            max_output_tokens=max_summary_output,
        )

        # Mirror Codex's ContextWindowExceeded handling: remove oldest cloned
        # history and retry, preserving recent context and the canonical archive.
        while estimate_tokens(summary_request.messages) > budget and compaction_input:
            previous_len = len(compaction_input)
            compaction_input = _trim_oldest_compaction_unit(compaction_input)
            trimmed_messages += previous_len - len(compaction_input)
            summary_request = _build_summary_request(
                compaction_input,
                max_output_tokens=max_summary_output,
            )

        if not compaction_input:
            raise RuntimeError("context compaction request cannot fit even after trimming old history")

        try:
            response = rt.model_executor.execute(
                rt.platform,
                session.profile_id,
                summary_request,
                token,
            )
        except ModelCancelled:
            raise
        except (AITransportError, AIResponseError, TimeoutError) as exc:
            if _looks_like_context_window_error(exc) and compaction_input:
                previous_len = len(compaction_input)
                compaction_input = _trim_oldest_compaction_unit(compaction_input)
                trimmed_messages += previous_len - len(compaction_input)
                last_failure = "provider_context_window_exceeded"
                continue
            model_attempts += 1
            last_failure = type(exc).__name__
            if model_attempts >= _COMPACTION_RETRY_LIMIT:
                raise
            time.sleep(0.15 * (2 ** (model_attempts - 1)))
            continue

        if not isinstance(response, ModelResponse):
            raise TypeError("compaction model must return ModelResponse")

        model_attempts += 1
        total_usage = _add_usage(total_usage, response.usage)

        if not _summary_is_complete(response):
            if not str(response.text or "").strip():
                last_failure = "empty_summary"
            elif response.tool_calls:
                last_failure = "unexpected_tool_calls"
            else:
                last_failure = f"incomplete_finish:{_normalized_finish_reason(response.finish_reason)}"

            if model_attempts < _COMPACTION_RETRY_LIMIT:
                # A length/incomplete completion often means the compaction task
                # is still too large. Codex makes the same retry cheaper by
                # removing the oldest cloned history item before sampling again.
                if compaction_input:
                    previous_len = len(compaction_input)
                    compaction_input = _trim_oldest_compaction_unit(compaction_input)
                    trimmed_messages += previous_len - len(compaction_input)
                continue

            from .runtime import _add_usage as runtime_add_usage
            session.usage = runtime_add_usage(session.usage, total_usage)
            raise RuntimeError(
                f"context compaction did not produce a complete summary after {model_attempts} attempts ({last_failure})"
            )

        summary = str(response.text or "").strip()
        candidate = [
            *transient,
            AIMessage(role=MessageRole.SYSTEM, content=summary),
            *retained,
        ]
        if (
            estimate_tokens(candidate, tools) + _COMPACTION_SAFETY_TOKENS > budget
            or len(candidate) > rt.limits.max_messages
        ):
            last_failure = "compacted_context_still_exceeds_budget"
            if model_attempts < _COMPACTION_RETRY_LIMIT and compaction_input:
                previous_len = len(compaction_input)
                compaction_input = _trim_oldest_compaction_unit(compaction_input)
                trimmed_messages += previous_len - len(compaction_input)
                continue
            from .runtime import _add_usage as runtime_add_usage
            session.usage = runtime_add_usage(session.usage, total_usage)
            raise RuntimeError("compacted context still exceeds request budget after retries")

        rt._commit_compaction_locked(
            session,
            summary=summary,
            repaired=repair,
            archived=archived,
            retained=retained,
            summary_source="auto" if model_attempts == 1 and not trimmed_messages else "auto_retry",
            summary_usage=total_usage,
        )
        return [*transient, *session.messages], {
            "context_digest": envelope.digest,
            "auto_compacted": True,
            "compaction_attempts": model_attempts,
            "compaction_trimmed_messages": trimmed_messages,
        }

    raise RuntimeError(f"context compaction failed ({last_failure or 'unknown'})")
