"""Codex-compatible model-window compaction primitives.

Durable Loom checkpoints and the active model window are deliberately separate:
checkpoints may archive the complete canonical transcript, while the replacement
history sent to the model follows Codex's user-messages-plus-summary contract.
"""
from __future__ import annotations

from collections.abc import Callable, Sequence

from app.ai import AIMessage, ImagePart, MessageRole, TextPart


SUMMARIZATION_PROMPT = """You are performing a CONTEXT CHECKPOINT COMPACTION. Create a handoff summary for another LLM that will resume the task.

Include:
- Current progress and key decisions made
- Important context, constraints, or user preferences
- What remains to be done (clear next steps)
- Any critical data, examples, or references needed to continue

Be concise, structured, and focused on helping the next LLM seamlessly continue the work.
"""

SUMMARY_PREFIX = (
    "Another language model started to solve this problem and produced a summary of its thinking process. "
    "You also have access to the state of the tools that were used by that language model. Use this to build "
    "on the work that has already been done and avoid duplicating work. Here is the summary produced by the "
    "other language model, use the information in this summary to assist with your own analysis:"
)

COMPACT_USER_MESSAGE_MAX_TOKENS = 20_000
COMPACTION_MESSAGE_NAME = "loom_compaction"


def _content_text(message: AIMessage) -> str:
    if isinstance(message.content, str):
        return message.content
    output: list[str] = []
    for part in message.content:
        if isinstance(part, TextPart):
            output.append(part.text)
        elif isinstance(part, ImagePart):
            # Images are not copied into a partially truncated synthetic user
            # message. Whole selected user messages still preserve them.
            output.append("[image]")
    return "\n".join(output)


def is_real_user_message(message: AIMessage) -> bool:
    return (
        message.role is MessageRole.USER
        and str(getattr(message, "name", "") or "") != COMPACTION_MESSAGE_NAME
    )


def _truncate_user_message(
    message: AIMessage,
    remaining_tokens: int,
    token_counter: Callable[[Sequence[AIMessage]], int],
) -> AIMessage | None:
    if remaining_tokens <= 0:
        return None
    if token_counter((message,)) <= remaining_tokens:
        return message
    if not isinstance(message.content, str):
        # Codex truncates a selected oldest user item rather than retaining an
        # invalid fragment of a tool/action item. Loom cannot losslessly split a
        # multipart image message, so fail closed by omitting that oldest item.
        return None

    text = message.content
    if not text:
        return None
    # The token counter is authoritative. Binary-search the longest suffix that
    # fits so recent text survives, matching compaction's newest-first budget.
    low, high = 1, len(text)
    best = ""
    while low <= high:
        mid = (low + high) // 2
        candidate = AIMessage(
            role=MessageRole.USER,
            name=message.name,
            content=text[-mid:],
        )
        if token_counter((candidate,)) <= remaining_tokens:
            best = text[-mid:]
            low = mid + 1
        else:
            high = mid - 1
    if not best:
        return None
    return AIMessage(role=MessageRole.USER, name=message.name, content=best)


def build_compacted_history(
    history: Sequence[AIMessage],
    summary: str,
    *,
    token_counter: Callable[[Sequence[AIMessage]], int],
) -> tuple[AIMessage, ...]:
    """Return Codex's replacement model history shape.

    Keep only genuine user messages, newest-first under a shared 20k-token
    budget, then append one contextual-user compaction summary. Assistant/tool
    items are intentionally not replayed from the pre-compaction window.
    """
    text = str(summary or "").strip()
    if not text:
        raise ValueError("context summary must not be empty")

    selected_reversed: list[AIMessage] = []
    remaining = COMPACT_USER_MESSAGE_MAX_TOKENS
    for message in reversed(tuple(history)):
        if not is_real_user_message(message):
            continue
        cost = max(0, int(token_counter((message,))))
        if cost <= remaining:
            selected_reversed.append(message)
            remaining -= cost
            continue
        truncated = _truncate_user_message(message, remaining, token_counter)
        if truncated is not None:
            selected_reversed.append(truncated)
        break

    selected_reversed.reverse()
    selected_reversed.append(
        AIMessage(
            role=MessageRole.USER,
            name=COMPACTION_MESSAGE_NAME,
            content=f"{SUMMARY_PREFIX}\n{text}",
        )
    )
    return tuple(selected_reversed)


__all__ = [
    "COMPACT_USER_MESSAGE_MAX_TOKENS",
    "COMPACTION_MESSAGE_NAME",
    "SUMMARIZATION_PROMPT",
    "SUMMARY_PREFIX",
    "build_compacted_history",
    "is_real_user_message",
]
