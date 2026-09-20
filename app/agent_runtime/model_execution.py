"""Bounded model execution service; abandoned requests cannot commit history."""
from __future__ import annotations

import contextvars
import queue
import threading
import time

from app.ai.execution_control import ExecutionControl, ModelCancelled, ModelSteered, current_control

from .model_replan import begin_sampling, changed, end_sampling


class ModelExecutor:
    """Bounded model execution.

    The bound used to be one number: 150 seconds of wall clock, whatever the
    request was doing. That is the right question to ask of a request that has
    produced nothing and the wrong one to ask of a response that is streaming
    perfectly well and is merely long. A reasoning model at max effort emits
    tens of thousands of thinking tokens at a steady ~195/s, so 150s was a hard
    ceiling of roughly 29,000 tokens: past that, the turn could not succeed no
    matter how long anyone waited, and what the user saw was the UI sitting
    there saying nothing before the turn died.

    So the deadline now follows what the request is actually doing:

    - nothing received yet -> ``timeout``, exactly as before. A backend with no
      streaming path never reports progress, so its behaviour is unchanged.
    - producing -> allowed to continue, as long as no single gap exceeds
      ``stall_timeout``. A stream that truly dies is now caught sooner than the
      old 150s, not later.
    - ``max_duration`` remains as a backstop against a runaway that streams
      forever.
    """

    def __init__(
        self,
        max_inflight: int = 16,
        timeout: float = 150.0,
        stall_timeout: float = 60.0,
        max_duration: float = 900.0,
    ) -> None:
        self._slots = threading.BoundedSemaphore(max_inflight)
        self.timeout = timeout
        self.stall_timeout = max(1.0, float(stall_timeout))
        self.max_duration = max(float(timeout), float(max_duration))

    @staticmethod
    def _check_signal(token, steering_revision: int | None) -> None:
        # Explicit Stop wins a race with steering. Steering only invalidates this
        # model sample; it must never become a logical turn cancellation.
        if token.cancelled:
            raise ModelCancelled("model request cancelled")
        if steering_revision is not None and changed(token, steering_revision):
            raise ModelSteered("model request superseded by same-turn steering")

    def _check_deadlines(self, control, started: float, deadline: float) -> None:
        now = time.monotonic()
        progress_at = control.progress_at
        if not progress_at:
            # Still waiting for the first sign of life. Non-streaming backends
            # never report any, which is why this path keeps the old meaning.
            if now >= deadline:
                raise TimeoutError("model request deadline exceeded")
            return
        idle = now - progress_at
        if idle >= self.stall_timeout:
            raise TimeoutError(
                f"model stopped producing output for {int(idle)}s "
                f"(stall timeout {int(self.stall_timeout)}s)"
            )
        if now - started >= self.max_duration:
            raise TimeoutError(
                f"model request exceeded its maximum duration of {int(self.max_duration)}s "
                "while still producing output"
            )

    def execute(self, platform, profile_id, request, token, *, steering_revision: int | None = None):
        started = time.monotonic()
        deadline = started + self.timeout
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
                    self._check_deadlines(control, started, deadline)
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
