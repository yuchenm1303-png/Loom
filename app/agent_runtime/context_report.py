"""What the active model context is made of, as a reportable record.

Loom already measures every number here on its way to building a request: the
resolved window, the input budget, what the provider actually counted, how much
of it went to tool definitions, and what had to be given up to make the request
fit. Until now those numbers only existed inside ``model_requested`` events,
which is why a user watching the agent say "my tool output was folded" had no
way to see that it was true.

These functions are deliberately pure and take an event payload rather than a
runtime: the honest snapshot is the one from the last real request, not a
re-derivation that could disagree with what was actually sent.
"""
from __future__ import annotations

from typing import Any, Mapping


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _percent(part: int, whole: int) -> float:
    if whole <= 0:
        return 0.0
    return round(min(100.0, max(0.0, part / whole * 100.0)), 1)


def limits_record(limits: Mapping[str, Any]) -> dict[str, Any]:
    """Restate resolved context limits in the app server's wire shape."""

    window = limits.get("context_window_tokens")
    return {
        "windowTokens": _int(window) if window else None,
        "effectiveWindowTokens": _int(limits.get("effective_context_window_tokens")),
        "inputBudgetTokens": _int(limits.get("input_budget_tokens")),
        "outputReserveTokens": _int(limits.get("output_reserve_tokens")),
        "autoCompactTokens": _int(limits.get("auto_compact_token_limit")),
        "toolOutputTokenLimit": _int(limits.get("tool_output_token_limit")),
        "windowKnown": bool(limits.get("window_known")),
        "limitsSource": str(limits.get("source") or ""),
    }


def empty_context_report(limits: Mapping[str, Any]) -> dict[str, Any]:
    """A thread that has not sampled the model yet: budget known, nothing spent."""

    record = limits_record(limits)
    budget = record["inputBudgetTokens"]
    record.update(
        {
            "usedTokens": 0,
            "usedPercent": 0.0,
            "freeTokens": max(0, budget),
            "accounting": "none",
            "messageCount": 0,
            "segments": [
                {"key": "conversation", "tokens": 0},
                {"key": "toolSchemas", "tokens": 0},
                {"key": "free", "tokens": max(0, budget)},
            ],
            "pressure": {
                "schemaMode": "",
                "toolsOmitted": [],
                "toolOutputsReduced": 0,
                "toolOutputsCollapsed": 0,
                "userMessagesTruncated": 0,
                "blinded": False,
            },
            "compactions": 0,
            "lastCompactedAt": "",
            "measuredAt": "",
            "measurementPending": False,
        }
    )
    return record


def post_compaction_context_report(
    limits: Mapping[str, Any],
    *,
    estimated_tokens: int,
    message_count: int,
    compactions: int,
    last_compacted_at: str,
    measured_at: str,
) -> dict[str, Any]:
    """Report a checkpoint replacement before the next real model request.

    A checkpoint rewrites the active history, so a MODEL_REQUESTED measurement
    from before it is stale by definition. Until another request is sent, expose
    a clearly-labelled estimate of the new canonical replacement instead of
    combining a fresh compaction count with old usage.
    """

    record = limits_record(limits)
    budget = record["inputBudgetTokens"]
    used = max(0, _int(estimated_tokens))
    free = max(0, budget - used)
    record.update(
        {
            "usedTokens": used,
            "usedPercent": _percent(used, budget),
            "freeTokens": free,
            "accounting": "post_compaction_estimate",
            "messageCount": max(0, _int(message_count)),
            "segments": [
                {"key": "conversation", "tokens": used},
                {"key": "toolSchemas", "tokens": 0},
                {"key": "free", "tokens": free},
            ],
            "pressure": {
                "schemaMode": "",
                "toolsOmitted": [],
                "toolOutputsReduced": 0,
                "toolOutputsCollapsed": 0,
                "userMessagesTruncated": 0,
                "blinded": False,
            },
            "compactions": max(0, int(compactions)),
            "lastCompactedAt": str(last_compacted_at or ""),
            "measuredAt": str(measured_at or ""),
            "measurementPending": True,
        }
    )
    return record


def context_report_from_request(
    data: Mapping[str, Any],
    *,
    compactions: int = 0,
    last_compacted_at: str = "",
    measured_at: str = "",
) -> dict[str, Any]:
    """Build the report from one ``model_requested`` payload.

    ``calibrated_input_tokens_after`` is the size of the request Loom actually
    sent, restated in the provider's own accounting -- the same number every
    budget decision in ``prepare_context`` is made against. Using it here means
    the meter a user reads and the budget the runtime enforces can never drift
    apart.
    """

    limits = data.get("context_limits")
    limits = limits if isinstance(limits, Mapping) else {}
    record = limits_record(limits)

    used = _int(data.get("calibrated_input_tokens_after"))
    if used <= 0:
        used = _int(data.get("active_context_tokens"))
    schema_tokens = _int(data.get("tool_schema_tokens"))
    budget = record["inputBudgetTokens"]

    plan = data.get("tool_schema_plan")
    plan = plan if isinstance(plan, Mapping) else {}
    omitted = plan.get("omitted_names")
    omitted_names = [str(name) for name in omitted] if isinstance(omitted, (list, tuple)) else []

    collapsed = _int(data.get("tool_outputs_collapsed"))
    reduced = _int(data.get("tool_outputs_reduced"))

    # Definitions are counted separately because they are the one part of a
    # request the agent never chose: everything else is its own conversation.
    conversation = max(0, used - schema_tokens)
    free = max(0, budget - used)

    record.update(
        {
            "usedTokens": used,
            "usedPercent": _percent(used, budget),
            "freeTokens": free,
            "accounting": str(data.get("token_accounting_source") or "estimate"),
            "messageCount": _int(data.get("message_count")),
            "segments": [
                {"key": "conversation", "tokens": conversation},
                {"key": "toolSchemas", "tokens": schema_tokens},
                {"key": "free", "tokens": free},
            ],
            "pressure": {
                "schemaMode": str(plan.get("mode") or ""),
                "toolsOmitted": omitted_names,
                "toolOutputsReduced": reduced,
                "toolOutputsCollapsed": collapsed,
                "userMessagesTruncated": _int(data.get("user_messages_truncated")),
                # The signal worth surfacing loudest. A collapsed observation is
                # not a smaller observation: the agent can no longer read the
                # result of the command it just ran, and will say so.
                "blinded": collapsed > 0,
            },
            "compactions": max(0, int(compactions)),
            "lastCompactedAt": str(last_compacted_at or ""),
            "measuredAt": str(measured_at or ""),
            "measurementPending": False,
        }
    )
    return record


__all__ = [
    "context_report_from_request",
    "empty_context_report",
    "limits_record",
    "post_compaction_context_report",
]
