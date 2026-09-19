"""Window switching is an intent with a success check, not a key injection.

From the same trace (run ccd23506, 2026-09-18): the model chose
``hotkey alt+tab`` to bring WeChat forward. Windows refuses foreground changes
driven by synthetic input, so the keys went in, nothing moved, and the step
still reported ``execution_ok: true``. The target window had been in the
observation's window list the whole time, reachable through ``switch_window``,
which activates natively and verifies the result.

The action vocabulary offered both at equal status, so this was a predictable
choice rather than a model mistake.
"""

from __future__ import annotations

from app.agent_runtime.computer_runtime import ComputerStateSnapshot
from app.agent_runtime.computer_single_loop_runtime import (
    SingleLoopComputerRuntime,
    _model_observation_text,
    _window_switch_hotkey,
)
from app.agent_runtime.computer_types import (
    ComputerAction,
    ComputerActionType,
    ComputerExecution,
    ComputerFrame,
    ComputerObservation,
    ComputerRect,
    ComputerWindow,
)
from app.agent_runtime.contracts import PermissionMode
from app.agent_runtime.sandbox import SandboxManager, SandboxPolicy
from app.agent_runtime.storage import FileAgentSessionStore
from app.agent_runtime.tools import ToolContext
from app.agent_runtime.workspace_tools import loom_default_tools
from app.ai import AGENT_FAST_ROLE, ModelResponse


class _Platform:
    def execute_chat(self, profile_id, request):
        return ModelResponse(text="done")


class _TwoWindowOperator:
    """Loom in the foreground, the target application behind it."""

    name = "two-window"

    def __init__(self, *, self_window: bool = True) -> None:
        self.observe_count = 0
        self.executed: list[ComputerAction] = []
        self.closed = False
        self.self_window = bool(self_window)

    def status(self):
        return {"backend": self.name}

    def observe(self):
        return self.observe_layered()

    def observe_layered(self, *, semantics="best_effort", deadline_ms=0.0):
        self.observe_count += 1
        frame = ComputerFrame(
            frame_id=f"frame-{self.observe_count}",
            origin_x=200,
            origin_y=74,
            width=2160,
            height=1380,
            window_id="0x10a9a",
        )
        loom = ComputerWindow(
            window_id="0x10a9a",
            title="Loom",
            process_name="electron.exe",
            rect=ComputerRect(200, 74, 2360, 1454),
            foreground=True,
        )
        target = ComputerWindow(
            window_id="0x10b80",
            title="WeChat",
            process_name="Weixin.exe",
            rect=ComputerRect(342, 342, 2262, 1459),
            foreground=False,
        )
        return ComputerObservation(
            observation_id=f"obs-{self.observe_count}",
            frame=frame,
            image_data=bytes([self.observe_count % 251]) * 64,
            active_window=loom,
            windows=(loom, target),
            controls=(),
            semantics={"state": "skipped", "reason": "loom_own_window"},
            metadata={"self_window": self.self_window},
        )

    def execute(self, action, observation):
        self.executed.append(action)
        return ComputerExecution(ok=True, message="injected", action=action, native=False)

    def close(self):
        self.closed = True


def _runtime(tmp_path):
    operator = _TwoWindowOperator()
    runtime = SingleLoopComputerRuntime(
        platform=_Platform(),
        store=FileAgentSessionStore(tmp_path / "state"),
        tools=loom_default_tools(),
        sandbox_manager=SandboxManager(policy=SandboxPolicy.OFF),
        web_search_provider=None,
        auto_configure_web_search=False,
        auto_configure_browser=False,
        computer_operator=operator,
        auto_configure_computer=False,
        computer_settle_delay=0,
    )
    workspace = tmp_path / "project"
    workspace.mkdir()
    session = runtime.create_session(
        AGENT_FAST_ROLE.role_id,
        workspace_dir=workspace,
        permission_mode=PermissionMode.FULL_ACCESS,
    )
    context = ToolContext(
        session_id=session.session_id,
        turn_id="turn-1",
        workspace=workspace,
        permission_mode=session.permission_mode.value,
    )
    runtime.computer_sessions.observe(session.session_id)
    return runtime, operator, context


def test_synthetic_alt_tab_is_refused_before_any_input_is_injected(tmp_path):
    runtime, operator, context = _runtime(tmp_path)
    try:
        result = runtime._handle_single_action(
            context,
            {"action": {"type": "hotkey", "keys": ["alt", "tab"]}},
        )

        assert result.ok is False
        assert operator.executed == [], "refused actions must not reach the desktop"
        assert result.data["effect_reason"] == "synthetic_window_switch_hotkey_refused"
        # The refusal has to be actionable: the model needs the id it should have
        # used, not just a complaint about the one it chose.
        assert "switch_window" in result.content
        assert "0x10b80" in result.content
    finally:
        runtime.close()


def test_switch_window_still_executes_normally(tmp_path):
    runtime, operator, context = _runtime(tmp_path)
    try:
        result = runtime._handle_single_action(
            context,
            {"action": {"type": "switch_window", "window_id": "0x10b80"}},
        )

        assert [action.type for action in operator.executed] == [ComputerActionType.SWITCH_WINDOW]
        # The fixture's foreground window never actually moves, so the runtime is
        # expected to call this out rather than trust the injection's own report.
        assert result.ok is False
        assert result.data["verification"]["target_confirmed"] is False
    finally:
        runtime.close()


def test_window_switch_hotkey_matcher_covers_the_usual_variants():
    def hotkey(*keys):
        return ComputerAction(type=ComputerActionType.HOTKEY, keys=keys)

    assert _window_switch_hotkey(hotkey("alt", "tab")) is True
    assert _window_switch_hotkey(hotkey("Alt", "Tab")) is True
    assert _window_switch_hotkey(hotkey("alt", "shift", "tab")) is True
    assert _window_switch_hotkey(hotkey("win", "tab")) is True
    # Ordinary shortcuts must keep working; this is a targeted refusal, not a
    # general distrust of hotkeys.
    assert _window_switch_hotkey(hotkey("ctrl", "c")) is False
    assert _window_switch_hotkey(hotkey("alt", "f4")) is False
    assert _window_switch_hotkey(hotkey("ctrl", "alt", "tab")) is False


def test_missing_hints_are_explained_rather_than_implied_to_be_emptiness():
    operator = _TwoWindowOperator(self_window=False)
    snapshot = ComputerStateSnapshot(1, operator.observe())

    text = _model_observation_text(snapshot)

    assert "latency budget" in text
    assert "says nothing about the window's contents" in text
    # The window list is what makes the advice above actionable.
    assert "0x10b80" in text


def test_the_model_is_told_when_it_is_looking_at_loom_itself():
    """Otherwise it must infer, from a screenshot of a chat UI, that the chat UI
    is the assistant and the task lives in one of the background windows."""

    snapshot = ComputerStateSnapshot(1, _TwoWindowOperator(self_window=True).observe())

    text = _model_observation_text(snapshot)

    assert "Loom's own interface" in text
    assert "switch_window" in text
    assert "0x10b80" in text


def test_host_pid_detection_is_off_rather_than_wrong_when_unconfigured(monkeypatch):
    """An older desktop build that does not pass its pid must keep working."""

    from app.agent_runtime.computer_windows import _host_process_ids

    monkeypatch.delenv("LOOM_DESKTOP_HOST_PID", raising=False)
    assert _host_process_ids() == frozenset()

    monkeypatch.setenv("LOOM_DESKTOP_HOST_PID", "4242")
    assert _host_process_ids() == frozenset({4242})

    monkeypatch.setenv("LOOM_DESKTOP_HOST_PID", "4242, 99 ;7")
    assert _host_process_ids() == frozenset({4242, 99, 7})

    monkeypatch.setenv("LOOM_DESKTOP_HOST_PID", "not-a-pid")
    assert _host_process_ids() == frozenset()


def test_ordinary_windows_are_not_labelled_as_loom():
    snapshot = ComputerStateSnapshot(1, _TwoWindowOperator(self_window=False).observe())

    assert "Loom's own interface" not in _model_observation_text(snapshot)
