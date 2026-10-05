"""Bounded model execution service; abandoned requests cannot commit history."""
from __future__ import annotations

import contextvars
import logging
import queue
import threading
import time
from dataclasses import replace
from app.ai.contracts import ModelResponse
from app.ai.profiles import DEFAULT_STREAM_IDLE_TIMEOUT_SECONDS

from app.ai.execution_control import ExecutionControl, ModelCancelled, ModelSteered, current_control

from .model_replan import begin_sampling, changed, end_sampling


_log = logging.getLogger(__name__)


class ModelRequestTimeout(TimeoutError):
    """Only safe model-sampling timeouts may be retried; tools never run here."""

    def __init__(self, message: str, *, reason: str, retryable: bool) -> None:
        super().__init__(message)
        self.reason = reason
        self.retryable = retryable


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
        timeout: float = DEFAULT_STREAM_IDLE_TIMEOUT_SECONDS,
        stall_timeout: float | None = None,
        max_duration: float = 900.0,
    ) -> None:
        self._slots = threading.BoundedSemaphore(max_inflight)
        self.timeout = timeout
        self._explicit_stall_timeout = stall_timeout is not None
        self.stall_timeout = max(0.01, float(stall_timeout if stall_timeout is not None else DEFAULT_STREAM_IDLE_TIMEOUT_SECONDS))
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
        progress_at = control.progress_at or getattr(control, "last_chunk_at", 0)
        if not progress_at:
            # Still waiting for the first sign of life. Non-streaming backends
            # never report any, which is why this path keeps the old meaning.
            if now >= deadline:
                raise ModelRequestTimeout(
                    "model request deadline exceeded: no provider output received "
                    f"within {int(self.timeout)}s",
                    reason="first_output_timeout", retryable=True,
                )
            return
        idle = now - progress_at
        stall_timeout = getattr(control, "stall_timeout", self.stall_timeout)
        if idle >= stall_timeout:
            raise ModelRequestTimeout(
                f"model stopped producing output for {int(idle)}s "
                f"(stall timeout {int(stall_timeout)}s)",
                reason="stream_stall_timeout", retryable=True,
            )
        if now - started >= self.max_duration:
            raise ModelRequestTimeout(
                f"model request exceeded its maximum duration of {int(self.max_duration)}s "
                "while still producing output",
                reason="max_duration_timeout", retryable=False,
            )

    def execute(self, platform, profile_id, request, token, *, steering_revision: int | None = None, on_activity=None, on_retry=None):
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
            control.retry_observer = on_retry
            control.stall_timeout = self.stall_timeout
            registry = getattr(platform, "registry", None)
            if registry is not None and not self._explicit_stall_timeout:
                control.stall_timeout = getattr(registry.get(profile_id), "stream_idle_timeout_seconds", self.stall_timeout)
                deadline = started + max(self.timeout, control.stall_timeout)
            control.request_purpose = getattr(request, "purpose", "generation")
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
                next_activity = 0.0
                while True:
                    self._check_signal(token, steering_revision)
                    if on_activity is not None and time.monotonic() >= next_activity:
                        on_activity({"active": True, "contentGapSeconds": max(0, time.monotonic() - (control.progress_at or control.started_at))})
                        next_activity = time.monotonic() + 2.0
                    try:
                        ok, result = results.get(timeout=0.05)
                    except queue.Empty:
                        self._check_deadlines(control, started, deadline)
                        continue
                    self._check_signal(token, steering_revision)
                    if not ok:
                        result.stream_timing = control.stream_timing()
                        raise result
                    if isinstance(result, ModelResponse):
                        result = replace(result, stream_timing=control.stream_timing())
                    return result
            except BaseException as exc:
                exc.stream_timing = control.stream_timing()
                if isinstance(exc, TimeoutError):
                    progress_at = control.progress_at
                    _log.warning(
                        "Model request timed out: profile=%s elapsed=%.1fs "
                        "received_generated_content=%s last_output_gap=%s error=%s",
                        profile_id,
                        time.monotonic() - started,
                        bool(progress_at),
                        f"{time.monotonic() - progress_at:.1f}s" if progress_at else "n/a",
                        exc,
                    )
                # Provider stream readers register close callbacks on the request
                # control. A steer therefore stops token generation promptly while
                # leaving the turn's CancellationToken untouched.
                control.cancel()
                raise
            finally:
                if on_activity is not None:
                    on_activity({"active": False, "contentGapSeconds": 0})
        finally:
            end_sampling(token)
