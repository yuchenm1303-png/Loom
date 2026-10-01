from types import SimpleNamespace

import pytest

from app.agent_runtime.computer_types import ComputerControl, ComputerFrame, ComputerRect
from app.agent_runtime.computer_single_loop_runtime import _resolve_frame_target, _model_observation_text


def observation():
    frame = ComputerFrame("current", -11, -11, 2582, 1550, dpi_x=144, dpi_y=144)
    button = ComputerControl("uia:80", "+1", "Button", ComputerRect(280, 560, 310, 590))
    return SimpleNamespace(frame=frame, controls=(button,), windows=(), active_window=None,
                           metadata={}, semantics={})


def test_control_click_uses_current_frame_and_exact_physical_center():
    obs = observation()
    action = _resolve_frame_target({"type": "click", "frame_id": "current", "control_id": "uia:80"}, obs)
    from app.agent_runtime.computer_types import ComputerPoint
    assert obs.frame.to_screen(ComputerPoint(**action["point"])) == (294, 574)
    assert "control_id" not in action  # physical hit-test, not native Invoke


@pytest.mark.parametrize("extra", [
    {"frame_id": "old", "point": {"x": .1, "y": .2}},
    {"control_id": "uia:80"},
    {"frame_id": "current", "control_id": "missing"},
    {"frame_id": "current", "control_id": "uia:80", "point": {"x": .1, "y": .2}},
])
def test_invalid_targets_fail_before_input(extra):
    with pytest.raises(ValueError):
        _resolve_frame_target({"type": "click", **extra}, observation())


def test_disabled_and_clipped_controls_are_refused():
    obs = observation()
    for control in (ComputerControl("x", "x", "Button", ComputerRect(-20, 0, 20, 20)),
                    ComputerControl("x", "x", "Button", ComputerRect(0, 0, 20, 20), enabled=False)):
        obs.controls = (control,)
        with pytest.raises(ValueError):
            _resolve_frame_target({"type": "click", "frame_id": "current", "control_id": "x"}, obs)


def test_actionable_controls_survive_shell_hint_limit():
    obs = observation()
    panes = tuple(ComputerControl(str(i), "", "Pane", ComputerRect(0, 0, 20, 20)) for i in range(50))
    obs.controls = panes + obs.controls
    text = _model_observation_text(SimpleNamespace(observation=obs))
    assert "id=uia:80; Button '+1'" in text
    assert "frame_id=current" in text
    assert "SAME full-image" in text


@pytest.mark.parametrize("origin", [(-2000, -100), (0, 0), (100, 200)])
@pytest.mark.parametrize("dpi", [96, 120, 144])
def test_rect_round_trip_does_not_apply_dpi_twice(origin, dpi):
    x, y = origin
    frame = ComputerFrame("f", x, y, 1000, 800, dpi_x=dpi, dpi_y=dpi)
    rect = ComputerRect(x + 800, y + 600, x + 821, y + 621)
    assert frame.to_screen(rect.center_in(frame)) == (x + 810, y + 610)


def test_browser_shells_do_not_exhaust_collector_limit(monkeypatch):
    import sys
    from app.agent_runtime.computer_windows import PyWinAutoWindowsOperator

    class Wrapper:
        def __init__(self, kind, name):
            self.element_info = SimpleNamespace(control_type=kind, name=name, automation_id="")

        def is_visible(self):
            return True

        def rectangle(self):
            return SimpleNamespace(left=0, top=0, right=20, bottom=20)

        def is_enabled(self):
            return True

    wrappers = [Wrapper("Pane", "") for _ in range(80)] + [Wrapper("Button", "+1")]
    desktop = SimpleNamespace(window=lambda **kw: SimpleNamespace(descendants=lambda: wrappers))
    monkeypatch.setitem(sys.modules, "pywinauto", SimpleNamespace(Desktop=lambda **kw: desktop))
    operator = object.__new__(PyWinAutoWindowsOperator)
    operator.max_controls = 4
    controls, mapping, truncated = operator._walk_uia_controls(1, budget_ms=10000)
    assert [c.name for c in controls] == ["+1"]
    assert "uia:80" in mapping
    assert not truncated
