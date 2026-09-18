from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol

from app.agent_runtime.stickers import (
    INLINE_STICKER_STRUCTURED_PLAN_BEGIN,
    INLINE_STICKER_STRUCTURED_PLAN_END,
    INLINE_STICKER_VISIBLE_MARKER_RE,
)


@dataclass(frozen=True, slots=True)
class RemoteMessage:
    message_id: str
    sender_id: str
    text: str
    created_at_ms: int = 0


class RemoteChannel(Protocol):
    def send_text(self, text: str) -> None: ...


class RemoteSessionState(Protocol):
    @property
    def thread_id(self) -> str: ...

    def set_thread_id(self, thread_id: str) -> None: ...


_GENERIC_AI_LEDGER_MARKER_RE = re.compile(r"\[\[AI_LEDGER_[^\]\r\n]{1,256}\]\]", re.I)


def sanitize_remote_text(text: str) -> str:
    value = str(text or "")
    if INLINE_STICKER_STRUCTURED_PLAN_BEGIN in value:
        while True:
            start = value.find(INLINE_STICKER_STRUCTURED_PLAN_BEGIN)
            if start < 0:
                break
            end = value.find(INLINE_STICKER_STRUCTURED_PLAN_END, start)
            if end < 0:
                value = value[:start]
                break
            value = value[:start] + value[end + len(INLINE_STICKER_STRUCTURED_PLAN_END) :]
    value = INLINE_STICKER_VISIBLE_MARKER_RE.sub("", value)
    value = _GENERIC_AI_LEDGER_MARKER_RE.sub("", value)
    return value.strip()


__all__ = [
    "RemoteChannel",
    "RemoteMessage",
    "RemoteSessionState",
    "sanitize_remote_text",
]
