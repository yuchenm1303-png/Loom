"""Read-only projection of legacy sticker controls out of model history."""
from .stickers import (INLINE_STICKER_VISIBLE_MARKER_RE, INLINE_STICKER_STRUCTURED_PLAN_BEGIN,
                       INLINE_STICKER_STRUCTURED_PLAN_END)


def strip_legacy_sticker_protocol(text: str) -> str:
    """Project protocol control tokens out of model history without mutating it."""
    source = INLINE_STICKER_VISIBLE_MARKER_RE.sub("", str(text or ""))
    while INLINE_STICKER_STRUCTURED_PLAN_BEGIN in source:
        start = source.index(INLINE_STICKER_STRUCTURED_PLAN_BEGIN)
        end = source.find(INLINE_STICKER_STRUCTURED_PLAN_END, start)
        if end < 0:
            return source[:start]
        source = source[:start] + source[end + len(INLINE_STICKER_STRUCTURED_PLAN_END):]
    return source
