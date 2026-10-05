"""Request-scoped cancellation, also propagated to provider stream readers."""
from __future__ import annotations

import threading
import time
from contextvars import ContextVar
from typing import Callable


class ModelCancelled(Exception):
    pass


class ModelSteered(Exception):
    """The current model sample was superseded by newer same-turn guidance."""


class ExecutionControl:
    def __init__(self) -> None:
        self.request_purpose = "generation"
        self.event = threading.Event()
        self._lock = threading.Lock()
        self._callbacks: list[Callable[[], object]] = []
        # Deliberately not under the lock. This is written once per provider
        # chunk - thousands of times per response - and read from one other
        # thread, and a float store is atomic under the GIL. Taking the cancel
        # lock on every token to gain nothing would be the only real cost here.
        self._progress_at = 0.0
        self.started_at = time.monotonic()
        self.last_chunk_at = 0.0
        self.first_chunk_at = 0.0
        self.first_content_at = 0.0
        self.first_tool_at = 0.0
        self.chunk_count = 0
        self.content_chunk_count = 0
        self.tool_argument_fragments = 0
        self.max_chunk_gap = 0.0
        self.max_content_gap = 0.0
        self.raw_chunk_reporting = False
        self.retry_observer = None

    def note_chunk(self) -> None:
        now = time.monotonic()
        self.max_chunk_gap = max(self.max_chunk_gap, now - (self.last_chunk_at or self.started_at))
        self.first_chunk_at = self.first_chunk_at or now
        self.last_chunk_at = now
        self.chunk_count += 1

    def stream_timing(self) -> dict[str, int | None]:
        now = time.monotonic()
        return {
            "first_chunk_ms": round((self.first_chunk_at - self.started_at) * 1000) if self.first_chunk_at else None,
            "first_content_ms": round((self.first_content_at - self.started_at) * 1000) if self.first_content_at else None,
            "first_tool_fragment_ms": round((self.first_tool_at - self.started_at) * 1000) if self.first_tool_at else None,
            "max_chunk_gap_ms": round(max(self.max_chunk_gap, now - (self.last_chunk_at or self.started_at)) * 1000),
            "max_content_gap_ms": round(max(self.max_content_gap, now - (self._progress_at or self.started_at)) * 1000),
            "chunk_count": self.chunk_count,
            "content_chunk_count": self.content_chunk_count,
            "tool_argument_fragments": self.tool_argument_fragments,
        }

    @property
    def cancelled(self) -> bool:
        return self.event.is_set()

    @property
    def progress_at(self) -> float:
        """Last substantive text/reasoning/tool fragment; 0 before generation."""

        return self._progress_at

    def note_progress(self, *, tool_fragment: bool = False) -> None:
        """Record that the provider is still producing.

        Called per substantive provider chunk rather than per surfaced event, and that
        distinction is the whole point: reasoning deltas are accumulated for the
        end of the turn and never become StreamEvents, so a thinking model can
        stream healthily for two minutes while everything downstream sees
        silence. Anything watching only public output cannot tell that apart
        from a dead connection.
        """

        now = time.monotonic()
        self.max_content_gap = max(self.max_content_gap, now - (self._progress_at or self.started_at))
        self.first_content_at = self.first_content_at or now
        self._progress_at = now
        self.content_chunk_count += 1
        if tool_fragment:
            self.first_tool_at = self.first_tool_at or now
            self.tool_argument_fragments += 1

    def check(self) -> None:
        if self.cancelled:
            raise ModelCancelled("model request cancelled")

    def on_cancel(self, callback: Callable[[], object]) -> None:
        with self._lock:
            if not self.cancelled:
                self._callbacks.append(callback)
                return
        self._close(callback)

    @staticmethod
    def _close(callback: Callable[[], object]) -> None:
        def close() -> None:
            try:
                callback()
            except Exception:
                pass
        threading.Thread(target=close, daemon=True, name="loom-model-close").start()

    def cancel(self) -> None:
        with self._lock:
            self.event.set()
            callbacks, self._callbacks = self._callbacks, []
        for callback in callbacks:
            self._close(callback)


current_control: ContextVar[ExecutionControl | None] = ContextVar("loom_model_control", default=None)


def check_cancelled() -> None:
    control = current_control.get()
    if control is not None:
        control.check()


def note_progress(*, tool_fragment: bool = False) -> None:
    control = current_control.get()
    if control is not None:
        control.note_progress(tool_fragment=tool_fragment)


def note_chunk() -> None:
    control = current_control.get()
    if control is not None:
        control.raw_chunk_reporting = True
        control.note_chunk()


def note_transport_retry(reason: str, attempt: int) -> None:
    control = current_control.get()
    if control is not None and control.retry_observer is not None:
        control.check()
        control.retry_observer({"reason": reason, "attempt": attempt, "will_retry": True})
