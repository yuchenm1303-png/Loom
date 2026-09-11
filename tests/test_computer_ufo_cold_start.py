"""Regression cover for the UFO cold-start failures that made Computer Use unusable.

Each test here maps to a failure observed while driving the real sidecar on Windows:
the task hung during UFO's import, the supervisor killed healthy slow tasks, a
failed run was reported as a success, and reasoning models could not drive UFO at
all because their responses never parsed.
"""

from __future__ import annotations

import time
from pathlib import Path

from app.agent_runtime import ufo_sidecar, ufo_sidecar_supervisor
from app.agent_runtime.ufo_sidecar import (
    _outcome_summary,
    _sanitize_llm_response,
    _strip_reasoning_wrapper,
)


def test_heavy_ufo_imports_are_preloaded_before_the_event_loop():
    """The Session import must not run inside an asyncio callback.

    Importing UFO's numpy/faiss/langchain tree from the event loop thread
    deadlocked the Windows DLL loader against asyncio's stdin worker, so every
    task stalled forever right after ufo.imports.started.
    """

    # main() performs bootstrap + preload synchronously, then hands the already
    # imported entrypoints to the loop.
    import inspect

    main_src = inspect.getsource(ufo_sidecar.main)
    preload_at = main_src.find("_preload_ufo_modules")
    loop_at = main_src.find("asyncio.run")
    assert preload_at != -1, "startup must preload UFO modules"
    assert loop_at != -1
    assert preload_at < loop_at, "preload must happen before the event loop starts"


def test_run_task_uses_preloaded_modules_instead_of_importing():
    import inspect

    src = inspect.getsource(ufo_sidecar._run_task)
    assert "_UFO_MODULES[" in src
    body_after_stage = src.split('controller.stage("ufo.imports.started")', 1)[1]
    # The lazy import survives only as an explicitly guarded fallback.
    assert "if not preloaded:" in body_after_stage


def test_protocol_stdout_is_not_claimed_on_import():
    """Rebinding fd 1 is a process-wide effect and must stay inside main()."""

    import inspect

    assert "_claim_protocol_stdout()" in inspect.getsource(ufo_sidecar.main)
    assert ufo_sidecar._PROTOCOL_STDOUT is not None


def test_reasoning_wrapper_is_stripped_so_ufo_can_parse_json():
    assert _strip_reasoning_wrapper('<think>why</think>\n\n{"ok": true}') == '{"ok": true}'
    assert _strip_reasoning_wrapper('<thinking>x</thinking>[1,2]') == "[1,2]"
    assert _strip_reasoning_wrapper('<REASONING>r</REASONING>{"z":0}') == '{"z":0}'
    # Multiple blocks, including a trailing one.
    assert _strip_reasoning_wrapper('<think>a</think>{"a":1}<think>b</think>') == '{"a":1}'
    # An unterminated block must not shadow a usable payload.
    assert _strip_reasoning_wrapper('<think>cut off {"x":1}') == '{"x":1}'


def test_plain_responses_are_left_untouched():
    for value in ('{"ok": true}', "no json at all", ""):
        assert _strip_reasoning_wrapper(value) == (value.strip() or value)


def test_sanitize_llm_response_handles_the_list_shape_ufo_returns():
    assert _sanitize_llm_response(['<think>t</think>{"a":1}', '{"b":2}']) == [
        '{"a":1}',
        '{"b":2}',
    ]
    assert _sanitize_llm_response(None) is None


def test_structured_output_probe_failure_maps_to_ufo_s_own_fallback():
    """Providers that accept json_schema and ignore it must degrade, not crash."""

    import inspect

    src = inspect.getsource(ufo_sidecar._install_structured_output_probe_fallback)
    assert "ValidationError" in src
    assert "BadRequestError" in src
    # UFO only downgrades when it sees this exact phrase in the error message.
    assert "'response_format' of type 'json_schema' is not supported" in src


def test_outcome_summaries_distinguish_the_ways_a_run_can_end():
    """UFO reports a clean unwind for runs that achieved nothing.

    Success is only claimed when the agent itself declared the task done, and the
    three failure shapes have to stay distinguishable: a blind retry is safe after
    "no action", but not after partial progress.
    """

    assert _outcome_summary("") == "UFO desktop task completed."
    assert "error state" in _outcome_summary("session_error")
    assert "ran out of steps" in _outcome_summary("step_budget_exhausted")
    assert "without performing any desktop action" in _outcome_summary("no_effective_action")
    assert "could not complete" in _outcome_summary("agent_reported_failure")

    partial = _outcome_summary("unconfirmed_partial_progress")
    assert "unconfirmed" in partial
    assert "before retrying" in partial


def test_focusing_a_window_does_not_count_as_doing_the_work():
    """Otherwise a task that only oriented itself would report partial progress."""

    from app.agent_runtime.ufo_sidecar import _NON_MUTATING_ACTIONS

    assert "select_application_window" in _NON_MUTATING_ACTIONS
    assert "list_tools" in _NON_MUTATING_ACTIONS
    assert "set_edit_text" not in _NON_MUTATING_ACTIONS
    assert "click_input" not in _NON_MUTATING_ACTIONS


def test_supervisor_watchdog_keys_on_silence_not_on_reaching_a_stage():
    """A slow first model call is not a hang.

    The previous watchdog required a dispatcher/window/action event within the
    timeout, so it killed tasks whose first vision-model call simply took longer
    than 30s.
    """

    import inspect

    src = inspect.getsource(ufo_sidecar_supervisor.watchdog_loop)
    assert "last_event_at" in src
    assert "first_progress" not in src


def test_supervisor_treats_heartbeats_as_liveness():
    now = time.monotonic()
    ufo_sidecar_supervisor.active = {
        "request_id": "r",
        "task_id": "t",
        "started_at": now - 600,
        "finished": False,
        "last_event_kind": "task.heartbeat",
        "last_event_sequence": 9,
        "last_event_at": now,
        "safe_request": {},
    }
    try:
        current = ufo_sidecar_supervisor.active
        silent_for = time.monotonic() - float(current["last_event_at"])
        # Long-running but audibly alive: far past started_at, but not silent.
        assert silent_for < ufo_sidecar_supervisor._timeout_seconds()
    finally:
        ufo_sidecar_supervisor.active = None


def test_supervisor_stall_timeout_has_a_realistic_floor(monkeypatch):
    monkeypatch.delenv("LOOM_UFO_STALL_TIMEOUT", raising=False)
    monkeypatch.delenv("LOOM_UFO_FIRST_STEP_TIMEOUT", raising=False)
    assert ufo_sidecar_supervisor._timeout_seconds() >= 60.0


def test_protocol_and_stderr_use_separate_locks():
    """A backed-up stderr pipe must not be able to block protocol writes."""

    assert ufo_sidecar_supervisor.WRITE_LOCK is not ufo_sidecar_supervisor.STDERR_LOCK
    import inspect

    assert "STDERR_LOCK" in inspect.getsource(ufo_sidecar_supervisor.forward_stderr)
    assert "WRITE_LOCK" in inspect.getsource(ufo_sidecar_supervisor.emit)


class _FakeProcess:
    def __init__(self, lines):
        self.stdout = iter(lines)
        self.stderr = None


def test_supervisor_drops_stray_non_json_output_instead_of_failing_the_task(monkeypatch):
    emitted: list[dict] = []
    monkeypatch.setattr(ufo_sidecar_supervisor, "emit", emitted.append)
    monkeypatch.setattr(ufo_sidecar_supervisor, "active", None)

    ufo_sidecar_supervisor.forward_stdout(
        _FakeProcess(
            [
                "UFO printed this to stdout\n",
                '{"type":"event","request_id":"r","kind":"action.started","data":{}}\n',
            ]
        )
    )

    assert [item["type"] for item in emitted] == ["event"]


def test_driver_drops_stray_non_json_output(monkeypatch):
    from app.agent_runtime.computer_ufo_driver import UfoWindowsDriver

    driver = UfoWindowsDriver.__new__(UfoWindowsDriver)
    import queue

    driver._messages = queue.Queue()
    driver._stdout_loop(
        _FakeProcess(['not json at all\n', '{"type":"ready"}\n'])
    )

    delivered = []
    while not driver._messages.empty():
        delivered.append(driver._messages.get_nowait())
    assert delivered == [{"type": "ready"}]


def test_a_model_that_cannot_see_is_refused_before_any_desktop_task():
    """Bridging a text-only model into UFO produces unreadable downstream errors.

    The observed failure was a non-vision model being handed the desktop and
    flailing, so the capability check has to drop the model and say why.
    """

    from dataclasses import replace as dc_replace

    from app.agent_runtime.computer_driver_runtime import ComputerDriverRuntime
    from app.agent_runtime.computer_ufo_driver import UfoDriverConfig, UfoWindowsDriver

    class _Platform:
        _loom_model_connection = {
            "provider": "openai-compatible",
            "model": "text-only-model",
            "api_key": "k",
            "base_url": "https://example.invalid/v1",
            "vision": False,
        }

    driver = UfoWindowsDriver.__new__(UfoWindowsDriver)
    driver.config = dc_replace(
        UfoDriverConfig(
            install_root=Path("."),
            source_root=Path("."),
            python=Path("."),
            sidecar=Path("."),
            api_type="openai",
            api_base="https://example.invalid/v1",
            api_key="k",
            api_model="previous-vision-model",
        )
    )
    driver.close = lambda: None

    runtime = object.__new__(ComputerDriverRuntime)
    runtime.computer_driver = driver
    runtime.computer_model_vision = None
    runtime.platform = _Platform()

    ComputerDriverRuntime._sync_driver_model_from_platform(runtime)

    assert runtime.computer_model_vision is False
    assert driver.config.api_model == "", "a blind model must not stay configured"


def test_an_undeclared_vision_capability_is_recorded_but_not_blocking():
    """Most profiles never set the flag; refusing them all would break the feature."""

    from dataclasses import replace as dc_replace

    from app.agent_runtime.computer_driver_runtime import ComputerDriverRuntime
    from app.agent_runtime.computer_ufo_driver import UfoDriverConfig, UfoWindowsDriver

    class _Platform:
        _loom_model_connection = {
            "provider": "openai-compatible",
            "model": "some-model",
            "api_key": "k",
            "base_url": "https://example.invalid/v1",
        }

    driver = UfoWindowsDriver.__new__(UfoWindowsDriver)
    driver.config = dc_replace(
        UfoDriverConfig(
            install_root=Path("."),
            source_root=Path("."),
            python=Path("."),
            sidecar=Path("."),
            api_type="openai",
            api_base="",
            api_key="",
            api_model="",
        )
    )
    driver.close = lambda: None

    runtime = object.__new__(ComputerDriverRuntime)
    runtime.computer_driver = driver
    runtime.computer_model_vision = None
    runtime.platform = _Platform()

    ComputerDriverRuntime._sync_driver_model_from_platform(runtime)

    assert runtime.computer_model_vision is None
    assert driver.config.api_model == "some-model"


def test_session_finish_flag_is_not_used_as_the_success_signal():
    """UFO only sets session._finish for interactive and plan-file sessions.

    Loom drives a single non-interactive round, so _finish is always False here.
    Keying success on it marked every completed task as a failure.
    """

    import inspect

    src = inspect.getsource(ufo_sidecar._run_task)
    code = "\n".join(
        line for line in src.splitlines() if not line.lstrip().startswith("#")
    )
    assert "_round_state(session)" in code
    assert "_finish" not in code


def test_round_state_reads_the_agents_own_verdict():
    class _State:
        def name(self):
            return "FINISH"

    class _Round:
        state = _State()

    class _Session:
        current_round = _Round()

    assert ufo_sidecar._round_state(_Session()) == "FINISH"
    assert ufo_sidecar._round_state(object()) == ""
