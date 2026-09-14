"""Codex-parity model-context projection and automatic compaction."""
from __future__ import annotations

import json
import math
from contextlib import nullcontext
from dataclasses import replace
from typing import Sequence

from app.ai import AIMessage, ChatRequest, MessageRole, ModelResponse, ModelUsage, ToolChoice
from app.ai.errors import AIResponseError, AITransportError
from app.ai.execution_control import ModelCancelled

from .context_compaction import SUMMARIZATION_PROMPT, build_compacted_history
from .context_limits import ResolvedContextLimits, resolve_context_limits
from .history import repair_tool_history
from .response_language import communication_language_message, infer_user_language


_CONTEXT_WINDOW_ERROR_MARKERS = (
    "context window",
    "context length",
    "maximum context",
    "max context",
    "too many tokens",
    "token limit",
)
_IMAGE_TOKEN_ESTIMATE = 2048
_EMERGENCY_OMISSION_MARKER = "\n\n[... omitted for context budget ...]\n\n"


class ContextBudgetExceeded(RuntimeError):
    """Structured request-budget error that behaves like a normal exception."""

    def __init__(
        self,
        *,
        estimated_tokens: int,
        input_budget_tokens: int,
        tool_schema_tokens: int,
        message_count: int,
        reason: str,
    ) -> None:
        self.estimated_tokens = int(estimated_tokens)
        self.input_budget_tokens = int(input_budget_tokens)
        self.tool_schema_tokens = int(tool_schema_tokens)
        self.message_count = int(message_count)
        self.reason = str(reason)
        super().__init__(str(self))

    def __str__(self) -> str:
        return (
            f"context budget exhausted ({self.reason}): estimated={self.estimated_tokens} tokens, "
            f"input_budget={self.input_budget_tokens}, tool_schemas={self.tool_schema_tokens}, "
            f"messages={self.message_count}"
        )


def estimate_tokens(messages, tools=()) -> int:
    """Fallback token estimate used only when provider usage is unavailable.

    Codex drives normal auto-compaction from model/provider token accounting. Loom
    cannot assume one tokenizer across OpenAI-compatible providers, so this
    estimator is deliberately a fallback and a hard-request safety check, not the
    primary normal-path compaction clock.
    """
    from .storage import _message_to_dict

    data = []
    image_count = 0
    for message in messages:
        payload = _message_to_dict(message)
        content = payload.get("content")
        if isinstance(content, list):
            for item in content:
                if not isinstance(item, dict) or item.get("type") != "image":
                    continue
                image_count += 1
                image_url = str(item.get("image_url") or "")
                metadata, separator, _encoded = image_url.partition(",")
                if (
                    separator
                    and metadata.casefold().startswith("data:image/")
                    and ";base64" in metadata.casefold()
                ):
                    item["image_url"] = metadata + separator
        data.append(payload)
    schemas = [
        {"name": tool.name, "description": tool.description, "parameters": tool.input_schema}
        for tool in tools
    ]
    text = json.dumps([data, schemas], ensure_ascii=False)
    return (
        math.ceil(len(text.encode("utf-8")) / 3)
        + 8 * len(messages)
        + image_count * _IMAGE_TOKEN_ESTIMATE
    )


def estimate_tool_schema_tokens(tools=()) -> int:
    if not tools:
        return 0
    return max(0, estimate_tokens((), tools) - estimate_tokens((), ()))


def safe_split(messages, keep=12):
    """Compatibility helper: find a boundary that does not split a tool group."""
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


def _looks_like_context_window_error(exc: BaseException) -> bool:
    text = str(exc or "").casefold()
    return any(marker in text for marker in _CONTEXT_WINDOW_ERROR_MARKERS)


def _latest_provider_context_tokens(rt, session) -> int | None:
    """Return the newest surviving provider-reported context usage."""
    try:
        events = rt.store.events(session.session_id)
    except Exception:
        return None
    for event in reversed(events):
        kind = getattr(event.kind, "value", str(event.kind))
        if kind == "context_checkpointed":
            return None
        if kind not in {"model_response", "model_response_rejected"}:
            continue
        usage = event.data.get("usage") if isinstance(event.data, dict) else None
        if not isinstance(usage, dict):
            continue
        total = int(usage.get("total_tokens") or 0)
        if total > 0:
            return total
        input_tokens = int(usage.get("input_tokens") or 0)
        output_tokens = int(usage.get("output_tokens") or 0)
        if input_tokens or output_tokens:
            return input_tokens + output_tokens
    return None


def _trim_oldest_compaction_unit(messages: Sequence[AIMessage]) -> tuple[AIMessage, ...]:
    """Drop one oldest logical item without orphaning its tool outputs."""
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
        if message.role is MessageRole.TOOL and message.tool_call_id in pending:
            pending.discard(message.tool_call_id)
            index += 1
            continue
        break
    return items[index:]


def _build_summary_request(
    transient: Sequence[AIMessage],
    history: Sequence[AIMessage],
    *,
    max_output_tokens: int,
) -> ChatRequest:
    return ChatRequest(
        messages=tuple(
            [
                *transient,
                *history,
                AIMessage(role=MessageRole.USER, content=SUMMARIZATION_PROMPT),
            ]
        ),
        tools=(),
        tool_choice=ToolChoice.NONE,
        max_output_tokens=max_output_tokens,
    )


def _metadata(
    *,
    envelope,
    communication_language: str,
    limits: ResolvedContextLimits,
    tools,
    estimated_before: int,
    estimated_after: int,
    active_context_tokens: int,
    token_accounting_source: str,
) -> dict[str, object]:
    return {
        "context_digest": envelope.digest,
        "communication_language": communication_language,
        "context_limits": limits.as_dict(),
        "estimated_input_tokens_before": estimated_before,
        "estimated_input_tokens_after": estimated_after,
        "active_context_tokens": active_context_tokens,
        "token_accounting_source": token_accounting_source,
        "tool_schema_tokens": estimate_tool_schema_tokens(tools),
        "tool_outputs_reduced": 0,
        "tool_outputs_collapsed": 0,
        "user_messages_truncated": 0,
        "estimated_tokens_saved": 0,
    }


def _raise_if_cancelled(token) -> None:
    if bool(getattr(token, "cancelled", False)):
        raise ModelCancelled()


def _single_user_emergency_projection(
    transient: Sequence[AIMessage],
    history: Sequence[AIMessage],
    tools,
    *,
    target_tokens: int,
) -> tuple[list[AIMessage], int] | None:
    """Clip one oversized real-user item for this request without mutating history.

    There is nothing useful for a compaction model to summarize when the entire
    canonical window is one oversized user item. Keep both ends of that item,
    make the omission explicit to the model, and leave durable history intact.
    """
    if len(history) != 1:
        return None
    message = history[0]
    if (
        message.role is not MessageRole.USER
        or message.name
        or not isinstance(message.content, str)
        or not message.content
        or message.tool_calls
    ):
        return None

    original = message.content

    def candidate(keep_chars: int) -> list[AIMessage]:
        head = (keep_chars + 1) // 2
        tail = keep_chars // 2
        if tail:
            content = original[:head] + _EMERGENCY_OMISSION_MARKER + original[-tail:]
        else:
            content = original[:head] + _EMERGENCY_OMISSION_MARKER
        return [*transient, replace(message, content=content)]

    minimum = candidate(0)
    minimum_tokens = estimate_tokens(minimum, tools)
    if minimum_tokens > target_tokens:
        return None

    low, high = 0, len(original) - 1
    best = minimum
    best_tokens = minimum_tokens
    while low <= high:
        mid = (low + high) // 2
        visible = candidate(mid)
        cost = estimate_tokens(visible, tools)
        if cost <= target_tokens:
            best = visible
            best_tokens = cost
            low = mid + 1
        else:
            high = mid - 1
    return best, best_tokens


def _fit_replacement_message_limit(
    replacement: Sequence[AIMessage],
    *,
    transient_count: int,
    max_messages: int,
) -> tuple[AIMessage, ...]:
    """Keep the newest compacted user context plus the summary under a hard cap."""
    items = tuple(replacement)
    allowed = max(1, int(max_messages) - int(transient_count))
    while len(items) > allowed and len(items) > 1:
        items = items[1:]
    return items


def prepare_context(rt, session, step, token):
    """Project canonical history from the captured Step and compact when required."""
    _raise_if_cancelled(token)
    envelope = rt._context_envelope(session, step)
    transient = [
        message
        for message in rt._request_context_messages(session, step, envelope)
        if message.name != "loom_communication_language"
    ]

    request_state = getattr(step, "request_state", None)
    captured = bool(getattr(request_state, "captured", False))
    project_instructions = (
        request_state.project_instructions
        if captured
        else rt.instruction_loader.load(session.workspace_dir)
    )
    if project_instructions:
        transient.append(
            AIMessage(
                role=MessageRole.USER,
                name="loom_project_instructions",
                content=project_instructions,
            )
        )

    if captured:
        communication_language = request_state.communication_language
    else:
        communication_language = infer_user_language(
            session.messages,
            fallback=session.communication_language,
        )
    session.communication_language = communication_language
    transient.append(
        communication_language_message(
            () if captured else session.messages,
            fallback=communication_language,
        )
    )

    tools = step.tool_router.definitions()
    limits = (
        request_state.context_limits
        if captured and request_state.context_limits is not None
        else resolve_context_limits(rt, session)
    )
    canonical_history = tuple(session.messages)
    visible_messages = [*transient, *canonical_history]
    estimated_before = estimate_tokens(visible_messages, tools)

    # Compaction cannot reduce base/runtime/project context or tool schemas. Fail
    # closed before asking the model to summarize history that cannot possibly
    # make the next request fit.
    fixed_tokens = estimate_tokens(transient, tools)
    if fixed_tokens >= limits.input_budget_tokens:
        raise ContextBudgetExceeded(
            estimated_tokens=estimated_before,
            input_budget_tokens=limits.input_budget_tokens,
            tool_schema_tokens=estimate_tool_schema_tokens(tools),
            message_count=len(visible_messages),
            reason="fixed instructions/runtime context or tool schemas exceed the model input budget",
        )

    provider_tokens = _latest_provider_context_tokens(rt, session)
    if provider_tokens is None:
        active_context_tokens = estimated_before
        accounting_source = "fallback_estimate"
    else:
        active_context_tokens = provider_tokens
        accounting_source = "provider_usage"

    # If one giant user item is the only canonical history, summarization cannot
    # safely archive a smaller history first. Use a request-only projection and
    # preserve the full durable user message for future recovery/export.
    hard_target = max(1, limits.input_budget_tokens - limits.safety_tokens)
    if estimated_before > hard_target:
        emergency = _single_user_emergency_projection(
            transient,
            canonical_history,
            tools,
            target_tokens=hard_target,
        )
        if emergency is not None:
            emergency_visible, emergency_tokens = emergency
            metadata = _metadata(
                envelope=envelope,
                communication_language=communication_language,
                limits=limits,
                tools=tools,
                estimated_before=estimated_before,
                estimated_after=emergency_tokens,
                active_context_tokens=active_context_tokens,
                token_accounting_source=accounting_source,
            )
            metadata.update(
                {
                    "emergency_user_truncation": True,
                    "user_messages_truncated": 1,
                    "estimated_tokens_saved": max(0, estimated_before - emergency_tokens),
                }
            )
            return emergency_visible, metadata

    hard_request_fits = (
        estimated_before <= limits.input_budget_tokens
        and len(visible_messages) <= rt.limits.max_messages
    )
    token_limit_reached = active_context_tokens >= limits.auto_compact_token_limit
    if hard_request_fits and not token_limit_reached:
        return visible_messages, _metadata(
            envelope=envelope,
            communication_language=communication_language,
            limits=limits,
            tools=tools,
            estimated_before=estimated_before,
            estimated_after=estimated_before,
            active_context_tokens=active_context_tokens,
            token_accounting_source=accounting_source,
        )

    repair = repair_tool_history(
        canonical_history,
        max_tool_result_chars=rt.limits.max_tool_result_chars,
    )
    compact_input = tuple(repair.messages)
    if not compact_input:
        raise ContextBudgetExceeded(
            estimated_tokens=estimated_before,
            input_budget_tokens=limits.input_budget_tokens,
            tool_schema_tokens=estimate_tool_schema_tokens(tools),
            message_count=len(visible_messages),
            reason="empty canonical history cannot be compacted",
        )

    trimmed_messages = 0
    transport_retries = 0
    response: ModelResponse | None = None
    max_output_tokens = max(1, limits.output_reserve_tokens)

    while True:
        _raise_if_cancelled(token)
        request = _build_summary_request(
            transient,
            compact_input,
            max_output_tokens=max_output_tokens,
        )
        request_tokens = estimate_tokens(request.messages)
        if request_tokens + max_output_tokens > limits.effective_context_window_tokens:
            if len(compact_input) <= 1:
                raise ContextBudgetExceeded(
                    estimated_tokens=request_tokens,
                    input_budget_tokens=limits.input_budget_tokens,
                    tool_schema_tokens=0,
                    message_count=len(request.messages),
                    reason="compaction request cannot fit after trimming old history",
                )
            previous = len(compact_input)
            compact_input = _trim_oldest_compaction_unit(compact_input)
            trimmed_messages += previous - len(compact_input)
            transport_retries = 0
            continue

        try:
            stream_scope = getattr(rt, "_internal_model_stream_scope", None)
            with stream_scope() if callable(stream_scope) else nullcontext():
                candidate = rt.model_executor.execute(
                    rt.platform,
                    session.profile_id,
                    request,
                    token,
                )
        except ModelCancelled:
            raise
        except (AITransportError, AIResponseError, TimeoutError) as exc:
            if _looks_like_context_window_error(exc):
                if len(compact_input) <= 1:
                    raise
                previous = len(compact_input)
                compact_input = _trim_oldest_compaction_unit(compact_input)
                trimmed_messages += previous - len(compact_input)
                transport_retries = 0
                continue
            if (
                isinstance(exc, AITransportError)
                and exc.retryable
                and transport_retries < rt.limits.model_retries
            ):
                transport_retries += 1
                continue
            raise

        if not isinstance(candidate, ModelResponse):
            raise TypeError("compaction model must return ModelResponse")
        response = candidate
        break

    _raise_if_cancelled(token)
    if response.tool_calls:
        raise RuntimeError("context compaction model returned unexpected tool calls")
    summary = str(response.text or "").strip() or "(no summary available)"

    replacement = build_compacted_history(
        tuple(repair.messages),
        summary,
        token_counter=lambda messages: estimate_tokens(messages),
    )
    replacement = _fit_replacement_message_limit(
        replacement,
        transient_count=len(transient),
        max_messages=rt.limits.max_messages,
    )
    compacted_visible = [*transient, *replacement]
    estimated_after = estimate_tokens(compacted_visible, tools)
    if (
        estimated_after > limits.input_budget_tokens
        or len(compacted_visible) > rt.limits.max_messages
    ):
        raise ContextBudgetExceeded(
            estimated_tokens=estimated_after,
            input_budget_tokens=limits.input_budget_tokens,
            tool_schema_tokens=estimate_tool_schema_tokens(tools),
            message_count=len(compacted_visible),
            reason="Codex replacement history still exceeds the current model request budget",
        )

    rt._commit_compaction_locked(
        session,
        summary=summary,
        repaired=repair,
        archived=tuple(repair.messages),
        retained=(),
        summary_source="auto",
        summary_usage=response.usage,
        replacement_override=replacement,
    )

    committed_visible = [*transient, *session.messages]
    metadata = _metadata(
        envelope=envelope,
        communication_language=communication_language,
        limits=limits,
        tools=tools,
        estimated_before=estimated_before,
        estimated_after=estimated_after,
        active_context_tokens=active_context_tokens,
        token_accounting_source=accounting_source,
    )
    metadata.update(
        {
            "auto_compacted": True,
            "compaction_attempts": 1,
            "compaction_trimmed_messages": trimmed_messages,
        }
    )
    return committed_visible, metadata


__all__ = [
    "ContextBudgetExceeded",
    "estimate_tokens",
    "estimate_tool_schema_tokens",
    "prepare_context",
    "safe_split",
]