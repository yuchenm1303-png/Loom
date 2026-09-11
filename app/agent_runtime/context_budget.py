"""Model-aware request budgeting and loss-aware Codex-style compaction."""
from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass
from typing import Sequence

from app.ai import AIMessage, ChatRequest, MessageRole, ModelResponse, ModelUsage, ToolChoice
from app.ai.errors import AIResponseError, AITransportError
from app.ai.execution_control import ModelCancelled

from .context_limits import ResolvedContextLimits, resolve_context_limits
from .context_reducer import (
    ContextReductionStats,
    reduce_tool_outputs,
    truncate_user_messages,
)
from .history import repair_tool_history
from .response_language import communication_language_message, infer_user_language


_COMPACTION_RETRY_LIMIT = 3
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


@dataclass(frozen=True, slots=True)
class ContextBudgetExceeded(RuntimeError):
    estimated_tokens: int
    input_budget_tokens: int
    tool_schema_tokens: int
    message_count: int
    reason: str

    def __str__(self) -> str:
        return (
            f"context budget exhausted ({self.reason}): estimated={self.estimated_tokens} tokens, "
            f"input_budget={self.input_budget_tokens}, tool_schemas={self.tool_schema_tokens}, "
            f"messages={self.message_count}"
        )


def estimate_tokens(messages, tools=()) -> int:
    """Conservative provider-neutral request estimate.

    The runtime deliberately owns this estimator rather than pretending every
    OpenAI-compatible endpoint shares a tokenizer. A provider-specific tokenizer
    can replace it later without changing context ownership or reducer semantics.
    """
    from .storage import _message_to_dict

    data = [_message_to_dict(message) for message in messages]
    schemas = [
        {"name": tool.name, "description": tool.description, "parameters": tool.input_schema}
        for tool in tools
    ]
    text = json.dumps([data, schemas], ensure_ascii=False)
    return (
        math.ceil(len(text.encode("utf-8")) / 3)
        + 8 * len(messages)
        + sum(4096 for message in messages if message.uses_vision)
    )


def estimate_tool_schema_tokens(tools=()) -> int:
    if not tools:
        return 0
    return max(0, estimate_tokens((), tools) - estimate_tokens((), ()))


def safe_split(messages, keep=12):
    """Retain the latest user and never separate tool calls from their outputs."""
    last_user = max(
        (index for index, message in enumerate(messages) if message.role is MessageRole.USER),
        default=-1,
    )
    pending = set()
    candidates = []
    for index, message in enumerate(messages):
        pending.update(call.call_id for call in message.tool_calls)
        if message.role is MessageRole.TOOL:
            pending.discard(message.tool_call_id)
        if not pending and 0 < index + 1 <= len(messages) - 2:
            candidates.append(index + 1)
    desired = max(1, len(messages) - keep)
    return max((index for index in candidates if index <= desired), default=0), last_user


def _normalized_finish_reason(value) -> str:
    return str(value or "").strip().casefold().replace("-", "_").replace(" ", "_")


def _finish_reason_is_incomplete(value) -> bool:
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
    # Preserve the active user instruction even when its original occurrence is
    # just before the archival boundary. Request-visible truncation may shorten a
    # pathological giant message, but durable canonical text is never altered.
    if 0 <= last_user < split:
        retained = (history[last_user], *retained)
    return retained


def _reduce_visible_history(
    history: Sequence[AIMessage],
    *,
    transient: Sequence[AIMessage],
    tools,
    limits: ResolvedContextLimits,
    target_tokens: int,
    truncate_users: bool,
) -> tuple[tuple[AIMessage, ...], ContextReductionStats]:
    target = max(1, int(target_tokens))
    visible, stats = reduce_tool_outputs(
        history,
        per_output_token_limit=limits.tool_output_token_limit,
    )
    if truncate_users:
        visible, user_stats = truncate_user_messages(
            visible,
            max_total_tokens=limits.recent_user_token_limit,
        )
        stats = stats.merged(user_stats)

    def total(candidate: Sequence[AIMessage]) -> int:
        return estimate_tokens([*transient, *candidate], tools)

    if total(visible) > target:
        visible, emergency_stats = reduce_tool_outputs(
            visible,
            per_output_token_limit=limits.tool_output_token_limit,
            target_total_tokens=target,
            estimate_total=total,
        )
        stats = stats.merged(emergency_stats)
    return visible, stats


def _select_partition(
    rt,
    history,
    transient,
    tools,
    limits: ResolvedContextLimits,
    *,
    summary_reserve: int,
):
    """Choose a canonical archive and a model-visible recent suffix that fit."""
    initial_keep = min(12, max(2, len(history) // 3))
    keep_candidates = []
    for keep in (initial_keep, max(2, initial_keep // 2), 2):
        if keep not in keep_candidates:
            keep_candidates.append(keep)

    target = max(1, limits.input_budget_tokens - limits.safety_tokens - max(1, summary_reserve))
    for keep in keep_candidates:
        split, last_user = safe_split(history, keep=keep)
        if not split:
            continue
        archived = tuple(history[:split])
        retained = _retained_for_split(history, split, last_user)
        visible_retained, reduction = _reduce_visible_history(
            retained,
            transient=transient,
            tools=tools,
            limits=limits,
            target_tokens=target,
            truncate_users=True,
        )
        probe = [
            *transient,
            AIMessage(role=MessageRole.SYSTEM, content="Compacted earlier context."),
            *visible_retained,
        ]
        if (
            estimate_tokens(probe, tools)
            + limits.safety_tokens
            + max(1, summary_reserve)
            <= limits.input_budget_tokens
            and len(probe) <= rt.limits.max_messages
        ):
            return archived, retained, visible_retained, reduction

    estimated = estimate_tokens([*transient, *history], tools)
    raise ContextBudgetExceeded(
        estimated_tokens=estimated,
        input_budget_tokens=limits.input_budget_tokens,
        tool_schema_tokens=estimate_tool_schema_tokens(tools),
        message_count=len(transient) + len(history),
        reason="recent messages or tool schemas cannot fit after safe reduction",
    )


def _trim_oldest_compaction_unit(messages):
    """Drop the oldest request item without leaving orphan tool outputs."""
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


def _metadata_base(
    *,
    envelope,
    communication_language: str,
    limits: ResolvedContextLimits,
    tools,
    estimated_before: int,
    estimated_after: int,
    reduction: ContextReductionStats,
) -> dict[str, object]:
    return {
        "context_digest": envelope.digest,
        "communication_language": communication_language,
        "context_limits": limits.as_dict(),
        "estimated_input_tokens_before": estimated_before,
        "estimated_input_tokens_after": estimated_after,
        "tool_schema_tokens": estimate_tool_schema_tokens(tools),
        **reduction.as_dict(),
    }


def prepare_context(rt, session, step, token):
    envelope = rt._context_envelope(session, step)
    transient = [
        message
        for message in rt._request_context_messages(session, step, envelope)
        if message.name != "loom_communication_language"
    ]
    instructions = rt.instruction_loader.load(session.workspace_dir)
    if instructions:
        transient.append(
            AIMessage(
                role=MessageRole.SYSTEM,
                name="loom_project_instructions",
                content=instructions,
            )
        )
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
    limits = resolve_context_limits(rt, session)
    hard_target = max(1, limits.input_budget_tokens - limits.safety_tokens)
    original_history = tuple(session.messages)
    estimated_before = estimate_tokens([*transient, *original_history], tools)

    # Tool outputs are observations, not immutable prompt prefix. Bound every
    # request-visible observation first, while leaving the durable transcript
    # untouched. If needed, collapse the oldest observations to structural stubs.
    visible_history, initial_reduction = _reduce_visible_history(
        original_history,
        transient=transient,
        tools=tools,
        limits=limits,
        target_tokens=hard_target,
        truncate_users=False,
    )
    visible_messages = [*transient, *visible_history]
    estimated_visible = estimate_tokens(visible_messages, tools)

    below_hard_limit = (
        estimated_visible <= hard_target
        and len(visible_messages) <= rt.limits.max_messages
    )
    below_auto_compact = estimated_visible <= limits.auto_compact_token_limit
    if below_hard_limit and below_auto_compact:
        return visible_messages, _metadata_base(
            envelope=envelope,
            communication_language=communication_language,
            limits=limits,
            tools=tools,
            estimated_before=estimated_before,
            estimated_after=estimated_visible,
            reduction=initial_reduction,
        )

    repair = repair_tool_history(
        session.messages,
        max_tool_result_chars=rt.limits.max_tool_result_chars,
    )
    history = tuple(repair.messages)

    # If no safe archival boundary exists, compaction cannot help. For a hard
    # overflow, make one final request-visible reduction pass that is allowed to
    # trim user text to the actual remaining budget. Canonical history remains
    # untouched, and fixed transient/tool-schema pressure still fails closed.
    possible_split, _ = safe_split(history, keep=min(12, max(2, len(history) // 3)))
    if not possible_split:
        if below_hard_limit:
            return visible_messages, _metadata_base(
                envelope=envelope,
                communication_language=communication_language,
                limits=limits,
                tools=tools,
                estimated_before=estimated_before,
                estimated_after=estimated_visible,
                reduction=initial_reduction,
            )

        fixed_tokens = estimate_tokens(transient, tools)
        emergency_user_budget = max(
            128,
            min(
                limits.recent_user_token_limit,
                hard_target - fixed_tokens - 64,
            ),
        )
        emergency_history, user_reduction = truncate_user_messages(
            history,
            max_total_tokens=emergency_user_budget,
        )

        def emergency_total(candidate: Sequence[AIMessage]) -> int:
            return estimate_tokens([*transient, *candidate], tools)

        emergency_history, tool_reduction = reduce_tool_outputs(
            emergency_history,
            per_output_token_limit=limits.tool_output_token_limit,
            target_total_tokens=hard_target,
            estimate_total=emergency_total,
        )
        emergency_reduction = user_reduction.merged(tool_reduction)
        emergency_messages = [*transient, *emergency_history]
        emergency_tokens = estimate_tokens(emergency_messages, tools)
        if (
            emergency_tokens <= hard_target
            and len(emergency_messages) <= rt.limits.max_messages
        ):
            metadata = _metadata_base(
                envelope=envelope,
                communication_language=communication_language,
                limits=limits,
                tools=tools,
                estimated_before=estimated_before,
                estimated_after=emergency_tokens,
                reduction=emergency_reduction,
            )
            metadata["emergency_user_truncation"] = True
            return emergency_messages, metadata

        raise ContextBudgetExceeded(
            estimated_tokens=emergency_tokens,
            input_budget_tokens=limits.input_budget_tokens,
            tool_schema_tokens=estimate_tool_schema_tokens(tools),
            message_count=len(emergency_messages),
            reason=(
                "non-archivable recent context cannot fit after emergency "
                "request-visible reduction"
            ),
        )

    language_message = communication_language_message(
        history,
        fallback=communication_language,
    )
    max_summary_output = max(1, limits.output_reserve_tokens)
    archived, retained, visible_retained, partition_reduction = _select_partition(
        rt,
        history,
        transient,
        tools,
        limits,
        summary_reserve=max_summary_output,
    )
    total_reduction = initial_reduction.merged(partition_reduction)

    # Codex reduces function outputs in its cloned compaction history before
    # calling the compaction endpoint. Do the same, but keep ``archived`` itself
    # canonical for Loom's durable checkpoint.
    compaction_input, summary_input_reduction = reduce_tool_outputs(
        archived,
        per_output_token_limit=limits.tool_output_token_limit,
    )
    total_reduction = total_reduction.merged(summary_input_reduction)
    total_usage = ModelUsage()
    model_attempts = 0
    trimmed_messages = 0
    last_failure = ""

    summary_probe = [
        *transient,
        AIMessage(role=MessageRole.SYSTEM, content="Compacted earlier context."),
        *visible_retained,
    ]
    summary_headroom = (
        limits.input_budget_tokens
        - estimate_tokens(summary_probe, tools)
        - limits.safety_tokens
    )
    max_summary_output = max(1, min(max_summary_output, summary_headroom))

    while model_attempts < _COMPACTION_RETRY_LIMIT:
        summary_request = _build_summary_request(
            compaction_input,
            max_output_tokens=max_summary_output,
            language_message=language_message,
        )

        while (
            estimate_tokens(summary_request.messages) + max_summary_output + limits.safety_tokens
            > limits.effective_context_window_tokens
            and len(compaction_input) > 1
        ):
            previous_len = len(compaction_input)
            compaction_input = _trim_oldest_compaction_unit(compaction_input)
            trimmed_messages += previous_len - len(compaction_input)
            summary_request = _build_summary_request(
                compaction_input,
                max_output_tokens=max_summary_output,
                language_message=language_message,
            )

        if (
            estimate_tokens(summary_request.messages) + max_summary_output + limits.safety_tokens
            > limits.effective_context_window_tokens
        ):
            raise ContextBudgetExceeded(
                estimated_tokens=estimate_tokens(summary_request.messages),
                input_budget_tokens=limits.input_budget_tokens,
                tool_schema_tokens=0,
                message_count=len(summary_request.messages),
                reason="compaction request cannot fit after reducing tool output and trimming old history",
            )

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
                last_failure = (
                    f"incomplete_finish:{_normalized_finish_reason(response.finish_reason)}"
                )

            if model_attempts < _COMPACTION_RETRY_LIMIT:
                if len(compaction_input) > 1:
                    previous_len = len(compaction_input)
                    compaction_input = _trim_oldest_compaction_unit(compaction_input)
                    trimmed_messages += previous_len - len(compaction_input)
                continue

            _charge_uncommitted_usage(session, total_usage)
            raise RuntimeError(
                "context compaction did not produce a complete summary after "
                f"{model_attempts} attempts ({last_failure})"
            )

        summary = str(response.text or "").strip()
        visible_candidate = [
            *transient,
            AIMessage(role=MessageRole.SYSTEM, content=summary),
            *visible_retained,
        ]
        candidate_tokens = estimate_tokens(visible_candidate, tools)
        if (
            candidate_tokens + limits.safety_tokens > limits.input_budget_tokens
            or len(visible_candidate) > rt.limits.max_messages
        ):
            # The summary itself may be unexpectedly verbose even inside the
            # requested output limit. Reduce recent observations once more before
            # sacrificing additional canonical history.
            visible_retained, retry_reduction = _reduce_visible_history(
                retained,
                transient=[
                    *transient,
                    AIMessage(role=MessageRole.SYSTEM, content=summary),
                ],
                tools=tools,
                limits=limits,
                target_tokens=hard_target,
                truncate_users=True,
            )
            total_reduction = total_reduction.merged(retry_reduction)
            visible_candidate = [
                *transient,
                AIMessage(role=MessageRole.SYSTEM, content=summary),
                *visible_retained,
            ]
            candidate_tokens = estimate_tokens(visible_candidate, tools)

        if (
            candidate_tokens + limits.safety_tokens > limits.input_budget_tokens
            or len(visible_candidate) > rt.limits.max_messages
        ):
            last_failure = "compacted_context_still_exceeds_budget"
            if model_attempts < _COMPACTION_RETRY_LIMIT and len(compaction_input) > 1:
                previous_len = len(compaction_input)
                compaction_input = _trim_oldest_compaction_unit(compaction_input)
                trimmed_messages += previous_len - len(compaction_input)
                continue
            _charge_uncommitted_usage(session, total_usage)
            raise ContextBudgetExceeded(
                estimated_tokens=candidate_tokens,
                input_budget_tokens=limits.input_budget_tokens,
                tool_schema_tokens=estimate_tool_schema_tokens(tools),
                message_count=len(visible_candidate),
                reason="compacted context still exceeds budget after all safe reducers",
            )

        rt._commit_compaction_locked(
            session,
            summary=summary,
            repaired=repair,
            archived=archived,
            retained=retained,
            summary_source=(
                "auto" if model_attempts == 1 and not trimmed_messages else "auto_retry"
            ),
            summary_usage=total_usage,
        )
        metadata = _metadata_base(
            envelope=envelope,
            communication_language=communication_language,
            limits=limits,
            tools=tools,
            estimated_before=estimated_before,
            estimated_after=candidate_tokens,
            reduction=total_reduction,
        )
        metadata.update(
            {
                "auto_compacted": True,
                "compaction_attempts": model_attempts,
                "compaction_trimmed_messages": trimmed_messages,
            }
        )
        return visible_candidate, metadata

    _charge_uncommitted_usage(session, total_usage)
    raise RuntimeError(f"context compaction failed ({last_failure or 'unknown'})")


__all__ = [
    "ContextBudgetExceeded",
    "estimate_tokens",
    "estimate_tool_schema_tokens",
    "prepare_context",
    "safe_split",
]
