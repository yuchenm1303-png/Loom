from __future__ import annotations

import copy
import threading
from dataclasses import dataclass
from typing import Any, Callable


@dataclass(slots=True)
class _Entry:
    operation: str
    ready: threading.Event
    result: Any = None
    error: BaseException | None = None


class IdempotencyStore:
    """Small in-process replay guard for remote write operations.

    Durable idempotency can replace this later without changing the public
    RemoteControl contract. Concurrent duplicate calls share one execution.
    """

    def __init__(self) -> None:
        self._guard = threading.Lock()
        self._entries: dict[str, _Entry] = {}

    def run(self, key: str, operation: str, action: Callable[[], Any]) -> tuple[Any, bool]:
        resolved_key = str(key or "").strip()
        resolved_operation = str(operation or "").strip()
        if not resolved_key:
            return action(), False
        if not resolved_operation:
            raise ValueError("idempotent operation must not be empty")

        owner = False
        with self._guard:
            entry = self._entries.get(resolved_key)
            if entry is None:
                entry = _Entry(operation=resolved_operation, ready=threading.Event())
                self._entries[resolved_key] = entry
                owner = True
            elif entry.operation != resolved_operation:
                raise ValueError("idempotency key was already used for a different operation")

        if not owner:
            entry.ready.wait()
            if entry.error is not None:
                raise entry.error
            return copy.deepcopy(entry.result), True

        try:
            result = action()
        except BaseException as exc:
            entry.error = exc
            entry.ready.set()
            with self._guard:
                current = self._entries.get(resolved_key)
                if current is entry:
                    self._entries.pop(resolved_key, None)
            raise

        entry.result = copy.deepcopy(result)
        entry.ready.set()
        return copy.deepcopy(result), False


__all__ = ["IdempotencyStore"]
