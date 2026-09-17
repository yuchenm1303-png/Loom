"""Request-scoped cancellation, also propagated to provider stream readers."""
from __future__ import annotations

import threading
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

    @property
    def cancelled(self) -> bool:
        return self.event.is_set()

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
