from __future__ import annotations

import threading
import time
from collections.abc import Callable

from .contracts import AgentEvent, AgentEventKind


class MemoryPipeline:
    """Small debounce scheduler for background memory extraction.

    Runtime event listeners must stay cheap because AgentRuntime calls them
    synchronously while committing durable turn state. The listener therefore
    only updates an in-memory deadline. Model work runs on this daemon worker.
    """

    def __init__(
        self,
        processor: Callable[[str], None],
        *,
        idle_seconds: float = 45.0,
        name: str = "loom-memory",
    ) -> None:
        self.processor = processor
        self.idle_seconds = max(0.0, float(idle_seconds))
        self._condition = threading.Condition()
        self._deadlines: dict[str, float] = {}
        self._stopped = False
        self._thread = threading.Thread(
            target=self._run,
            name=name,
            daemon=True,
        )
        self._thread.start()

    def on_event(self, event: AgentEvent) -> None:
        if event.kind is AgentEventKind.TURN_COMPLETED:
            self.schedule(event.session_id)

    def schedule(self, session_id: str, *, delay: float | None = None) -> None:
        key = str(session_id or "").strip()
        if not key:
            return
        seconds = self.idle_seconds if delay is None else max(0.0, float(delay))
        deadline = time.monotonic() + seconds
        with self._condition:
            if self._stopped:
                return
            # A later completed turn resets the debounce window. Explicit retry
            # and startup scheduling pass their own delay and should replace it.
            self._deadlines[key] = deadline
            self._condition.notify_all()

    def pending_count(self) -> int:
        with self._condition:
            return len(self._deadlines)

    def stop(self, *, timeout: float = 2.0) -> None:
        with self._condition:
            self._stopped = True
            self._deadlines.clear()
            self._condition.notify_all()
        if threading.current_thread() is not self._thread:
            self._thread.join(timeout=max(0.0, float(timeout)))

    def _run(self) -> None:
        while True:
            session_id = ""
            with self._condition:
                while not self._stopped:
                    if not self._deadlines:
                        self._condition.wait()
                        continue
                    session_id, deadline = min(
                        self._deadlines.items(),
                        key=lambda item: item[1],
                    )
                    remaining = deadline - time.monotonic()
                    if remaining > 0:
                        self._condition.wait(timeout=remaining)
                        continue
                    self._deadlines.pop(session_id, None)
                    break
                if self._stopped:
                    return

            try:
                self.processor(session_id)
            except Exception:
                # The runtime processor owns persistent retry/backoff state and
                # rescheduling. A worker exception must never kill the scheduler.
                continue


__all__ = ["MemoryPipeline"]
