"""Bounded model execution service; abandoned requests cannot commit history."""
from __future__ import annotations

import contextvars
import queue
import threading
import time

from app.ai.execution_control import ExecutionControl, ModelCancelled, ModelSteered, current_control

from .model_replan import begin_sampling, changed, end_sampling


class ModelExecutor:
    def __init__(self, max_inflight: int = 16, timeout: float = 150.0) -> None:
        self._slots = threading.BoundedSemaphore(max_inflight)
        self.timeout = timeout

    @staticmethod
    def _check_signal(token, steering_revision: int | None) -> None:
        # Explicit Stop wins a race with steering. Steering only invalidates this
        # model sample; it must never become a logical turn cancellation.
        if token.cancelled:
            raise ModelCancelled("model request cancelled")
        if steering_revision is not None and changed(token, steering_revision):
            raise ModelSteered("model request superseded by same-turn steering")

    def execute(self, platform, profile_id, request, token, *, steering_revision: int | None = None):
        deadline = time.monotonic() + self.timeout
        begin_sampling(token)
        try:
            while not self._slots.acquire(timeout=0.05):
                self._check_signal(token, steering_revision)
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
                    self._check_signal(token, steering_revision)
                    if time.monotonic() >= deadline:
                        raise TimeoutError("model request deadline exceeded")
                    try:
                        ok, result = results.get(timeout=0.05)
                    except queue.Empty:
                        continue
                    self._check_signal(token, steering_revision)
                    if not ok:
                        raise result
                    return result
            except BaseException:
                # Provider stream readers register close callbacks on the request
                # control. A steer therefore stops token generation promptly while
                # leaving the turn's CancellationToken untouched.
                control.cancel()
                raise
        finally:
            end_sampling(token)
