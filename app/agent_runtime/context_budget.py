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

from .context_compaction import build_compacted_history, summarization_prompt
from .context_limits import ResolvedContextLimits, resolve_context_limits
from .context_reducer import ContextReductionStats, reduce_tool_outputs
from .history import repair_tool_history
from .response_language import (
    communication_language_message,
    infer_user_language,
    text_matches_communication_language,
)
from .turn_response_validation import (
    contains_serialized_tool_protocol,
    visible_model_text,
)


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

# Calibration of the fallback estimator against provider-reported input tokens.
# Bounds keep a hostile or degenerate provider from either inflating history out
# of the window or suppressing compaction entirely.
_CALIBRATION_MIN_SAMPLES = 3
_CALIBRATION_SAMPLE_LIMIT = 12
_CALIBRATION_EVENT_SCAN_LIMIT = 240
_CALIBRATION_MIN_FACTOR = 0.7
_CALIBRATION_MAX_FACTOR = 2.0


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


def is_context_window_error(exc: BaseException) -> bool:
    """Whether a provider rejected a request for being too long for the model."""
    text = str(exc or "").casefold()
    return any(marker in text for marker in _CONTEXT_WINDOW_ERROR_MARKERS)


def request_forced_compaction(rt, session) -> None:
    """Make the next ``prepare_context`` compact even if its budget says it fits.

    A provider that rejects a request as too long is the only authoritative
    statement about that model's real window. Loom's own limits were wrong by
    definition at that point, so retrying the same request — which is what
    malformed-response recovery does — cannot succeed.
    """
    sessions = getattr(rt, "_forced_compaction_sessions", None)
    if sessions is None:
        sessions = set()
        rt._forced_compaction_sessions = sessions
    sessions.add(str(getattr(session, "session_id", "") or ""))


def _consume_forced_compaction(rt, session) -> bool:
    """Read and clear the one-shot flag so a forced pass never repeats itself."""
    sessions = getattr(rt, "_forced_compaction_sessions", None)
    if not sessions:
        return False
    key = str(getattr(session, "session_id", "") or "")
    if key not in sessions:
        return False
    sessions.discard(key)
    return True


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


def _observed_context_ceiling(rt, session) -> int | None:
    """Smallest request size this provider has already refused as too long.

    When no metadata declares the model's window, a rejection is the only hard
    fact available about it. Recording that size turns one failed request into a
    durable bound, so an unknown-window session converges instead of rediscovering
    the ceiling every time history grows back.
    """
    try:
        events = rt.store.events(session.session_id)
    except Exception:
        return None

    sizes: list[int] = []
    for event in tuple(events)[-_CALIBRATION_EVENT_SCAN_LIMIT:]:
        kind = getattr(event.kind, "value", str(event.kind))
        if kind != "model_response_rejected":
            continue
        data = event.data if isinstance(event.data, dict) else {}
        if data.get("reason") != "context_window_exceeded":
            continue
        size = int(data.get("rejected_input_tokens") or 0)
        if size > 0:
            sizes.append(size)
    return min(sizes) if sizes else None


def _largest_accepted_input_tokens(rt, session) -> int:
    """Largest request size this provider has actually served in this session."""
    try:
        events = rt.store.events(session.session_id)
    except Exception:
        return 0
    best = 0
    for event in tuple(events)[-_CALIBRATION_EVENT_SCAN_LIMIT:]:
        kind = getattr(event.kind, "value", str(event.kind))
        if kind != "model_response":
            continue
        data = event.data if isinstance(event.data, dict) else {}
        usage = data.get("usage")
        if isinstance(usage, dict):
            best = max(best, int(usage.get("input_tokens") or 0))
    return best


def _resolved_input_budget(
    rt,
    session,
    limits: ResolvedContextLimits,
    *,
    fixed_tokens: int,
) -> int | None:
    """The ceiling to budget compaction against, or ``None`` when none is honest.

    Codex budgets only against declared model metadata and otherwise leaves the
    window unset, letting the provider be the authority. Loom follows that, plus
    one thing Codex does not need: a ceiling this provider proved by refusing a
    request.

    That proof is only trusted when it is self-consistent. Providers return
    context-length errors for reasons that have nothing to do with length — a
    misrouted model, a gateway fault — and believing a bogus tiny ceiling would
    wedge the session far more thoroughly than not budgeting at all.
    """
    if limits.window_known:
        return limits.input_budget_tokens
    ceiling = _observed_context_ceiling(rt, session)
    if ceiling is None:
        return None
    # Stay clear of the proven-bad size rather than probing it again.
    budget = max(1, ceiling * 9 // 10)
    if budget <= int(fixed_tokens):
        # Compaction cannot shrink base context, so this "ceiling" would make
        # every request impossible. It is not a statement about history length.
        return None
    if budget <= _largest_accepted_input_tokens(rt, session):
        # The same provider already served a larger request; the rejection
        # contradicts its own behaviour and is not a usable bound.
        return None
    return budget


def _estimator_calibration(rt, session) -> tuple[float, int]:
    """Measure this session's fallback-estimator bias from provider accounting.

    ``estimate_tokens`` cannot know any specific provider's tokenizer, so it is
    deliberately pessimistic. Every committed model step already records both
    Loom's pre-request estimate and the provider's reported ``input_tokens``,
    which makes that bias directly observable rather than assumed. Compacting on
    an uncalibrated estimate throws away context the model still has room for,
    and summarization is far more expensive than carrying the history.
    """
    try:
        events = rt.store.events(session.session_id)
    except Exception:
        return 1.0, 0

    factors: list[float] = []
    pending: dict | None = None
    for event in tuple(events)[-_CALIBRATION_EVENT_SCAN_LIMIT:]:
        kind = getattr(event.kind, "value", str(event.kind))
        data = event.data if isinstance(event.data, dict) else {}
        if kind == "model_requested":
            pending = data
            continue
        if kind != "model_response":
            continue
        request_data, pending = pending, None
        if request_data is None:
            continue
        # Always the raw estimator output: metadata never stores calibrated
        # values, otherwise each turn would calibrate against its own correction.
        estimated = int(
            request_data.get("estimated_input_tokens_after")
            or request_data.get("estimated_input_tokens_before")
            or 0
        )
        usage = data.get("usage")
        reported = int(usage.get("input_tokens") or 0) if isinstance(usage, dict) else 0
        if estimated <= 0 or reported <= 0:
            continue
        factors.append(reported / float(estimated))

    if len(factors) < _CALIBRATION_MIN_SAMPLES:
        return 1.0, len(factors)

    sample = sorted(factors[-_CALIBRATION_SAMPLE_LIMIT:])
    middle = len(sample) // 2
    median = (
        sample[middle]
        if len(sample) % 2
        else (sample[middle - 1] + sample[middle]) / 2
    )
    factor = min(_CALIBRATION_MAX_FACTOR, max(_CALIBRATION_MIN_FACTOR, median))
    return factor, len(sample)


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
    communication_language: str,
    max_output_tokens: int | None,
) -> ChatRequest:
    return ChatRequest(
        messages=tuple(
            [
                *transient,
                *history,
                AIMessage(
                    role=MessageRole.USER,
                    content=summarization_prompt(communication_language),
                ),
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
    reduction_stats: ContextReductionStats | None = None,
    calibration: float = 1.0,
    calibration_samples: int = 0,
    input_budget: int | None = None,
) -> dict[str, object]:
    metadata: dict[str, object] = {
        "context_digest": envelope.digest,
        "communication_language": communication_language,
        "context_limits": limits.as_dict(),
        # Raw estimator output. `_estimator_calibration` reads these back, so
        # storing calibrated values here would compound the correction.
        "estimated_input_tokens_before": estimated_before,
        "estimated_input_tokens_after": estimated_after,
        "estimator_calibration": round(float(calibration), 4),
        "estimator_calibration_samples": int(calibration_samples),
        "calibrated_input_tokens_before": int(math.ceil(estimated_before * calibration)),
        "calibrated_input_tokens_after": int(math.ceil(estimated_after * calibration)),
        # A fallback window means no model profile declared its real context
        # size, so every threshold below is a guess about someone else's model.
        "context_window_fallback": limits.source == "runtime_fallback",
        "effective_input_budget_tokens": input_budget,
        "context_budget_source": (
            "model_metadata"
            if limits.window_known
            else ("observed_provider_limit" if input_budget is not None else "unbounded")
        ),
        "active_context_tokens": active_context_tokens,
        "token_accounting_source": token_accounting_source,
        "tool_schema_tokens": estimate_tool_schema_tokens(tools),
        "tool_outputs_reduced": 0,
        "tool_outputs_collapsed": 0,
        "user_messages_truncated": 0,
        "estimated_tokens_saved": 0,
    }
    if reduction_stats is not None:
        metadata.update(reduction_stats.as_dict())
    return metadata


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
    calibration, calibration_samples = _estimator_calibration(rt, session)

    def calibrated(raw_tokens: int) -> int:
        """Raw estimator tokens restated in this provider's own accounting."""
        return int(math.ceil(int(raw_tokens) * calibration))

    # Compaction cannot reduce base/runtime/project context or tool schemas. Fail
    # closed before asking the model to summarize history that cannot possibly
    # make the next request fit. This is a structural impossibility check rather
    # than a compaction decision, so it keeps using the resolved limits even when
    # the window is a fallback: that number is then an absolute sanity ceiling.
    fixed_tokens = estimate_tokens(transient, tools)
    if calibrated(fixed_tokens) >= limits.input_budget_tokens:
        raise ContextBudgetExceeded(
            estimated_tokens=estimated_before,
            input_budget_tokens=limits.input_budget_tokens,
            tool_schema_tokens=estimate_tool_schema_tokens(tools),
            message_count=len(visible_messages),
            reason="fixed instructions/runtime context or tool schemas exceed the model input budget",
        )

    # ``None`` means no declared window and no credible provider rejection to
    # learn from, so there is no honest number to compact against. Inventing one
    # is what turned a 21k conversation into four compactions in three minutes;
    # send the request and let the provider rule instead.
    input_budget = _resolved_input_budget(
        rt,
        session,
        limits,
        fixed_tokens=calibrated(fixed_tokens),
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
    # Also structural: one oversized user item cannot be summarized into a
    # smaller history, so the sanity ceiling applies here too.
    hard_target = max(1, limits.input_budget_tokens - limits.safety_tokens)
    if calibrated(estimated_before) > hard_target:
        emergency = _single_user_emergency_projection(
            transient,
            canonical_history,
            tools,
            # The projection searches in raw estimator units, so the target has
            # to be converted back out of provider accounting.
            target_tokens=max(1, int(hard_target / calibration)),
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
                calibration=calibration,
                calibration_samples=calibration_samples,
            input_budget=input_budget,
            )
            metadata.update(
                {
                    "emergency_user_truncation": True,
                    "user_messages_truncated": 1,
                    "estimated_tokens_saved": max(0, estimated_before - emergency_tokens),
                }
            )
            return emergency_visible, metadata

    # Large historical tool observations should not force a semantic handoff by
    # themselves. Project bounded previews/collapsed stubs into this request copy
    # first; the canonical durable transcript remains untouched. This reducer is
    # only invoked when the raw request no longer fits, so roomy model windows
    # still receive full recent tool observations.
    projected_history = canonical_history
    projected_visible = visible_messages
    estimated_projected = estimated_before
    reduction_stats = ContextReductionStats()
    if input_budget is not None and calibrated(estimated_before) > input_budget:
        projected_history, reduction_stats = reduce_tool_outputs(
            canonical_history,
            per_output_token_limit=limits.tool_output_token_limit,
            target_total_tokens=hard_target,
            estimate_total=lambda history: estimate_tokens([*transient, *history], tools),
        )
        projected_visible = [*transient, *projected_history]
        estimated_projected = estimate_tokens(projected_visible, tools)

    calibrated_active_context_tokens = active_context_tokens
    if accounting_source == "fallback_estimate":
        # No provider accounting yet, so this threshold is being driven by the
        # estimator and has to be restated in provider units like every other
        # budget comparison here.
        calibrated_active_context_tokens = calibrated(estimated_projected)

    # Keep Loom's legacy message-count safety cap as a secondary compaction
    # trigger. It is not the normal token clock, but compacting here prevents the
    # outer TurnRunner guard from terminating a turn when a safe checkpoint can
    # still reduce the request.
    # A provider that already rejected this history as too long outranks every
    # local budget below, so that verdict forces one compaction pass.
    forced_compaction = _consume_forced_compaction(rt, session)
    hard_request_fits = (
        not forced_compaction
        and (
            input_budget is None
            or calibrated(estimated_projected) <= input_budget
        )
        and len(projected_visible) <= rt.limits.max_messages
    )
    # Codex leaves `auto_compact_token_limit` unset for a model it has no metadata
    # for, so this trigger simply never fires there. Guessing a threshold instead
    # is what made a 21k conversation compact four times in three minutes.
    token_limit_reached = (
        limits.window_known
        and calibrated_active_context_tokens >= limits.auto_compact_token_limit
    )
    if hard_request_fits and not token_limit_reached:
        return projected_visible, _metadata(
            envelope=envelope,
            communication_language=communication_language,
            limits=limits,
            tools=tools,
            estimated_before=estimated_before,
            estimated_after=estimated_projected,
            active_context_tokens=calibrated_active_context_tokens,
            token_accounting_source=accounting_source,
            reduction_stats=reduction_stats,
            calibration=calibration,
            calibration_samples=calibration_samples,
            input_budget=input_budget,
        )

    # The compaction model may use the request-only reduced projection, but the
    # durable checkpoint must archive/repair canonical history. Keep those two
    # responsibilities separate so context budgeting never destroys evidence.
    repair = repair_tool_history(
        canonical_history,
        max_tool_result_chars=rt.limits.max_tool_result_chars,
    )
    projected_repair = repair_tool_history(
        projected_history,
        max_tool_result_chars=rt.limits.max_tool_result_chars,
    )
    compact_input = tuple(projected_repair.messages)
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
    response_retries = 0
    response_attempts = 0
    summary_usage = ModelUsage()
    summary = ""
    # Reserve a numeric output budget for the arithmetic below, but only impose
    # it on the provider when something authoritative declared it. A reasoning
    # model spends an invented cap on its chain of thought and returns an empty
    # summary, which this loop then has to reject and retry.
    output_budget_tokens = max(1, limits.output_reserve_tokens)
    summary_output_cap = output_budget_tokens if limits.output_reserve_declared else None
    summary_request_ceiling = limits.effective_context_window_tokens

    while True:
        _raise_if_cancelled(token)
        request = _build_summary_request(
            transient,
            compact_input,
            communication_language=communication_language,
            max_output_tokens=summary_output_cap,
        )
        request_tokens = estimate_tokens(request.messages)
        if calibrated(request_tokens) + output_budget_tokens > summary_request_ceiling:
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
            if is_context_window_error(exc):
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
        response_attempts += 1
        summary_usage = ModelUsage(
            input_tokens=summary_usage.input_tokens + candidate.usage.input_tokens,
            output_tokens=summary_usage.output_tokens + candidate.usage.output_tokens,
            total_tokens=summary_usage.total_tokens + candidate.usage.total_tokens,
        )
        # Reasoning models put `<think>` in the same channel as the answer, and
        # some providers print tool calls as text instead of calling. Either one
        # committed verbatim becomes a summary that is mostly not a summary, and
        # it is re-injected on every later step of the turn.
        candidate_summary = visible_model_text(candidate.text)
        serialized_tool_text = contains_serialized_tool_protocol(candidate_summary)
        wrong_language = not text_matches_communication_language(
            candidate_summary,
            communication_language,
        )
        if candidate.tool_calls or serialized_tool_text or not candidate_summary or wrong_language:
            if response_retries >= rt.limits.model_retries:
                if candidate.tool_calls:
                    reason = "unexpected tool calls"
                elif serialized_tool_text:
                    reason = "tool-call markup instead of a summary"
                elif wrong_language:
                    reason = f"a summary in the wrong language (expected {communication_language})"
                else:
                    reason = "an empty summary"
                raise RuntimeError(
                    f"context compaction model repeatedly returned {reason}"
                )
            response_retries += 1
            # Historical native calls can prime compatible providers to keep
            # acting even though compaction is text-only. Retry from a smaller
            # complete history unit; canonical history remains untouched and is
            # still what the eventual checkpoint archives.
            if len(compact_input) > 1:
                previous = len(compact_input)
                compact_input = _trim_oldest_compaction_unit(compact_input)
                trimmed_messages += previous - len(compact_input)
            transport_retries = 0
            continue
        summary = candidate_summary
        break

    _raise_if_cancelled(token)

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
    # Judge the compacted result by whatever gate let the request in, so an
    # unbudgeted session cannot be failed for producing a history it would have
    # been allowed to send uncompacted. The message cap always applies.
    if (
        (input_budget is not None and calibrated(estimated_after) > input_budget)
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
        summary_usage=summary_usage,
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
        active_context_tokens=calibrated_active_context_tokens,
        token_accounting_source=accounting_source,
        reduction_stats=reduction_stats,
        calibration=calibration,
        calibration_samples=calibration_samples,
        input_budget=input_budget,
    )
    metadata.update(
        {
            "auto_compacted": True,
            "compaction_attempts": response_attempts,
            "compaction_trimmed_messages": trimmed_messages,
            "forced_by_provider_context_error": forced_compaction,
        }
    )
    return committed_visible, metadata


__all__ = [
    "ContextBudgetExceeded",
    "estimate_tokens",
    "estimate_tool_schema_tokens",
    "is_context_window_error",
    "prepare_context",
    "request_forced_compaction",
    "safe_split",
]
