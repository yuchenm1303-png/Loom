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
        self.event = threading.Event()
        self._lock = threading.Lock()
        self._callbacks: list[Callable[[], object]] = []
        # Deliberately not under the lock. This is written once per provider
        # chunk - thousands of times per response - and read from one other
        # thread, and a float store is atomic under the GIL. Taking the cancel
        # lock on every token to gain nothing would be the only real cost here.
        self._progress_at = 0.0

    @property
    def cancelled(self) -> bool:
        return self.event.is_set()

    @property
    def progress_at(self) -> float:
        """When the provider last produced anything; 0.0 if it never has."""

        return self._progress_at

    def note_progress(self) -> None:
        """Record that the provider is still producing.

        Called per raw provider chunk rather than per surfaced event, and that
        distinction is the whole point: reasoning deltas are accumulated for the
        end of the turn and never become StreamEvents, so a thinking model can
        stream healthily for two minutes while everything downstream sees
        silence. Anything watching only public output cannot tell that apart
        from a dead connection.
        """

        self._progress_at = time.monotonic()

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


def note_progress() -> None:
    control = current_control.get()
    if control is not None:
        control.note_progress()
