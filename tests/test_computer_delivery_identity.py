"""A control action must land once, on the control the model saw.

Three rules borrowed from agent-desktop's Windows adapter (2026-11-01):

* A native UI Automation write that did not answer in time may still land. A
  button that opens a modal dialog blocks Invoke until the dialog closes, so
  the old "native failed -> click the same spot physically" fallback pressed
  it twice. Only a write that provably never reached the target may fall back.
* A control id is a position in one walk. Before acting, the stored wrapper is
  re-read and compared on stable identity (type, automation id, label); a
  changed or vanished control refuses the action before any input.
* A window handle can be recycled to another program; switching to a stale
  window id whose owning process changed is refused.
"""

from __future__ import annotations

import sys
import threading
from collections import OrderedDict
from types import SimpleNamespace

import pytest

import app.agent_runtime.computer_windows as computer_windows
from app.agent_runtime.computer_semantics import SemanticLayerProvider
from app.agent_runtime.computer_types import (
    ComputerAction,
    ComputerActionType,
    ComputerControl,
    ComputerDelivery,
    ComputerExecution,
    ComputerFrame,
    ComputerPoint,
    ComputerRect,
    ComputerWindow,
)
from app.agent_runtime.computer_windows import PyWinAutoWindowsOperator, _ControlHandle


class ComError(Exception):
    def __init__(self, hresult: int) -> None:
        super().__init__(hex(hresult))
        self.hresult = hresult


class Wrapper:
    def __init__(self, *, control_type="Button", name="Save", automation_id="", rect=(10, 10, 50, 30)) -> None:
        self.element_info = SimpleNamespace(control_type=control_type, name=name, automation_id=automation_id)
        self.rect = rect
        self.invoked = 0
        self.texts: list[str] = []

    def rectangle(self):
        left, top, right, bottom = self.rect
        return SimpleNamespace(left=left, top=top, right=right, bottom=bottom)


class InvokeWrapper(Wrapper):
    def __init__(self, *, block: threading.Event | None = None, error: BaseException | None = None, **kw) -> None:
        super().__init__(**kw)
        self.block = block
        self.error = error

    def invoke(self) -> None:
        self.invoked += 1
        if self.block is not None:
            self.block.wait(5)
        if self.error is not None:
            raise self.error


class EditWrapper(Wrapper):
    def __init__(self, *, block: threading.Event | None = None, **kw) -> None:
        super().__init__(control_type="Edit", **kw)
        self.block = block

    def set_edit_text(self, text: str) -> None:
        self.texts.append(text)
        if self.block is not None:
            self.block.wait(5)


@pytest.fixture
def stubs(monkeypatch):
    sent: list[tuple] = []
    monkeypatch.setitem(sys.modules, "pyautogui", SimpleNamespace(press=lambda key: sent.append(("press", key))))
    monkeypatch.setitem(sys.modules, "win32api", SimpleNamespace(SetCursorPos=lambda pos: sent.append(("move", pos))))
    monkeypatch.setitem(sys.modules, "win32con", SimpleNamespace())
    return sent


@pytest.fixture
def provider(monkeypatch):
    monkeypatch.setattr(computer_windows, "NATIVE_INVOKE_DEADLINE_MS", 80)
    monkeypatch.setattr(computer_windows, "CONTROL_IDENTITY_DEADLINE_MS", 80)
    instance = SemanticLayerProvider(cooldown_s=0)
    yield instance
    instance.close()


def _operator(provider=None):
    operator = object.__new__(PyWinAutoWindowsOperator)
    operator._lock = threading.RLock()
    operator._control_maps = {}
    operator._window_generations = OrderedDict()
    operator.semantics = provider
    operator.clicks = []
    operator.typed = []
    operator._click_point = lambda frame, point, double=False, right=False: operator.clicks.append(point)
    operator._send_unicode_text = operator.typed.append
    operator._read_focused_value = lambda frame: None
    return operator


def _observation():
    return SimpleNamespace(
        observation_id="obs",
        frame=ComputerFrame("f", 0, 0, 100, 100, window_id="0x10"),
        active_window=None,
        controls=(),
    )


def _register(operator, wrapper, control_id="uia:3"):
    info = wrapper.element_info
    identity = ComputerControl(
        control_id, info.name, info.control_type, ComputerRect(*wrapper.rect), automation_id=info.automation_id
    )
    operator._control_maps["obs"] = {control_id: _ControlHandle(wrapper=wrapper, identity=identity)}
    return control_id


def _click(control_id="uia:3", point=None):
    return ComputerAction(ComputerActionType.CLICK, control_id=control_id, point=point)


# -- delivery ---------------------------------------------------------------


def test_invoke_that_outlives_its_deadline_is_uncertain_and_never_clicked_again(provider, stubs):
    release = threading.Event()
    wrapper = InvokeWrapper(block=release)
    operator = _operator(provider)
    try:
        result = operator.execute(_click(_register(operator, wrapper)), _observation())
    finally:
        release.set()

    assert result.ok is False
    assert result.delivery == ComputerDelivery.UNCERTAIN
    assert wrapper.invoked == 1
    assert operator.clicks == []
    assert stubs == []
    assert "do not repeat" in result.message


def test_invoke_that_never_reached_the_target_falls_back_to_one_physical_click(stubs):
    wrapper = Wrapper()  # no Invoke pattern
    operator = _operator()

    result = operator.execute(_click(_register(operator, wrapper)), _observation())

    assert result.ok is True
    assert result.delivery == ComputerDelivery.DELIVERED
    assert result.fallback_used is True
    assert len(operator.clicks) == 1


@pytest.mark.parametrize(
    "hresult, expected",
    [
        (0x80040201, ComputerDelivery.NOT_DELIVERED),  # UIA_E_ELEMENTNOTAVAILABLE
        (-2147220992, ComputerDelivery.NOT_DELIVERED),  # UIA_E_ELEMENTNOTENABLED, signed
        (0x80131505, ComputerDelivery.UNCERTAIN),  # UIA_E_TIMEOUT
        (0x80004005, ComputerDelivery.UNCERTAIN),  # E_FAIL
    ],
)
def test_invoke_errors_are_split_by_whether_the_provider_saw_them(stubs, hresult, expected):
    wrapper = InvokeWrapper(error=ComError(hresult))
    operator = _operator()

    result = operator.execute(_click(_register(operator, wrapper)), _observation())

    if expected == ComputerDelivery.NOT_DELIVERED:
        assert result.ok is True and len(operator.clicks) == 1
    else:
        assert result.ok is False and result.delivery == ComputerDelivery.UNCERTAIN
        assert operator.clicks == []


def test_busy_worker_sends_nothing_so_the_physical_fallback_is_safe(stubs):
    class Busy:
        def run(self, call, *, deadline_ms, **_):
            # The identity read answers; the invoke is refused before queueing.
            if getattr(call, "__name__", "") == "read":
                return SimpleNamespace(ok=True, value=call(), reason="")
            return SimpleNamespace(ok=False, value=None, reason="busy")

    wrapper = InvokeWrapper()
    operator = _operator(Busy())
    operator._wrapper_center = lambda wrapper, frame: ComputerPoint(0.3, 0.2)

    result = operator.execute(_click(_register(operator, wrapper)), _observation())

    assert result.ok is True
    assert wrapper.invoked == 0
    assert operator.clicks == [ComputerPoint(0.3, 0.2)]


def test_set_text_that_outlives_its_deadline_is_not_retyped_with_sendinput(provider, stubs):
    release = threading.Event()
    wrapper = EditWrapper(block=release, name="File name")
    operator = _operator(provider)
    action = ComputerAction(ComputerActionType.TYPE, control_id=_register(operator, wrapper), text="report.txt")
    try:
        result = operator.execute(action, _observation())
    finally:
        release.set()

    assert result.ok is False
    assert result.delivery == ComputerDelivery.UNCERTAIN
    assert wrapper.texts == ["report.txt"]
    assert operator.typed == []
    assert operator.clicks == []


def test_delivery_is_reported_to_the_model():
    action = ComputerAction(ComputerActionType.WAIT)
    assert ComputerExecution(False, "x", action, delivery="uncertain").to_safe_dict()["delivery"] == "uncertain"
    assert "delivery" not in ComputerExecution(True, "x", action).to_safe_dict()


# -- identity ---------------------------------------------------------------


@pytest.mark.parametrize(
    "change, reason",
    [
        (lambda w: setattr(w.element_info, "name", "Delete"), "label differs"),
        (lambda w: setattr(w.element_info, "control_type", "CheckBox"), "type Button -> CheckBox"),
        (lambda w: setattr(w.element_info, "automation_id", "other"), "automation id differs"),
    ],
)
def test_a_control_that_changed_since_the_observation_is_refused_before_input(stubs, change, reason):
    wrapper = InvokeWrapper(automation_id="save")
    operator = _operator()
    control_id = _register(operator, wrapper)
    change(wrapper)

    with pytest.raises(RuntimeError, match=reason):
        operator.execute(_click(control_id), _observation())

    assert wrapper.invoked == 0
    assert operator.clicks == []


def test_a_vanished_control_is_refused_before_input(stubs):
    class Gone(InvokeWrapper):
        @property
        def element_info(self):
            raise ComError(0x80040201)

        @element_info.setter
        def element_info(self, value):
            self._info = value

    wrapper = Gone()
    operator = _operator()
    identity = ComputerControl("uia:3", "Save", "Button", ComputerRect(10, 10, 50, 30))
    operator._control_maps["obs"] = {"uia:3": _ControlHandle(wrapper=wrapper, identity=identity)}

    with pytest.raises(RuntimeError, match="no longer available"):
        operator.execute(_click("uia:3"), _observation())
    assert wrapper.invoked == 0


def test_an_edit_whose_name_carries_its_value_is_still_the_same_control(stubs):
    wrapper = EditWrapper(name="draft")
    operator = _operator()
    action = ComputerAction(ComputerActionType.TYPE, control_id=_register(operator, wrapper), text="final")
    wrapper.element_info.name = "draft v2"

    result = operator.execute(action, _observation())

    assert result.ok is True and result.native is True
    assert wrapper.texts == ["final"]


def test_an_unlabelled_control_is_identified_by_where_it_is(stubs):
    wrapper = InvokeWrapper(name="")
    operator = _operator()
    control_id = _register(operator, wrapper)

    assert operator.execute(_click(control_id), _observation()).ok is True
    wrapper.rect = (10, 60, 50, 80)
    with pytest.raises(RuntimeError, match="unlabelled control moved"):
        operator.execute(_click(control_id), _observation())
    assert wrapper.invoked == 1


def test_identity_that_cannot_be_read_in_time_uses_the_screenshot_point_not_the_wrapper(stubs):
    class Slow:
        def run(self, call, *, deadline_ms, **_):
            return SimpleNamespace(ok=False, value=None, reason="deadline")

    wrapper = InvokeWrapper()
    operator = _operator(Slow())
    control_id = _register(operator, wrapper)

    result = operator.execute(_click(control_id, point=ComputerPoint(0.5, 0.5)), _observation())
    assert result.ok is True
    assert wrapper.invoked == 0
    assert operator.clicks == [ComputerPoint(0.5, 0.5)]

    with pytest.raises(RuntimeError, match="could not confirm"):
        operator.execute(_click(control_id), _observation())


# -- walk -------------------------------------------------------------------


def _walk(monkeypatch, window_class, wrappers):
    class Walked(Wrapper):
        def is_visible(self):
            return True

        def is_enabled(self):
            return True

    items = [Walked(control_type=kind, name=name, automation_id=aid) for kind, name, aid in wrappers]
    desktop = SimpleNamespace(window=lambda **kw: SimpleNamespace(descendants=lambda: items))
    monkeypatch.setitem(sys.modules, "pywinauto", SimpleNamespace(Desktop=lambda **kw: desktop))
    monkeypatch.setitem(sys.modules, "win32gui", SimpleNamespace(GetClassName=lambda hwnd: window_class))
    operator = _operator()
    operator.max_controls = 20
    controls, mapping, _ = operator._walk_uia_controls(1, budget_ms=10000)
    return [(c.control_type, c.name) for c in controls], mapping


WALKED = [
    ("Pane", "", ""),
    ("Pane", "Sidebar", ""),
    ("ToolBar", "", "nav"),
    ("Group", "", ""),
    ("Custom", "", ""),
    ("Group", "Filters", ""),
    ("Button", "", ""),
]


def test_chromium_skips_only_anonymous_wrappers(monkeypatch):
    kept, mapping = _walk(monkeypatch, "Chrome_WidgetWin_1", WALKED)
    assert kept == [("Pane", "Sidebar"), ("ToolBar", ""), ("Group", "Filters"), ("Button", "")]
    assert all(isinstance(handle, _ControlHandle) for handle in mapping.values())


def test_native_windows_keep_anonymous_groups_which_may_be_custom_drawn_controls(monkeypatch):
    kept, _ = _walk(monkeypatch, "Notepad", WALKED)
    assert kept == [
        ("Pane", "Sidebar"),
        ("ToolBar", ""),
        ("Group", ""),
        ("Custom", ""),
        ("Group", "Filters"),
        ("Button", ""),
    ]


# -- window identity --------------------------------------------------------


def test_switching_to_a_recycled_window_handle_is_refused(monkeypatch):
    generations = {0x20: "100:1"}
    monkeypatch.setattr(computer_windows, "_process_generation", lambda hwnd: generations.get(hwnd))
    operator = _operator()
    switched = []
    operator._switch_window = lambda action: switched.append(action.window_id) or "switched"
    observation = _observation()
    operator._remember_window_generations("obs", (ComputerWindow("0x20", "Editor"),))
    action = ComputerAction(ComputerActionType.SWITCH_WINDOW, window_id="0X20")

    assert operator.execute(action, observation) == "switched"

    generations[0x20] = "777:5"  # the editor closed; another program got the handle
    with pytest.raises(RuntimeError, match="belongs to another program"):
        operator.execute(action, observation)

    generations.pop(0x20)  # cannot tell (an elevated process): the old behaviour stands
    assert operator.execute(action, observation) == "switched"
    assert switched == ["0X20", "0X20"]
