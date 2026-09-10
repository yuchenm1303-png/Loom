"""Normalisation for what a user actually submits as a turn.

A turn used to be a string. It is now "a string, or a string plus the images
the user attached to it", and every runtime layer needs the same answer to two
questions: what content goes into the model message, and what plain text goes
into the journal, the thread title, and the UI. Deriving both in one place is
what keeps those two answers from drifting apart.
"""

from __future__ import annotations

from collections.abc import Sequence

from app.ai import ImagePart, TextPart
from app.ai.contracts import ContentPart, MessageContent


TurnInput = str | Sequence[ContentPart]


def turn_input_text(content: MessageContent) -> str:
    """The reader-facing rendering of turn content.

    Images are deliberately summarised rather than described: this text is used
    for titles and log lines, where a data URL would be noise.
    """
    if isinstance(content, str):
        return content
    texts = [part.text for part in content if isinstance(part, TextPart)]
    if texts:
        return "\n".join(texts)
    images = sum(1 for part in content if isinstance(part, ImagePart))
    return f"[{images} image{'s' if images != 1 else ''}]"


def normalize_turn_input(value: TurnInput) -> tuple[MessageContent, str]:
    """Return ``(message content, plain text)`` for one submitted turn.

    Single-text input collapses back to a plain string so existing sessions,
    stored transcripts and providers see exactly the payload they saw before
    attachments existed.
    """
    if isinstance(value, str):
        text = value.strip()
        if not text:
            raise ValueError("agent turn input must not be empty")
        return text, text
    if isinstance(value, (bytes, bytearray)) or not isinstance(value, Sequence):
        raise TypeError("agent turn input must be text or a sequence of content parts")

    parts: list[ContentPart] = []
    for part in value:
        if isinstance(part, TextPart):
            text = part.text.strip()
            if text:
                parts.append(TextPart(text))
        elif isinstance(part, ImagePart):
            parts.append(part)
        else:
            raise TypeError("unsupported agent turn input part")

    if not parts:
        raise ValueError("agent turn input must not be empty")
    if len(parts) == 1 and isinstance(parts[0], TextPart):
        return parts[0].text, parts[0].text
    content = tuple(parts)
    return content, turn_input_text(content)


__all__ = ["TurnInput", "normalize_turn_input", "turn_input_text"]
