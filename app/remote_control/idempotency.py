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

        action_completed = False
        try:
            result = action()
            action_completed = True
            entry.result = copy.deepcopy(result)
        except BaseException as exc:
            entry.error = exc
            entry.ready.set()
            if not action_completed:
                with self._guard:
                    current = self._entries.get(resolved_key)
                    if current is entry:
                        self._entries.pop(resolved_key, None)
            # If the action finished but its result could not be copied, retain the failure:
            # another request must not repeat a write merely to reconstruct its response.
            raise

        entry.ready.set()
        return copy.deepcopy(entry.result), False


__all__ = ["IdempotencyStore"]
