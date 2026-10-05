"""Model-response validation helpers for the canonical TurnRunner."""
from __future__ import annotations

import re

from app.ai import MessageRole, ModelResponse


COMPLETE_FINISH_REASONS = {"", "stop", "tool_calls", "function_call", "tool_use", "completed", "end_turn"}
_COMPLETE_THINK_BLOCK_RE = re.compile(r"<think>.*?</think>", re.IGNORECASE | re.DOTALL)
_SERIALIZED_TOOL_PROTOCOL_RE = re.compile(
    r"(?:<tool_call\b|</tool_call>|<invoke\s+name\s*=|\]\s*<\]\s*minimax\s*\[>\s*\[<)",
    re.IGNORECASE,
)
TERMINAL_RECOVERY_INSTRUCTION = (
    "Your previous response was rejected because it was empty, malformed, or contained reasoning without a user-visible "
    "answer. Continue the same task now. "
    "If an available tool is needed, emit a native structured tool call through the tool-calling protocol; "
    "do not print JSON, '[' or a tool-call prefix in assistant text. Otherwise return a complete final answer."
)
TRUNCATED_RECOVERY_INSTRUCTION = (
    "The previous assistant response was cut off by the provider's output limit and was not committed. "
    "Continue the same task from that partial response without repeating its analysis. If it was leading to "
    "a tool action, emit the native structured tool call immediately; otherwise finish with a concise answer."
)



def visible_model_text(text: str) -> str:
    """Return model text with complete reasoning blocks removed.

    Providers that expose ``<think>`` inline put reasoning in the same channel as
    the answer. Every consumer that treats model text as content rather than as a
    transcript must drop those blocks first.
    """

    return _COMPLETE_THINK_BLOCK_RE.sub("", str(text or "")).strip()


def contains_serialized_tool_protocol(text: str) -> bool:
    """Detect tool-call markup a provider printed as text instead of calling."""

    return bool(_SERIALIZED_TOOL_PROTOCOL_RE.search(str(text or "")))


def merge_recovery_text(partial: str, continuation: str) -> str:
    """Reconstruct one self-contained answer from a rejected partial + retry.

    Recovery prompts tell the provider that the partial text already exists in
    context, so most models continue from the exact cut point. A few repeat some
    or all of the prefix. Preserve the complete replacement when it already
    contains the partial, otherwise remove the longest exact overlap before
    concatenating. This keeps the durable assistant message whole instead of
    committing only the retry suffix.
    """

    prefix = str(partial or "")
    suffix = str(continuation or "")
    if not prefix:
        return suffix
    if not suffix:
        return prefix
    if suffix.startswith(prefix):
        return suffix
    if prefix.endswith(suffix):
        return prefix

    max_overlap = min(len(prefix), len(suffix))
    for width in range(max_overlap, 0, -1):
        if prefix[-width:] == suffix[:width]:
            return prefix + suffix[width:]
    return prefix + suffix


def invalid_terminal_response(response: ModelResponse) -> str:
    """Reject provider terminal responses that cannot be valid public output."""

    reason = str(response.finish_reason or "").strip().casefold()
    if reason not in COMPLETE_FINISH_REASONS:
        return f"incomplete_finish:{reason or 'unknown'}"
    if response.tool_calls:
        return ""
    raw = str(response.text or "")
    visible = visible_model_text(raw)
    if raw.strip() and not visible:
        return "reasoning_without_visible_answer"
    if contains_serialized_tool_protocol(visible):
        return "serialized_tool_call_text"
    # Public Markdown shape is not transport completeness. A legitimate answer
    # may explain an opening bracket or contain an unclosed code fence.
    return ""


def history_message_count(messages) -> int:
    """Count conversation messages while ignoring Loom-injected system guidance."""

    total = 0
    for message in messages:
        if message.role is MessageRole.SYSTEM and str(getattr(message, "name", "") or "").startswith("loom_"):
            continue
        total += 1
    return total


__all__ = [
    "COMPLETE_FINISH_REASONS",
    "TERMINAL_RECOVERY_INSTRUCTION",
    "TRUNCATED_RECOVERY_INSTRUCTION",
    "contains_serialized_tool_protocol",
    "history_message_count",
    "merge_recovery_text",
    "invalid_terminal_response",
    "visible_model_text",
]
