"""Model-response validation helpers for the canonical TurnRunner."""
from __future__ import annotations

import re

from app.ai import MessageRole, ModelResponse


COMPLETE_FINISH_REASONS = {"", "stop", "tool_calls", "function_call", "completed", "end_turn"}
RESUMABLE_TERMINAL_REASONS = frozenset({
    "unfinished_terminal_text",
    "unterminated_code_fence",
    "unterminated_inline_code",
    "unterminated_emphasis",
})
_COMPLETE_THINK_BLOCK_RE = re.compile(r"<think>.*?</think>", re.IGNORECASE | re.DOTALL)
_COMPLETE_FENCED_CODE_RE = re.compile(r"```[\s\S]*?```")
_INLINE_BACKTICK_RUN_RE = re.compile(r"(?<!\\)(`{1,2})(?!`)")
_STRONG_MARK_RE = re.compile(r"(?<!\\)\*\*")
_DANGLING_TERMINAL_RE = re.compile(r"(?:\[|\{|<tool_call>)\s*$", re.IGNORECASE)
_DANGLING_DISCOURSE_RE = re.compile(r"[:：]\s*$")
_SERIALIZED_TOOL_PROTOCOL_RE = re.compile(
    r"(?:<tool_call\b|</tool_call>|<invoke\s+name\s*=|\]\s*<\]\s*minimax\s*\[>\s*\[<)",
    re.IGNORECASE,
)
_INLINE_STICKER_RE = re.compile(r"\[\[AI_LEDGER_INLINE_STICKER:[a-z0-9_]{2,48}\]\]", re.I)
TERMINAL_RECOVERY_INSTRUCTION = (
    "Your previous response was rejected because it was empty, malformed (including invalid native tool-call "
    "arguments), ended with an incomplete serialized structure, or contained reasoning without a user-visible "
    "answer. Continue the same task now. "
    "If an available tool is needed, emit a native structured tool call through the tool-calling protocol; "
    "do not print JSON, '[' or a tool-call prefix in assistant text. Otherwise return a complete final answer."
)
TRUNCATED_RECOVERY_INSTRUCTION = (
    "The previous assistant response was cut off by the provider's output limit and was not committed. "
    "Continue the same task from that partial response without repeating its analysis. If it was leading to "
    "a tool action, emit the native structured tool call immediately; otherwise finish with a concise answer."
)
UNFINISHED_RECOVERY_INSTRUCTION = (
    "The previous assistant response ended while introducing the next action and was not committed as a final "
    "answer. Continue the same task from that partial response without repeating it. If the promised action "
    "requires an available tool, emit the native structured tool call now; otherwise complete the answer."
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


def _without_complete_code(text: str) -> str:
    value = _COMPLETE_FENCED_CODE_RE.sub("", str(text or ""))
    # Remove balanced inline-code spans before checking emphasis. Do the longer
    # delimiter first so a double-backtick span is not mistaken for two singles.
    for delimiter in ("``", "`"):
        escaped = re.escape(delimiter)
        value = re.sub(
            rf"(?<!\\){escaped}[\s\S]*?(?<!\\){escaped}",
            "",
            value,
        )
    return value


def _has_unterminated_inline_code(text: str) -> bool:
    value = _COMPLETE_FENCED_CODE_RE.sub("", str(text or ""))
    counts = {1: 0, 2: 0}
    for match in _INLINE_BACKTICK_RUN_RE.finditer(value):
        counts[len(match.group(1))] += 1
    return any(count % 2 for count in counts.values())


def _has_unterminated_emphasis(text: str) -> bool:
    value = _without_complete_code(text)
    return len(_STRONG_MARK_RE.findall(value)) % 2 == 1


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
    if _DANGLING_TERMINAL_RE.search(visible):
        return "dangling_serialized_structure"
    if visible.count("```") % 2:
        return "unterminated_code_fence"
    if _has_unterminated_inline_code(visible):
        return "unterminated_inline_code"
    if _has_unterminated_emphasis(visible):
        return "unterminated_emphasis"
    if _DANGLING_DISCOURSE_RE.search(visible):
        return "unfinished_terminal_text"
    return ""


def strip_compaction_echo(messages, text: str) -> tuple[str, bool]:
    """Remove a model's verbatim replay of private checkpoint context."""

    source = str(text or "")
    summaries = [
        str(message.content or "")
        for message in messages
        if message.role is MessageRole.SYSTEM
        and str(getattr(message, "name", "") or "") == "loom_compaction"
        and isinstance(message.content, str)
    ]
    if not source or not summaries:
        return source, False

    def normalized(line: str) -> str:
        return re.sub(r"\s+", " ", _INLINE_STICKER_RE.sub("", line)).strip()

    summary_lines = {
        value
        for summary in summaries
        for line in summary.splitlines()
        if len(value := normalized(line)) >= 12
    }
    response_lines = source.splitlines(keepends=True)
    matched = [
        index for index, line in enumerate(response_lines)
        if normalized(line) in summary_lines
    ]
    if len(matched) < 3 or sum(len(normalized(response_lines[i])) for i in matched) < 80:
        return source, False

    start = matched[0]
    while start > 0:
        previous = normalized(response_lines[start - 1])
        if not previous or previous.startswith("#") or "压缩摘要" in previous or previous.startswith("[请求已被压缩"):
            start -= 1
            continue
        break
    cleaned = "".join(response_lines[:start]).strip()
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned, True


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
    "RESUMABLE_TERMINAL_REASONS",
    "TERMINAL_RECOVERY_INSTRUCTION",
    "TRUNCATED_RECOVERY_INSTRUCTION",
    "UNFINISHED_RECOVERY_INSTRUCTION",
    "contains_serialized_tool_protocol",
    "history_message_count",
    "merge_recovery_text",
    "invalid_terminal_response",
    "visible_model_text",
    "strip_compaction_echo",
]