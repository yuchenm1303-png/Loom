"""Bounded model execution service; abandoned requests cannot commit history."""
from __future__ import annotations

import contextvars
import queue
import threading
import time

from app.ai.execution_control import ExecutionControl, ModelCancelled, current_control


class ModelExecutor:
    def __init__(self, max_inflight: int = 16, timeout: float = 150.0) -> None:
        self._slots = threading.BoundedSemaphore(max_inflight)
        self.timeout = timeout

    def execute(self, platform, profile_id, request, token):
        deadline = time.monotonic() + self.timeout
        while not self._slots.acquire(timeout=0.05):
            if token.cancelled:
                raise ModelCancelled()
            if time.monotonic() >= deadline:
                raise TimeoutError("model request concurrency limit reached")
        results: queue.Queue = queue.Queue(maxsize=1)
        control = ExecutionControl()
        context = contextvars.copy_context()

        def run():
            binding = current_control.set(control)
            try:
                control.check()
                results.put((True, platform.execute_chat(profile_id, request)))
            except BaseException as exc:
                results.put((False, exc))
            finally:
                current_control.reset(binding)
                self._slots.release()

        threading.Thread(target=lambda: context.run(run), daemon=True, name="loom-model-request").start()
        try:
            while True:
                if token.cancelled:
                    raise ModelCancelled("model request cancelled")
                if time.monotonic() >= deadline:
                    raise TimeoutError("model request deadline exceeded")
                try:
                    ok, result = results.get(timeout=0.05)
                except queue.Empty:
                    continue
                if token.cancelled:
                    raise ModelCancelled()
                if not ok:
                    raise result
                return result
        except BaseException:
            control.cancel()
            raise
