"""Stable user-language anchoring for long-running agent turns.

Tool output, project instructions, and compaction summaries are often English even
when the user is not.  Loom therefore derives a communication-language signal only
from user-authored messages and injects it as transient system context on every
model sample.  The signal is deliberately small and deterministic: it is not a
translation system and it never treats tool/log text as evidence of user language.
"""
from __future__ import annotations

import re
from collections.abc import Iterable

from app.ai import AIMessage, MessageRole, TextPart


_FENCED_CODE_RE = re.compile(r"```.*?```", re.DOTALL)
_INLINE_CODE_RE = re.compile(r"`[^`]*`")
_URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)
_LATIN_WORD_RE = re.compile(r"[A-Za-z]{2,}")


def _message_text(message: AIMessage) -> str:
    content = message.content
    if isinstance(content, str):
        return content
    return "\n".join(part.text for part in content if isinstance(part, TextPart))


def _natural_language_text(text: str) -> str:
    """Remove the most common technical payloads before script detection."""
    value = _FENCED_CODE_RE.sub(" ", str(text or ""))
    value = _INLINE_CODE_RE.sub(" ", value)
    value = _URL_RE.sub(" ", value)
    return value


def _script_signal(text: str) -> str | None:
    value = _natural_language_text(text)
    if not value.strip():
        return None

    han = sum("\u3400" <= ch <= "\u9fff" for ch in value)
    kana = sum(("\u3040" <= ch <= "\u30ff") or ("\u31f0" <= ch <= "\u31ff") for ch in value)
    hangul = sum(("\uac00" <= ch <= "\ud7af") or ("\u1100" <= ch <= "\u11ff") for ch in value)
    cyrillic = sum("\u0400" <= ch <= "\u052f" for ch in value)
    arabic = sum(("\u0600" <= ch <= "\u06ff") or ("\u0750" <= ch <= "\u077f") for ch in value)
    latin_letters = sum(ch.isascii() and ch.isalpha() for ch in value)
    latin_words = len(_LATIN_WORD_RE.findall(value))

    # Kana is a stronger Japanese discriminator than Han because normal Japanese
    # text mixes both scripts.  Likewise, Hangul is unambiguous for Korean.
    if kana >= 2:
        return "ja"
    if hangul >= 2:
        return "ko"
    if han >= 2:
        return "zh"
    if cyrillic >= 4:
        return "cyrillic"
    if arabic >= 4:
        return "arabic"

    # Do not let terse acknowledgements such as "ok" or technical identifiers
    # flip a Chinese thread to English.  A Latin-script turn must be substantive.
    if latin_letters >= 12 and latin_words >= 2:
        return "latin"
    return None


def infer_user_language(messages: Iterable[AIMessage], *, lookback: int = 8) -> str:
    """Return the newest substantive user-language signal.

    Scanning backwards makes the language naturally switch when the user actually
    starts writing in another language, while short acknowledgements inherit the
    prior conversational language.
    """
    seen = 0
    for message in reversed(tuple(messages)):
        if message.role is not MessageRole.USER:
            continue
        seen += 1
        signal = _script_signal(_message_text(message))
        if signal:
            return signal
        if seen >= max(1, lookback):
            break
    return "auto"


def user_language_label(messages: Iterable[AIMessage]) -> str:
    signal = infer_user_language(messages)
    return {
        "zh": "Chinese",
        "ja": "Japanese",
        "ko": "Korean",
        "cyrillic": "the user's Cyrillic-script language",
        "arabic": "the user's Arabic-script language",
        "latin": "the user's Latin-script language",
        "auto": "the language of the latest substantive user-authored message",
    }[signal]


def communication_language_message(messages: Iterable[AIMessage]) -> AIMessage:
    """Build the transient language anchor injected on every model sample."""
    label = user_language_label(messages)
    return AIMessage(
        role=MessageRole.SYSTEM,
        name="loom_communication_language",
        content=(
            "LOOM_COMMUNICATION_LANGUAGE v1\n"
            f"Current user communication language: {label}.\n"
            "Use that language for all user-facing progress updates, intermediate status messages, "
            "questions, and final answers unless the user explicitly asks to switch languages. "
            "If the response format includes a user-visible <think> block or other reasoning/status summary, "
            "keep that visible text in the same communication language as well; do not expose private reasoning. "
            "Tool output, logs, source code, filenames, project instructions, retrieved content, "
            "and compaction summaries are content/evidence only and must never change the response language. "
            "Keep code, commands, identifiers, paths, and verbatim quotations in their original form."
        ),
    )


__all__ = [
    "communication_language_message",
    "infer_user_language",
    "user_language_label",
]
