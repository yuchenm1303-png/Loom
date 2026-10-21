"""A result per message object, remembered while the message is in use.

A request is built from the same few hundred history messages at every step, and several things are worked out
from each message each time (what it costs in tokens, which position it holds in the history). A message is frozen,
so what is worked out from it is a fact about it, and it is worth working out once.
"""
from __future__ import annotations

import threading
from collections import OrderedDict

from app.ai import AIMessage


def message_weight(message: AIMessage) -> int:
    """Roughly the characters a message keeps alive, which is what the memo is bounded by."""
    content = message.content
    if isinstance(content, str):
        size = len(content)
    else:
        size = sum(len(getattr(part, "text", "") or "") + len(getattr(part, "image_url", "") or "") for part in content)
    return size + len(message.reasoning) + 256


class MessageMemo:
    """Values keyed by the identity of the message they were worked out from.

    The entry holds the message, so no other object can take its id while the entry exists, and a hit is the same
    message and not merely the same address. The memo is bounded by the number of entries and by the text those
    messages keep alive (a message that left its session is held until it is pushed out, least recently used
    first). It never holds ``None``: that is what a miss returns.
    """

    def __init__(self, entries: int = 16384, budget: int = 24_000_000) -> None:
        self._limit, self._budget = entries, budget
        self._entries: OrderedDict[int, tuple[AIMessage, object, int]] = OrderedDict()
        self._weight = 0
        self._lock = threading.Lock()

    def get(self, message: AIMessage) -> object | None:
        key = id(message)
        with self._lock:
            entry = self._entries.get(key)
            if entry is None or entry[0] is not message:
                return None
            self._entries.move_to_end(key)
            return entry[1]

    def put(self, message: AIMessage, value: object) -> None:
        weight = message_weight(message)
        key = id(message)
        with self._lock:
            previous = self._entries.pop(key, None)
            if previous is not None:
                self._weight -= previous[2]
            self._entries[key] = (message, value, weight)
            self._weight += weight
            while self._entries and (len(self._entries) > self._limit or self._weight > self._budget):
                _, (_, _, dropped) = self._entries.popitem(last=False)
                self._weight -= dropped

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
            self._weight = 0
