"""Latency contract for Computer Use observation.

From a real trace (run ccd23506, 2026-09-18): one ``observe`` took 108.0s, of
which 107.9s was UI Automation enumeration of the foreground window, and the
action that followed took a further 132.5s because it re-enumerated the same
window after injecting input. Four and a half minutes produced no progress, and
the whole subsystem was unresponsive throughout because one lock was held across
that call.

Nothing in the suite could fail on that, because the observation contract had no
time dimension at all: ``timings_ms`` was recorded and never asserted. These
tests are that missing dimension. They deliberately model the failure as
"the desktop is slow", not "the desktop is broken", because a busy application
answering UIA slowly is the normal case Loom has to survive.
"""

from __future__ import annotations

import threading
import time
from dataclasses import replace

from app.agent_runtime.computer_runtime import ComputerSessionStore
from app.agent_runtime.computer_semantics import (
    _SLOW_WINDOW_RETRY_DEADLINE_MS,
    SemanticLayerProvider,
    SemanticState,
)
from app.agent_runtime.computer_types import (
    ComputerAction,
    ComputerActionType,
    ComputerControl,
    ComputerExecution,
    ComputerFrame,
    ComputerObservation,
    ComputerPoint,
    ComputerRect,
    ComputerWindow,
)
from app.agent_runtime.computer_windows import (
    SEMANTICS_BEST_EFFORT,
    SEMANTICS_REQUIRED,
    SEMANTICS_SKIP,
    PyWinAutoWindowsOperator,
)

#: The budget every assertion here is written against. A step may cost the model
#: seconds of thinking; it may not cost Loom seconds of waiting on a window.
OBSERVE_BUDGET_S = 1.0


class _Barrier:
    """A collector that blocks until the test releases it."""

    def __init__(self) -> None:
        self.release = threading.Event()
        self.entered = threading.Event()
        self.calls = 0

    def __call__(self):
        self.calls += 1
        self.entered.set()
        self.release.wait(timeout=30)
        return ((), {}, False)


def _provider(**kwargs) -> SemanticLayerProvider:
    return SemanticLayerProvider(cooldown_s=kwargs.pop("cooldown_s", 0.05), **kwargs)


def test_stuck_uia_call_does_not_hold_the_caller_past_its_deadline():
    provider = _provider()
    blocked = _Barrier()
    try:
        started = time.perf_counter()
        layer = provider.collect(blocked, deadline_ms=120, window_key="0x1")
        elapsed = time.perf_counter() - started

        assert layer.state is SemanticState.UNAVAILABLE
        assert layer.reason == "deadline"
        assert layer.controls == ()
        assert elapsed < OBSERVE_BUDGET_S
    finally:
        blocked.release.set()
        provider.close()


def test_second_caller_is_refused_instantly_while_one_call_is_still_stuck():
    """The 108s call had a successor. Neither may queue behind the other.

    A blocked cross-apartment COM call cannot be aborted, so the worker is still
    inside it. Queuing would make the next caller wait for both calls in turn,
    which is how a single slow window becomes an unbounded stall.
    """

    provider = _provider()
    blocked = _Barrier()
    try:
        provider.collect(blocked, deadline_ms=100, window_key="0x1")
        assert blocked.entered.wait(timeout=5)

        started = time.perf_counter()
        layer = provider.collect(blocked, deadline_ms=5000, window_key="0x1")
        elapsed = time.perf_counter() - started

        assert layer.state is SemanticState.UNAVAILABLE
        assert layer.reason == "busy"
        assert elapsed < 0.1
        assert blocked.calls == 1
    finally:
        blocked.release.set()
        provider.close()


def test_a_walk_that_collected_nothing_is_not_reported_as_partial():
    provider = _provider()
    try:
        layer = provider.collect(lambda: ((), {}, True), deadline_ms=500)
        assert layer.state is SemanticState.UNAVAILABLE
        assert layer.reason == "budget_exhausted"
    finally:
        provider.close()


def _drain(provider) -> None:
    for _ in range(300):
        if not provider.status()["inflight"]:
            return
        time.sleep(0.01)
    raise AssertionError("the worker never finished its blocked call")


def test_one_slow_window_does_not_cut_every_other_window_off():
    """A single miss is a busy window, not a broken desktop.

    Opening the global breaker on the first timeout would mean one mid-render
    chat window costs every other application its hints for the next minute.
    """

    provider = _provider()
    blocked = _Barrier()
    fast_calls = []

    def fast():
        fast_calls.append(1)
        return ((), {}, False)

    try:
        provider.collect(blocked, deadline_ms=80, window_key="0xslow")
        assert blocked.entered.wait(timeout=5)
        blocked.release.set()
        _drain(provider)

        other = provider.collect(fast, deadline_ms=500, window_key="0xother")
        assert other.state is SemanticState.READY
        assert fast_calls == [1]
    finally:
        blocked.release.set()
        provider.close()


def test_breaker_opens_after_repeated_breaches_and_closes_once_it_recovers():
    now = [1000.0]
    provider = SemanticLayerProvider(cooldown_s=10.0, clock=lambda: now[0])
    fast_calls = []

    def fast():
        fast_calls.append(1)
        return ((), {}, False)

    try:
        for index in range(2):
            blocked = _Barrier()
            provider.collect(blocked, deadline_ms=80, window_key=f"0x{index}")
            assert blocked.entered.wait(timeout=5)
            blocked.release.set()
            _drain(provider)

        assert provider.status()["circuit_open"] is True
        refused = provider.collect(fast, deadline_ms=500, window_key="0x9")
        assert refused.state is SemanticState.UNAVAILABLE
        assert refused.reason == "circuit_open"
        assert fast_calls == []

        now[0] += 60.0
        recovered = provider.collect(fast, deadline_ms=500, window_key="0x9")
        assert recovered.state is SemanticState.READY
        assert fast_calls == [1]
        assert provider.status()["circuit_open"] is False
    finally:
        provider.close()


def test_a_worker_lost_inside_com_forever_is_replaced_rather_than_left_stuck():
    """Deadlines protect callers; this protects the session.

    If a COM call never returns, the single worker stays occupied and every later
    request is refused as "busy" for the rest of the process - a permanent silent
    degradation. The stuck thread cannot be killed, so it is orphaned and a new
    worker takes over.
    """

    now = [1000.0]
    provider = SemanticLayerProvider(cooldown_s=0.05, clock=lambda: now[0])
    wedged = _Barrier()
    fast_calls = []

    def fast():
        fast_calls.append(1)
        return ((), {}, False)

    try:
        provider.collect(wedged, deadline_ms=80, window_key="0xwedged")
        assert wedged.entered.wait(timeout=5)

        # Still inside the call: other callers are refused, not queued.
        assert provider.collect(fast, deadline_ms=200).reason == "busy"
        assert fast_calls == []

        # Long past any legitimate deadline: the call is not slow, it is lost.
        now[0] += 120.0
        recovered = provider.collect(fast, deadline_ms=2000, window_key="0xother")

        assert recovered.state is SemanticState.READY
        assert fast_calls == [1]
        assert provider.status()["worker_recycles"] == 1
    finally:
        wedged.release.set()
        provider.close()


def test_an_orphaned_worker_cannot_corrupt_the_worker_that_replaced_it():
    now = [1000.0]
    provider = SemanticLayerProvider(cooldown_s=0.05, clock=lambda: now[0])
    wedged = _Barrier()
    try:
        provider.collect(wedged, deadline_ms=80, window_key="0xwedged")
        assert wedged.entered.wait(timeout=5)
        now[0] += 120.0

        blocking = _Barrier()
        provider.collect(blocking, deadline_ms=50, window_key="0xnew")
        assert blocking.entered.wait(timeout=5)
        assert provider.status()["inflight"] is True

        # The orphan finishes late. It must not clear the new worker's state.
        wedged.release.set()
        time.sleep(0.2)
        assert provider.status()["inflight"] is True
    finally:
        wedged.release.set()
        blocking.release.set()
        provider.close()


def test_warmup_runs_once_and_keeps_the_first_real_caller_off_the_cold_path():
    """Cold UIA client construction must not be charged to a window."""

    provider = _provider()
    warmed = []
    try:
        assert provider.warmup(lambda: warmed.append(1)) is True
        assert provider.warmup(lambda: warmed.append(2)) is False
        for _ in range(300):
            if warmed:
                break
            time.sleep(0.01)
        assert warmed == [1]
        assert provider.status()["consecutive_timeouts"] == 0
        assert provider.status()["slow_windows"] == 0
    finally:
        provider.close()


def test_an_unmeasured_window_just_gets_the_default_budget():
    provider = _provider()
    try:
        assert provider.deadline_for("0xnew", 250, ceiling_ms=900) == 250
    finally:
        provider.close()


def test_a_window_that_misses_its_budget_is_retried_wider_not_banned():
    """A miss says the window costs more than that budget, not that it is broken.

    A healthy Chromium window with a live accessibility tree measures ~350ms, so
    a fixed 250ms budget would permanently deny hints to an application that is
    working perfectly well. The budget has to go looking for the real cost.
    """

    provider = _provider()
    blocked = _Barrier()
    try:
        layer = provider.collect(blocked, deadline_ms=120, ceiling_ms=900, window_key="0xchat")
        assert layer.state is SemanticState.UNAVAILABLE

        widened = provider.deadline_for("0xchat", 120, ceiling_ms=900)
        assert widened == 240

        blocked.release.set()
        _drain(provider)
        blocked.release.clear()

        # Still bounded: doubling stops at the ceiling however slow the window is.
        provider.collect(blocked, deadline_ms=600, ceiling_ms=900, window_key="0xheavy")
        assert provider.deadline_for("0xheavy", 120, ceiling_ms=900) == 900
    finally:
        blocked.release.set()
        provider.close()


def test_a_window_that_misses_even_its_ceiling_is_only_probed_cheaply():
    provider = _provider()
    blocked = _Barrier()
    try:
        provider.collect(blocked, deadline_ms=100, ceiling_ms=100, window_key="0xdead")

        assert provider.deadline_for("0xdead", 900, ceiling_ms=900) == _SLOW_WINDOW_RETRY_DEADLINE_MS
        assert provider.deadline_for("0xother", 900, ceiling_ms=900) == 900
    finally:
        blocked.release.set()
        provider.close()


def test_a_measured_window_keeps_the_budget_it_earned():
    provider = _provider()
    try:
        def slow_but_fine():
            time.sleep(0.2)
            return ((), {}, False)

        layer = provider.collect(slow_but_fine, deadline_ms=900, ceiling_ms=900, window_key="0xelectron")
        assert layer.state is SemanticState.READY

        earned = provider.deadline_for("0xelectron", 250, ceiling_ms=900)
        assert 300 <= earned <= 900, earned
        assert provider.status()["measured_windows"] == 1
    finally:
        provider.close()


def test_a_window_that_truncates_settles_on_the_budget_that_worked():
    """Stop searching, but keep the budget that produced hints.

    The underlying enumeration is one atomic call, so a window like this is
    bimodal: at 250ms it returns nothing, at 500ms it returns a usable partial.
    Escalating to the ceiling every step wastes time; dropping back to the
    default pays most of the cost and gets nothing.
    """

    provider = _provider()
    control = ComputerControl(
        control_id="uia:0",
        name="Send",
        control_type="Button",
        rect=ComputerRect(0, 0, 10, 10),
    )
    try:
        layer = provider.collect(
            lambda: ((control,), {}, True),
            deadline_ms=500,
            ceiling_ms=900,
            window_key="0xheavy",
        )
        assert layer.state is SemanticState.PARTIAL

        assert provider.deadline_for("0xheavy", 250, ceiling_ms=900) == 500
    finally:
        provider.close()


def test_widening_one_windows_budget_does_not_open_the_global_breaker():
    provider = _provider()
    blocked = _Barrier()
    try:
        for _ in range(3):
            provider.collect(blocked, deadline_ms=60, ceiling_ms=900, window_key="0xchat")
            blocked.release.set()
            _drain(provider)
            blocked.release.clear()

        assert provider.status()["circuit_open"] is False
        assert provider.status()["consecutive_timeouts"] == 0
    finally:
        blocked.release.set()
        provider.close()


def test_a_truncated_walk_is_reported_as_partial_not_as_the_whole_window():
    provider = _provider()
    control = ComputerControl(
        control_id="uia:0",
        name="Send",
        control_type="Button",
        rect=ComputerRect(0, 0, 10, 10),
    )
    try:
        layer = provider.collect(lambda: ((control,), {}, True), deadline_ms=500)
        assert layer.state is SemanticState.PARTIAL
        assert layer.reason == "budget_exhausted"
        assert len(layer.controls) == 1
    finally:
        provider.close()


def test_operator_observation_gives_up_on_semantics_instead_of_waiting():
    """The operator-level guarantee, independent of which window is slow."""

    operator = object.__new__(PyWinAutoWindowsOperator)
    operator.max_controls = 300
    operator.semantics = _provider()
    entered = threading.Event()
    release = threading.Event()

    def never_returns(hwnd, *, budget_ms):
        entered.set()
        release.wait(timeout=30)
        return ((), {}, False)

    operator._walk_uia_controls = never_returns
    try:
        started = time.perf_counter()
        layer = operator._semantic_layer(0x10A9A, semantics=SEMANTICS_BEST_EFFORT, deadline_ms=150)
        elapsed = time.perf_counter() - started

        assert layer.state is SemanticState.UNAVAILABLE
        # Specifically the deadline, not a collector that failed fast: those are
        # very different bugs and only one of them is the one under test.
        assert layer.reason == "deadline"
        assert elapsed < OBSERVE_BUDGET_S
        assert entered.is_set()

        skipped = operator._semantic_layer(0x10A9A, semantics=SEMANTICS_SKIP, deadline_ms=150)
        assert skipped.state is SemanticState.SKIPPED
        assert skipped.reason == "not_requested"
    finally:
        release.set()
        operator.semantics.close()


class _RecordingOperator:
    """Operator that records how each caller asked for the semantic layer."""

    name = "recording"

    def __init__(self, *, block: threading.Event | None = None) -> None:
        self.requests: list[str] = []
        self.observe_count = 0
        self.executed: list[ComputerAction] = []
        self.closed = False
        self._block = block

    def status(self):
        return {"backend": self.name}

    def observe(self):
        self.requests.append("legacy")
        self._wait()
        return self._observation()

    def observe_layered(self, *, semantics=SEMANTICS_BEST_EFFORT, deadline_ms=0.0):
        self.requests.append(str(semantics))
        self._wait()
        return self._observation()

    def _wait(self) -> None:
        if self._block is not None:
            self._block.wait(timeout=30)

    def _observation(self):
        self.observe_count += 1
        frame = ComputerFrame(
            frame_id=f"frame-{self.observe_count}",
            origin_x=0,
            origin_y=0,
            width=1920,
            height=1080,
            window_id="0x10",
        )
        active = ComputerWindow(
            window_id="0x10",
            title="Chat",
            process_name="chat.exe",
            rect=ComputerRect(0, 0, 1920, 1080),
            foreground=True,
        )
        return ComputerObservation(
            observation_id=f"obs-{self.observe_count}",
            frame=frame,
            image_data=bytes([self.observe_count % 251]) * 64,
            active_window=active,
            windows=(active,),
            controls=(),
            semantics={"state": "unavailable", "reason": "deadline"},
        )

    def execute(self, action, observation):
        self.executed.append(action)
        return ComputerExecution(ok=True, message="done", action=action, native=False)

    def close(self):
        self.closed = True


def test_post_action_observation_never_asks_for_the_expensive_layer():
    """Verification reads a hash and a window id. It used to pay for a tree walk."""

    operator = _RecordingOperator()
    store = ComputerSessionStore(operator, settle_delay=0)
    store.observe("session-a")
    operator.requests.clear()

    store.execute(
        "session-a",
        store.latest("session-a").state_revision,
        ComputerAction(type=ComputerActionType.CLICK, point=ComputerPoint(0.5, 0.5)),
    )

    assert operator.requests == [SEMANTICS_BEST_EFFORT]
    assert SEMANTICS_REQUIRED not in operator.requests


def test_a_slow_observation_does_not_freeze_the_desktop_action_path():
    """The freeze, not the slowness, is what made this look like a hang.

    One lock used to cover both the observation I/O and the store's bookkeeping,
    so a slow window blocked status, cancellation and every other caller for as
    long as the window took. Observing must hold no lock that input needs.
    """

    operator = _RecordingOperator()
    store = ComputerSessionStore(operator, settle_delay=0)
    store.observe("session-a")

    gate = threading.Event()
    operator._block = gate
    observed = threading.Event()

    def observe_later():
        store.observe("session-a")
        observed.set()

    worker = threading.Thread(target=observe_later, daemon=True)
    worker.start()
    try:
        # Give the observation time to be well inside the operator call.
        time.sleep(0.1)
        assert not observed.is_set(), "fixture error: the observation was not slow"

        # The behavioural property, independent of how the locks are named: other
        # callers keep being served while one observation is stuck in a window.
        answered = threading.Event()

        def read_state():
            store.latest("session-a")
            answered.set()

        reader = threading.Thread(target=read_state, daemon=True)
        reader.start()
        assert answered.wait(timeout=2.0), "a slow observation must not block store reads"
        reader.join(timeout=2)

        acquired = store._input_lock.acquire(blocking=False)
        assert acquired, "a slow observation must not hold the input lock"
        store._input_lock.release()
    finally:
        gate.set()
        worker.join(timeout=10)
    assert observed.is_set()


def test_a_degraded_layer_is_greppable_in_the_event_stream():
    """The question after a bad run is "was Loom waiting on something?"."""

    emitted: list[tuple[str, dict]] = []

    class _Diagnostics:
        raw = False

        def operation_id(self):
            return "op"

        def emit(self, event, **data):
            emitted.append((event, data))

        def save_screenshot(self, *args, **kwargs):
            return ""

    operator = _RecordingOperator()
    store = ComputerSessionStore(operator, settle_delay=0, diagnostics=_Diagnostics())

    store.observe("session-a")

    degraded = [data for event, data in emitted if event == "semantics.degraded"]
    assert degraded, [event for event, _ in emitted]
    assert degraded[0]["state"] == "unavailable"
    assert degraded[0]["reason"] == "deadline"


def test_a_healthy_layer_stays_quiet():
    emitted: list[str] = []

    class _Diagnostics:
        raw = False

        def operation_id(self):
            return "op"

        def emit(self, event, **data):
            emitted.append(event)

        def save_screenshot(self, *args, **kwargs):
            return ""

    class _HealthyOperator(_RecordingOperator):
        def _observation(self):
            observation = super()._observation()
            return replace(observation, semantics={"state": "ready", "reason": ""})

    store = ComputerSessionStore(_HealthyOperator(), settle_delay=0, diagnostics=_Diagnostics())
    store.observe("session-a")

    assert "semantics.degraded" not in emitted


def test_the_original_incident_cannot_recur():
    """Replays the shape of run ccd23506 end to end.

    There, one observe cost 108.0s and the action that followed cost 132.5s,
    both of them inside UI Automation, for no progress at all. Here the semantic
    layer never answers; the same observe-then-act sequence must still finish in
    about a second, because nothing on the loop's critical path waits on it.
    """

    provider = _provider()
    wedged = _Barrier()

    class _IncidentOperator(_RecordingOperator):
        def observe_layered(self, *, semantics=SEMANTICS_BEST_EFFORT, deadline_ms=0.0):
            self.requests.append(str(semantics))
            layer = provider.collect(
                wedged,
                deadline_ms=250,
                ceiling_ms=250,
                window_key="0x10a9a",
            )
            return replace(
                self._observation(),
                controls=layer.controls,
                semantics=layer.to_dict(),
            )

    operator = _IncidentOperator()
    store = ComputerSessionStore(operator, settle_delay=0)
    try:
        started = time.perf_counter()
        snapshot = store.observe("session-a")
        outcome = store.execute(
            "session-a",
            snapshot.state_revision,
            ComputerAction(type=ComputerActionType.CLICK, point=ComputerPoint(0.5, 0.5)),
        )
        elapsed = time.perf_counter() - started

        assert elapsed < 2.0, f"observe+act took {elapsed:.1f}s"
        # The step still happened, and still carries a screenshot and a verdict.
        assert outcome.after is not None
        assert outcome.after.observation.image_data
        assert outcome.after.observation.semantics["state"] == "unavailable"
        assert operator.executed, "the action itself must not be skipped"
    finally:
        wedged.release.set()
        provider.close()


def test_legacy_operators_without_layering_still_work():
    """Embedders with a plain observe() keep working; they just get one layer."""

    class OldOperator(_RecordingOperator):
        """An operator from before layering: it only knows how to observe."""

        observe_layered = None

    operator = OldOperator()
    store = ComputerSessionStore(operator, settle_delay=0)

    snapshot = store.observe("session-a")

    assert snapshot.observation.observation_id == "obs-1"
    assert operator.requests == ["legacy"]
