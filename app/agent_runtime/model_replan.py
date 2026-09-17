"""Process-local live-steering signals for superseding in-flight model samples.

The durable steering inbox remains the source of truth for user intent. This
module only coordinates process-local model work: it lets a steering submission
invalidate the model sample that was prepared before that submission, without
cancelling the logical turn or an already-running tool.
"""
from __future__ import annotations

import threading
import time
import weakref
from dataclasses import dataclass, field
from typing import Any


@dataclass
class _ReplanState:
    condition: threading.Condition = field(default_factory=threading.Condition)
    revision: int = 0
    model_sampling: int = 0


_states: weakref.WeakKeyDictionary[Any, _ReplanState] = weakref.WeakKeyDictionary()
_states_lock = threading.Lock()


def _state(token: Any) -> _ReplanState:
    with _states_lock:
        state = _states.get(token)
        if state is None:
            state = _ReplanState()
            _states[token] = state
        return state


def revision(token: Any) -> int:
    state = _state(token)
    with state.condition:
        return state.revision


def request_replan(token: Any) -> bool:
    """Invalidate the current model sample and return whether one is active."""

    state = _state(token)
    with state.condition:
        state.revision += 1
        sampling = state.model_sampling > 0
        state.condition.notify_all()
        return sampling


def begin_sampling(token: Any) -> None:
    state = _state(token)
    with state.condition:
        state.model_sampling += 1


def end_sampling(token: Any) -> None:
    state = _state(token)
    with state.condition:
        state.model_sampling = max(0, state.model_sampling - 1)
        state.condition.notify_all()


def changed(token: Any, expected_revision: int) -> bool:
    return revision(token) != int(expected_revision)


def wait_for_signal(token: Any, expected_revision: int, timeout: float) -> str:
    """Wait for turn cancellation or a newer steering revision.

    Returns ``"cancel"``, ``"steer"``, or ``"timeout"``. Cancellation is
    intentionally checked first so an explicit Stop always wins a race with a
    steering submission.
    """

    deadline = time.monotonic() + max(0.0, float(timeout))
    state = _state(token)
    while True:
        if bool(getattr(token, "cancelled", False)):
            return "cancel"
        with state.condition:
            if state.revision != int(expected_revision):
                return "steer"
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return "timeout"
            # CancellationToken's stop event is separate from this condition, so
            # use a short bounded wait to keep explicit Stop responsive too.
            state.condition.wait(min(0.05, remaining))


__all__ = [
    "begin_sampling",
    "changed",
    "end_sampling",
    "request_replan",
    "revision",
    "wait_for_signal",
]
