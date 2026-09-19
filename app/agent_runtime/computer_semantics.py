from __future__ import annotations

import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from enum import Enum
from queue import SimpleQueue
from typing import Any, Callable

from .computer_types import ComputerControl

#: Waiting budgets, in milliseconds, for the two ways Loom asks for semantics.
#: ``best_effort`` sits on the critical path of every observation, so its budget
#: is a UI latency budget rather than a generous timeout. ``required`` is only
#: used by the legacy grounder path, which cannot promote a click without a
#: control map, so it may wait longer before giving up.
DEFAULT_BEST_EFFORT_DEADLINE_MS = 250
DEFAULT_REQUIRED_DEADLINE_MS = 1500

#: Ceilings for the same two modes. A window is allowed to earn a larger budget
#: than the default by demonstrating what it costs - a healthy Electron window
#: with a materialized accessibility tree measures ~350ms, so a fixed 250ms
#: budget would deny hints to every such application forever. Nothing may earn
#: more than these, which is what keeps a pathological window bounded.
MAX_BEST_EFFORT_DEADLINE_MS = 900
MAX_REQUIRED_DEADLINE_MS = 3000

#: How long the breaker stays open after a deadline breach, and how far repeated
#: breaches may extend it. A slow desktop tends to stay slow for the length of
#: whatever it is busy with, so re-probing it every step only re-pays the wait.
DEFAULT_COOLDOWN_S = 20.0
MAX_COOLDOWN_S = 120.0

#: Consecutive deadline breaches before the global breaker opens. One miss means
#: one window was busy and is handled by the per-window memory below; repeated
#: misses mean the desktop itself is not answering and nothing is worth asking.
BREAKER_TIMEOUT_THRESHOLD = 2

#: How long one call may occupy the worker before the worker is presumed lost and
#: replaced. Well above any legitimate deadline: reaching this means the call is
#: not slow, it is not coming back.
STUCK_WORKER_RECOVERY_S = 60.0

#: Per-window latency memory. Windows differ by orders of magnitude - a native
#: dialog answers in 15ms, a Chromium window with a live accessibility tree in
#: ~350ms, a busy one not at all - so the budget is measured per window rather
#: than guessed globally. A window that misses even its ceiling is considered
#: hopeless and only gets a cheap probe, which is enough to notice if it
#: recovers.
_SLOW_WINDOW_MEMORY = 32
_SLOW_WINDOW_RETRY_DEADLINE_MS = 80

#: Headroom over a window's measured cost, so ordinary jitter does not turn a
#: known-good window into a miss.
_COST_HEADROOM = 1.5
_COST_MARGIN_MS = 60.0


class SemanticState(str, Enum):
    """Outcome of one attempt to read the semantic layer of a window."""

    READY = "ready"
    PARTIAL = "partial"
    UNAVAILABLE = "unavailable"
    SKIPPED = "skipped"


@dataclass(frozen=True, slots=True)
class SemanticLayer:
    """UI Automation controls for one window, plus how that attempt went.

    ``controls`` is always usable: an empty tuple simply means Loom is running
    without semantic hints this step. Callers must never treat the absence of
    controls as an error, because on a busy desktop it is the normal case.
    """

    state: SemanticState = SemanticState.SKIPPED
    controls: tuple[ComputerControl, ...] = ()
    mapping: dict[str, Any] = field(default_factory=dict)
    reason: str = ""
    duration_ms: float = 0.0
    deadline_ms: float = 0.0

    @property
    def ready(self) -> bool:
        return self.state in {SemanticState.READY, SemanticState.PARTIAL}

    def to_dict(self) -> dict[str, object]:
        return {
            "state": self.state.value,
            "reason": self.reason,
            "controls": len(self.controls),
            "duration_ms": round(float(self.duration_ms), 3),
            "deadline_ms": round(float(self.deadline_ms), 3),
        }


@dataclass(slots=True)
class _Job:
    call: Callable[[], Any]
    done: threading.Event = field(default_factory=threading.Event)
    value: Any = None
    error: BaseException | None = None
    duration_ms: float = 0.0


@dataclass(frozen=True, slots=True)
class DispatchResult:
    """Result of dispatching one UI Automation call onto the worker."""

    ok: bool
    value: Any = None
    reason: str = ""
    duration_ms: float = 0.0
    deadline_ms: float = 0.0


class SemanticLayerProvider:
    """Deadline-bounded, single-apartment executor for UI Automation work.

    UI Automation is a synchronous cross-process COM call into the message loop
    of an arbitrary third-party application, so its latency is set by the app
    being automated rather than by Loom. A busy Electron renderer has made one
    control walk take 108 seconds in the field. Nothing on Loom's critical path
    may inherit that latency, so every UIA touch goes through here.

    Three properties make the bound real:

    * One dedicated worker thread owns every UIA call, so all UIA objects live
      in a single COM apartment and wrappers stay usable for native invokes.
    * Callers wait with a deadline. A blocked cross-apartment COM call cannot be
      aborted from outside, so "deadline" means Loom stops waiting, not that the
      call stops running; serializing onto one worker bounds the damage to
      exactly one stuck thread instead of one per attempt.
    * A circuit breaker plus per-window latency memory stop Loom from paying the
      wait again while the desktop is proven slow. This is what turns a
      pathological window from a repeated multi-second tax into a single one.
    """

    def __init__(
        self,
        *,
        name: str = "uia",
        cooldown_s: float = DEFAULT_COOLDOWN_S,
        initializer: Callable[[], None] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.name = str(name or "uia")
        self.cooldown_s = max(0.0, float(cooldown_s))
        self._initializer = initializer
        self._clock = clock
        self._lock = threading.Lock()
        self._queue: SimpleQueue[_Job | None] = SimpleQueue()
        self._thread: threading.Thread | None = None
        self._generation = 0
        self._closed = False
        self._inflight = False
        self._inflight_since = 0.0
        self._recycles = 0
        self._warmed = False
        self._open_until = 0.0
        self._consecutive_timeouts = 0
        self._last_duration_ms = 0.0
        self._slow_windows: OrderedDict[str, float] = OrderedDict()
        self._window_cost: OrderedDict[str, float] = OrderedDict()
        self._window_failed_at: OrderedDict[str, float] = OrderedDict()
        self._window_truncates: OrderedDict[str, float] = OrderedDict()
        self._counters = {"dispatched": 0, "completed": 0, "timeout": 0, "busy": 0, "circuit_open": 0}

    # -- lifecycle -----------------------------------------------------------

    def _ensure_worker(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._start_worker()

    def _start_worker(self) -> None:
        self._generation += 1
        generation = self._generation
        queue = self._queue
        thread = threading.Thread(
            target=self._serve,
            args=(queue, generation),
            name=f"loom-computer-{self.name}-{generation}",
            daemon=True,
        )
        self._thread = thread
        thread.start()

    def _recycle_worker(self) -> None:
        """Replace a worker that has been stuck in one COM call for too long.

        Deadlines protect callers, and serializing onto one worker bounds the
        damage to one stuck thread - but if that call never returns, the single
        worker stays occupied and the semantic layer is dead for the rest of the
        process. That is a quiet permanent degradation, which is exactly what
        this subsystem is supposed to stop having.

        The stuck thread cannot be killed (a blocked cross-apartment COM call is
        not interruptible), so it is orphaned instead: it keeps its own queue,
        receives a stop sentinel on it, and exits if the call ever returns. A new
        worker with a fresh queue takes over. The orphan is a daemon thread and
        costs one stack plus whatever UIA objects it holds.
        """

        self._queue.put(None)
        self._queue = SimpleQueue()
        self._inflight = False
        self._recycles += 1
        self._start_worker()

    def _serve(self, queue: "SimpleQueue[_Job | None]", generation: int) -> None:
        if self._initializer is not None:
            try:
                self._initializer()
            except Exception:
                # COM initialization is advisory here: comtypes initializes the
                # apartment lazily on first use, so a failure to pre-initialize
                # must not take the worker down.
                pass
        while True:
            job = queue.get()
            if job is None:
                return
            started = self._clock()
            try:
                job.value = job.call()
            except BaseException as exc:  # noqa: BLE001 - reported to the caller
                job.error = exc
            finally:
                job.duration_ms = (self._clock() - started) * 1000.0
                with self._lock:
                    # An orphaned worker finishing late must not clear the state
                    # of the worker that replaced it.
                    if generation == self._generation:
                        self._inflight = False
                        self._last_duration_ms = job.duration_ms
                job.done.set()

    def warmup(self, call: Callable[[], Any]) -> bool:
        """Pay one-time UIA client construction before anyone is waiting.

        Building the UI Automation client (typelib import, COM object creation)
        costs hundreds of milliseconds once per process. Charged to the first
        real request it looks exactly like a slow window: the budget is missed,
        the window is remembered as slow and the breaker trips, so a healthy
        desktop loses its hints for the first minute of every session. Running it
        eagerly, while the model is still deciding what to do, removes the whole
        class of false positive.
        """

        with self._lock:
            if self._closed or self._warmed or self._inflight:
                return False
            self._warmed = True
            self._inflight = True
            self._ensure_worker()
            self._queue.put(_Job(call=call))
        return True

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
        self._queue.put(None)

    # -- breaker -------------------------------------------------------------

    def _trip(self) -> None:
        self._consecutive_timeouts += 1
        if self._consecutive_timeouts < BREAKER_TIMEOUT_THRESHOLD:
            # One miss is a slow window, not a slow desktop. The per-window
            # memory already shrank that window's next budget; opening the global
            # breaker here would punish every other window for it.
            return
        backoff = self.cooldown_s * (2 ** min(3, self._consecutive_timeouts - BREAKER_TIMEOUT_THRESHOLD))
        self._open_until = self._clock() + min(MAX_COOLDOWN_S, backoff)

    def _reset(self) -> None:
        self._consecutive_timeouts = 0
        self._open_until = 0.0

    def _remember(self, store: OrderedDict[str, float], key: str, value: float) -> None:
        store[key] = value
        store.move_to_end(key)
        while len(store) > _SLOW_WINDOW_MEMORY:
            store.popitem(last=False)

    def _note_success(self, window_key: str, duration_ms: float) -> None:
        key = str(window_key or "")
        if not key:
            return
        self._slow_windows.pop(key, None)
        self._window_failed_at.pop(key, None)
        self._remember(self._window_cost, key, float(duration_ms))

    def note_truncated(self, window_key: str, deadline_ms: float) -> None:
        """Record the budget at which this window yielded a usable partial walk.

        Widening helps a window that would have finished slightly later. It does
        not help one whose walk is simply larger than any budget: more time just
        buys more controls, indefinitely, while consumers render a few dozen. So
        the search stops here rather than escalating to the ceiling on every
        step - but it stops at the budget that worked, not at the default. The
        underlying enumeration is one atomic cross-process call, so a budget
        slightly too small returns nothing at all rather than proportionally
        less; dropping back to the default would pay most of the cost for none
        of the benefit.
        """

        key = str(window_key or "")
        if not key:
            return
        with self._lock:
            self._window_cost.pop(key, None)
            self._window_failed_at.pop(key, None)
            self._remember(self._window_truncates, key, float(deadline_ms))

    def _note_timeout(self, window_key: str, deadline_ms: float, *, hopeless: bool) -> None:
        key = str(window_key or "")
        if not key:
            return
        self._window_cost.pop(key, None)
        self._remember(self._window_failed_at, key, float(deadline_ms))
        if hopeless:
            self._remember(self._slow_windows, key, self._clock())

    def deadline_for(self, window_key: str, deadline_ms: float, *, ceiling_ms: float) -> float:
        """Budget for this window: the default, corrected by what it has shown.

        Three cases, in order. A window that answered before is given its
        measured cost plus headroom. A window that missed a budget is retried
        with a wider one, because a miss says the cost is higher than that
        budget, not that the window is broken - the search stops at the ceiling.
        A window that missed even the ceiling is only probed cheaply, so
        discovering that it recovered costs almost nothing.
        """

        key = str(window_key or "")
        default = float(deadline_ms)
        ceiling = max(default, float(ceiling_ms))
        with self._lock:
            if key in self._slow_windows:
                return min(default, float(_SLOW_WINDOW_RETRY_DEADLINE_MS))
            settled = self._window_truncates.get(key)
            if settled is not None:
                return max(default, min(ceiling, settled))
            cost = self._window_cost.get(key)
            failed_at = self._window_failed_at.get(key)
        if cost is not None:
            return max(default, min(ceiling, cost * _COST_HEADROOM + _COST_MARGIN_MS))
        if failed_at is not None:
            return max(default, min(ceiling, failed_at * 2.0))
        return default

    # -- dispatch ------------------------------------------------------------

    def run(
        self,
        call: Callable[[], Any],
        *,
        deadline_ms: float,
        window_key: str = "",
        decisive: bool = True,
    ) -> DispatchResult:
        """Run one UIA call on the worker, waiting at most ``deadline_ms``.

        ``decisive`` says whether a miss here is evidence about the desktop as a
        whole. A miss while still widening one window's budget is not: it only
        means that window costs more than the budget tried so far, and counting
        it would let one heavy window open the breaker for every other window.
        """

        deadline_ms = max(1.0, float(deadline_ms))
        with self._lock:
            if self._closed:
                return DispatchResult(False, reason="closed", deadline_ms=deadline_ms)
            if self._inflight:
                if self._clock() - self._inflight_since >= STUCK_WORKER_RECOVERY_S:
                    self._recycle_worker()
                else:
                    # A previous call is still blocked inside COM. Queuing behind
                    # it would make this caller wait for both, so refuse now.
                    self._counters["busy"] += 1
                    return DispatchResult(False, reason="busy", deadline_ms=deadline_ms)
            if self._clock() < self._open_until:
                self._counters["circuit_open"] += 1
                return DispatchResult(False, reason="circuit_open", deadline_ms=deadline_ms)
            self._inflight = True
            self._inflight_since = self._clock()
            self._counters["dispatched"] += 1
            self._ensure_worker()
            job = _Job(call=call)
            self._queue.put(job)

        completed = job.done.wait(timeout=deadline_ms / 1000.0)
        if not completed:
            with self._lock:
                self._counters["timeout"] += 1
                self._note_timeout(window_key, deadline_ms, hopeless=decisive)
                if decisive:
                    self._trip()
            return DispatchResult(False, reason="deadline", deadline_ms=deadline_ms)

        with self._lock:
            self._counters["completed"] += 1
            self._reset()
            self._note_success(window_key, job.duration_ms)
        if job.error is not None:
            return DispatchResult(
                False,
                reason=f"error:{type(job.error).__name__}",
                duration_ms=job.duration_ms,
                deadline_ms=deadline_ms,
            )
        return DispatchResult(
            True,
            value=job.value,
            duration_ms=job.duration_ms,
            deadline_ms=deadline_ms,
        )

    def collect(
        self,
        call: Callable[[], tuple[tuple[ComputerControl, ...], dict[str, Any], bool]],
        *,
        deadline_ms: float,
        ceiling_ms: float = 0.0,
        window_key: str = "",
    ) -> SemanticLayer:
        """Collect one window's controls, degrading instead of blocking."""

        ceiling = max(float(deadline_ms), float(ceiling_ms or deadline_ms))
        effective = self.deadline_for(window_key, deadline_ms, ceiling_ms=ceiling)
        result = self.run(
            call,
            deadline_ms=effective,
            window_key=window_key,
            decisive=effective >= ceiling,
        )
        if not result.ok:
            return SemanticLayer(
                state=SemanticState.UNAVAILABLE,
                reason=result.reason,
                duration_ms=result.duration_ms,
                deadline_ms=result.deadline_ms,
            )
        try:
            controls, mapping, truncated = result.value
        except (TypeError, ValueError):
            return SemanticLayer(
                state=SemanticState.UNAVAILABLE,
                reason="malformed_collector_result",
                duration_ms=result.duration_ms,
                deadline_ms=result.deadline_ms,
            )
        collected = tuple(controls)
        if truncated and collected:
            self.note_truncated(window_key, effective)
        if truncated and not collected:
            # The walk returned inside its budget but produced nothing usable.
            # Calling that "partial" would claim we saw part of the window; we
            # saw none of it.
            return SemanticLayer(
                state=SemanticState.UNAVAILABLE,
                reason="budget_exhausted",
                duration_ms=result.duration_ms,
                deadline_ms=result.deadline_ms,
            )
        return SemanticLayer(
            state=SemanticState.PARTIAL if truncated else SemanticState.READY,
            controls=collected,
            mapping=dict(mapping),
            reason="budget_exhausted" if truncated else "",
            duration_ms=result.duration_ms,
            deadline_ms=result.deadline_ms,
        )

    def status(self) -> dict[str, object]:
        with self._lock:
            open_for = max(0.0, self._open_until - self._clock())
            return {
                "backend": self.name,
                "inflight": self._inflight,
                "circuit_open": open_for > 0.0,
                "circuit_open_for_s": round(open_for, 3),
                "consecutive_timeouts": self._consecutive_timeouts,
                "last_duration_ms": round(self._last_duration_ms, 3),
                "slow_windows": len(self._slow_windows),
                "measured_windows": len(self._window_cost),
                "worker_recycles": self._recycles,
                "counters": dict(self._counters),
            }


__all__ = [
    "DEFAULT_BEST_EFFORT_DEADLINE_MS",
    "DEFAULT_REQUIRED_DEADLINE_MS",
    "DispatchResult",
    "SemanticLayer",
    "SemanticLayerProvider",
    "SemanticState",
]
