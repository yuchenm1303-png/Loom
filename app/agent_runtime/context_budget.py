"""Token-aware request budgeting and Codex-style in-turn compaction."""
from __future__ import annotations

import json
import math
import time

from app.ai import AIMessage, ChatRequest, MessageRole, ModelResponse, ModelUsage, ToolChoice
from app.ai.errors import AIResponseError, AITransportError
from app.ai.execution_control import ModelCancelled
from .history import repair_tool_history
from .response_language import communication_language_message, infer_user_language


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


def _charge_uncommitted_usage(session, usage: ModelUsage) -> None:
    if not (usage.input_tokens or usage.output_tokens or usage.total_tokens):
        return
    from .runtime import _add_usage as runtime_add_usage
    session.usage = runtime_add_usage(session.usage, usage)


def _retained_for_split(history, split: int, last_user: int):
    retained = tuple(history[split:])
    # Preserve the verbatim active user instruction even during a single long turn.
    if 0 <= last_user < split:
        retained = (history[last_user], *retained)
    return retained


def _select_partition(rt, history, transient, tools, budget, *, summary_reserve: int):
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
            estimate_tokens(probe, tools)
            + _COMPACTION_SAFETY_TOKENS
            + max(1, summary_reserve)
            <= budget
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


def _build_summary_request(
    archived,
    *,
    max_output_tokens: int,
    language_message: AIMessage,
) -> ChatRequest:
    from .context_runtime import _COMPACTION_SYSTEM_PROMPT

    # Compaction is a separate model task. Carry the user-language anchor into it
    # explicitly so English-heavy logs/tool output cannot rewrite the conversation
    # language at the exact point old user messages are being summarized away.
    return ChatRequest(
        messages=(
            language_message,
            *archived,
            AIMessage(role=MessageRole.USER, content=_COMPACTION_SYSTEM_PROMPT),
        ),
        tools=(),
        tool_choice=ToolChoice.NONE,
        max_output_tokens=max_output_tokens,
    )


def prepare_context(rt, session, step, token):
    envelope = rt._context_envelope(session, step)
    # Some runtime layers append advisory system context after ContextAgentRuntime.
    # Normalize the language anchor here, after all of those layers and project
    # instructions, so it is always the final transient instruction before canonical
    # conversation history. This prevents later English runtime text from diluting it.
    transient = [
        message
        for message in rt._request_context_messages(session, step, envelope)
        if message.name != "loom_communication_language"
    ]
    instructions = rt.instruction_loader.load(session.workspace_dir)
    if instructions:
        transient.append(AIMessage(role=MessageRole.SYSTEM, name="loom_project_instructions", content=instructions))
    communication_language = infer_user_language(
        session.messages,
        fallback=session.communication_language,
    )
    session.communication_language = communication_language
    transient.append(
        communication_language_message(
            session.messages,
            fallback=communication_language,
        )
    )

    tools = step.tool_router.definitions()
    budget = rt.limits.context_window_tokens - rt.limits.output_reserve_tokens
    messages = [*transient, *session.messages]
    if estimate_tokens(messages, tools) <= budget and len(messages) <= rt.limits.max_messages:
        return messages, {
            "context_digest": envelope.digest,
            "communication_language": communication_language,
        }

    repair = repair_tool_history(session.messages, max_tool_result_chars=rt.limits.max_tool_result_chars)
    history = tuple(repair.messages)
    language_message = communication_language_message(
        history,
        fallback=communication_language,
    )
    max_summary_output = max(1, rt.limits.output_reserve_tokens)
    archived, retained = _select_partition(
        rt,
        history,
        transient,
        tools,
        budget,
        summary_reserve=max_summary_output,
    )

    # Keep the canonical archive intact for Loom's checkpoint. Only the temporary
    # compaction request is trimmed, exactly like Codex trimming its cloned history.
    compaction_input = archived
    total_usage = ModelUsage()
    model_attempts = 0
    trimmed_messages = 0
    last_failure = ""

    # A compaction summary becomes input on the next request. Bound its output by
    # the space that remains after transient context, tool schemas, retained
    # history, and the safety margin. Previously Loom partitioned with a tiny
    # placeholder but allowed a full output-reserve-sized summary, so the model
    # could repeatedly produce a valid summary that could never fit back into the
    # request being compacted.
    summary_probe = [
        *transient,
        AIMessage(role=MessageRole.SYSTEM, content="Compacted earlier context."),
        *retained,
    ]
    summary_headroom = (
        budget
        - estimate_tokens(summary_probe, tools)
        - _COMPACTION_SAFETY_TOKENS
    )
    max_summary_output = max(1, min(max_summary_output, summary_headroom))

    while model_attempts < _COMPACTION_RETRY_LIMIT:
        summary_request = _build_summary_request(
            compaction_input,
            max_output_tokens=max_summary_output,
            language_message=language_message,
        )

        # Mirror Codex's ContextWindowExceeded handling: remove oldest cloned
        # history and retry, preserving recent context and the canonical archive.
        while estimate_tokens(summary_request.messages) > budget and len(compaction_input) > 1:
            previous_len = len(compaction_input)
            compaction_input = _trim_oldest_compaction_unit(compaction_input)
            trimmed_messages += previous_len - len(compaction_input)
            summary_request = _build_summary_request(
                compaction_input,
                max_output_tokens=max_summary_output,
                language_message=language_message,
            )

        if estimate_tokens(summary_request.messages) > budget:
            raise RuntimeError("context compaction request cannot fit after trimming old history")

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
            if _looks_like_context_window_error(exc):
                # Codex only drops more history while there is more than one
                # request item left; otherwise it returns the context-window error.
                if len(compaction_input) > 1:
                    previous_len = len(compaction_input)
                    compaction_input = _trim_oldest_compaction_unit(compaction_input)
                    trimmed_messages += previous_len - len(compaction_input)
                    last_failure = "provider_context_window_exceeded"
                    continue
                _charge_uncommitted_usage(session, total_usage)
                raise

            model_attempts += 1
            last_failure = type(exc).__name__
            if model_attempts >= _COMPACTION_RETRY_LIMIT:
                _charge_uncommitted_usage(session, total_usage)
                raise
            time.sleep(0.15 * (2 ** (model_attempts - 1)))
            continue

        if not isinstance(response, ModelResponse):
            _charge_uncommitted_usage(session, total_usage)
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
                # is still too large. Retry after dropping the oldest cloned item,
                # as Codex does for an oversized compaction request.
                if len(compaction_input) > 1:
                    previous_len = len(compaction_input)
                    compaction_input = _trim_oldest_compaction_unit(compaction_input)
                    trimmed_messages += previous_len - len(compaction_input)
                continue

            _charge_uncommitted_usage(session, total_usage)
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
            if model_attempts < _COMPACTION_RETRY_LIMIT:
                if len(compaction_input) > 1:
                    previous_len = len(compaction_input)
                    compaction_input = _trim_oldest_compaction_unit(compaction_input)
                    trimmed_messages += previous_len - len(compaction_input)
                continue
            _charge_uncommitted_usage(session, total_usage)
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
            "communication_language": communication_language,
            "auto_compacted": True,
            "compaction_attempts": model_attempts,
            "compaction_trimmed_messages": trimmed_messages,
        }

    _charge_uncommitted_usage(session, total_usage)
    raise RuntimeError(f"context compaction failed ({last_failure or 'unknown'})")
