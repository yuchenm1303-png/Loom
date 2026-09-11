from __future__ import annotations

import json
import math
from dataclasses import dataclass, replace
from typing import Callable, Sequence

from app.ai import AIMessage, MessageRole


_TOOL_OMITTED = (
    "Tool output was reduced in the active model context because the request was near its context "
    "limit. The durable Loom transcript still contains the recorded result; re-read or rerun the "
    "source when exact details are needed."
)
_USER_OMITTED = "[Earlier user message omitted from the active model context after compaction.]"


def approx_text_tokens(value: str) -> int:
    return max(1, math.ceil(len(str(value or "").encode("utf-8")) / 3))


def truncate_text_for_tokens(value: str, max_tokens: int, *, marker: str) -> str:
    text = str(value or "")
    budget = max(1, int(max_tokens))
    if approx_text_tokens(text) <= budget:
        return text

    marker_text = f"\n{marker}\n"
    byte_budget = max(24, budget * 3 - len(marker_text.encode("utf-8")))
    raw = text.encode("utf-8")
    head_budget = byte_budget * 3 // 5
    tail_budget = byte_budget - head_budget
    head = raw[:head_budget].decode("utf-8", errors="ignore")
    tail = raw[-tail_budget:].decode("utf-8", errors="ignore") if tail_budget else ""
    return head + marker_text + tail


@dataclass(frozen=True, slots=True)
class ContextReductionStats:
    tool_outputs_reduced: int = 0
    tool_outputs_collapsed: int = 0
    user_messages_truncated: int = 0
    estimated_tokens_saved: int = 0

    def merged(self, other: "ContextReductionStats") -> "ContextReductionStats":
        return ContextReductionStats(
            tool_outputs_reduced=self.tool_outputs_reduced + other.tool_outputs_reduced,
            tool_outputs_collapsed=self.tool_outputs_collapsed + other.tool_outputs_collapsed,
            user_messages_truncated=self.user_messages_truncated + other.user_messages_truncated,
            estimated_tokens_saved=self.estimated_tokens_saved + other.estimated_tokens_saved,
        )

    def as_dict(self) -> dict[str, int]:
        return {
            "tool_outputs_reduced": self.tool_outputs_reduced,
            "tool_outputs_collapsed": self.tool_outputs_collapsed,
            "user_messages_truncated": self.user_messages_truncated,
            "estimated_tokens_saved": self.estimated_tokens_saved,
        }


def _tool_ok(content: str) -> bool | None:
    try:
        payload = json.loads(content)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or "ok" not in payload:
        return None
    return bool(payload.get("ok"))


def _reduced_tool_content(message: AIMessage, max_tokens: int) -> str:
    raw = str(message.content or "")
    preview_budget = max(96, int(max_tokens) - 120)
    preview = truncate_text_for_tokens(
        raw,
        preview_budget,
        marker="[middle of tool output omitted for context budget]",
    )
    payload = {
        "ok": _tool_ok(raw),
        "content": preview,
        "data": {
            "context_reduced": True,
            "reason": "context_budget",
            "tool": message.name,
            "exact_result_remains_in_durable_transcript": True,
        },
    }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _collapsed_tool_content(message: AIMessage) -> str:
    payload = {
        "ok": _tool_ok(str(message.content or "")),
        "content": _TOOL_OMITTED,
        "data": {
            "context_reduced": True,
            "collapsed": True,
            "reason": "context_budget",
            "tool": message.name,
            "exact_result_remains_in_durable_transcript": True,
        },
    }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def reduce_tool_outputs(
    messages: Sequence[AIMessage],
    *,
    per_output_token_limit: int,
    target_total_tokens: int | None = None,
    estimate_total: Callable[[Sequence[AIMessage]], int] | None = None,
) -> tuple[tuple[AIMessage, ...], ContextReductionStats]:
    """Shrink tool observations in a request copy without mutating durable history.

    First, every oversized tool result gets a bounded preview. If the complete
    request is still above ``target_total_tokens``, the oldest remaining previews
    are collapsed to small structural stubs until it fits or no tool output can be
    reduced further. Assistant tool calls and call IDs are never modified.
    """

    limit = max(128, int(per_output_token_limit))
    working = list(messages)
    reduced = 0
    collapsed = 0
    saved = 0

    for index, message in enumerate(tuple(working)):
        if message.role is not MessageRole.TOOL or not isinstance(message.content, str):
            continue
        before = approx_text_tokens(message.content)
        if before <= limit:
            continue
        content = _reduced_tool_content(message, limit)
        after = approx_text_tokens(content)
        working[index] = replace(message, content=content)
        reduced += 1
        saved += max(0, before - after)

    if target_total_tokens is not None and estimate_total is not None:
        target = max(1, int(target_total_tokens))
        # Prefer collapsing older observations so the newest tool result keeps a
        # useful preview. This differs from cache-oriented prefix trimming but is
        # better aligned with an interactive desktop agent's semantic needs.
        for index, message in enumerate(tuple(working)):
            if estimate_total(tuple(working)) <= target:
                break
            if message.role is not MessageRole.TOOL or not isinstance(message.content, str):
                continue
            collapsed_content = _collapsed_tool_content(message)
            if message.content == collapsed_content:
                continue
            before = approx_text_tokens(message.content)
            after = approx_text_tokens(collapsed_content)
            if after >= before:
                continue
            working[index] = replace(message, content=collapsed_content)
            collapsed += 1
            saved += before - after

    return tuple(working), ContextReductionStats(
        tool_outputs_reduced=reduced,
        tool_outputs_collapsed=collapsed,
        estimated_tokens_saved=saved,
    )


def truncate_user_messages(
    messages: Sequence[AIMessage],
    *,
    max_total_tokens: int,
) -> tuple[tuple[AIMessage, ...], ContextReductionStats]:
    """Bound request-visible user text newest-first, preserving canonical history."""

    remaining = max(1, int(max_total_tokens))
    working = list(messages)
    changed = 0
    saved = 0

    for index in range(len(working) - 1, -1, -1):
        message = working[index]
        if message.role is not MessageRole.USER or not isinstance(message.content, str):
            continue
        before = approx_text_tokens(message.content)
        if before <= remaining:
            remaining -= before
            continue

        if remaining > 0:
            content = truncate_text_for_tokens(
                message.content,
                remaining,
                marker="[middle of user message omitted for context budget]",
            )
            remaining = 0
        else:
            content = _USER_OMITTED
        after = approx_text_tokens(content)
        if content != message.content:
            working[index] = replace(message, content=content)
            changed += 1
            saved += max(0, before - after)

    return tuple(working), ContextReductionStats(
        user_messages_truncated=changed,
        estimated_tokens_saved=saved,
    )


__all__ = [
    "ContextReductionStats",
    "approx_text_tokens",
    "reduce_tool_outputs",
    "truncate_text_for_tokens",
    "truncate_user_messages",
]
