"""The HUD pointer must not visit a wrong place on its way to the right one.

Reported while watching a live run: the overlay cursor "does not stop steadily
at the position to be clicked - it often flies out and then pulls back to the
click position".

Each desktop action produces two HUD updates. The completion carries a
``screen_point`` already expressed against the virtual desktop, so it lands
correctly. The start carries only the action itself, whose point is normalized
against the captured application window - and the overlay spans the whole
desktop. Passing that fraction through marked the same fraction of the wrong
rectangle, so every action drew the cursor at a bogus position first and
corrected it ~200ms later, which is precisely the flight out and back.

The fix is to convert the pending point with the frame it belongs to, and to
hold the previous position when that is not possible. A truthful "no new target
yet" beats a confident wrong one.
"""

from __future__ import annotations

from app.agent_runtime.computer_single_loop_runtime import desktop_point
from app.agent_runtime.computer_types import ComputerFrame, ComputerPoint


#: A window sitting well away from the desktop origin, so a window-local
#: fraction and a desktop-local one cannot coincide by accident.
WINDOW = ComputerFrame(frame_id="f", origin_x=1000, origin_y=800, width=400, height=200)
DESKTOP = (0, 0, 2560, 1600)


def test_a_window_local_point_is_converted_not_passed_through(monkeypatch):
    from app.agent_runtime import computer_single_loop_runtime as loop

    monkeypatch.setattr(loop, "_virtual_screen_bounds", lambda: DESKTOP)

    converted = desktop_point(WINDOW, ComputerPoint(0.5, 0.5))

    # Centre of a 400x200 window at (1000,800) is (1200,900) on the desktop.
    assert converted["screen_x"] == 1200
    assert converted["screen_y"] == 900
    assert abs(converted["x_norm"] - 1200 / 2560) < 1e-6
    assert abs(converted["y_norm"] - 900 / 1600) < 1e-6
    # The untranslated fraction would have put the cursor a long way off.
    assert abs(converted["x_norm"] - 0.5) > 0.03


def test_the_start_and_completion_of_one_action_agree(monkeypatch):
    """The two updates per action must not disagree, or the cursor jumps.

    This is the regression in one assertion: whatever the HUD is told when the
    action starts has to be the same place it is told when the action finishes.
    """

    from app.agent_runtime import computer_single_loop_runtime as loop
    from app.app_server_reasoning import ReasoningManagedLoomAppServerService as Service

    monkeypatch.setattr(loop, "_virtual_screen_bounds", lambda: DESKTOP)

    action = {"type": "click", "point": {"x": 0.213, "y": 0.073}}
    converted = desktop_point(WINDOW, ComputerPoint(0.213, 0.073))
    completion = {
        "action": action,
        "screen_point": {
            "screen_x": converted["screen_x"],
            "screen_y": converted["screen_y"],
            "x_norm": converted["x_norm"],
            "y_norm": converted["y_norm"],
        },
    }

    class _Store:
        def latest(self, session_id):
            class _Snapshot:
                observation = type("_Obs", (), {"frame": WINDOW})()

            return _Snapshot()

    service = object.__new__(Service)
    service.runtime = type("_Runtime", (), {"computer_sessions": _Store()})()

    at_completion = Service._hud_point("computer_action", {}, completion)
    at_start = service._hud_point_from_live_frame(
        "session-1", "computer_action", {"action": action}, {}
    )

    assert at_completion is not None
    assert at_start is not None
    assert abs(at_start[0] - at_completion[0]) < 1e-6
    assert abs(at_start[1] - at_completion[1]) < 1e-6
    # And neither is the raw window-local fraction.
    assert abs(at_start[0] - 0.213) > 0.03


def test_the_cursor_holds_position_when_no_frame_is_available():
    """Without a frame there is no honest answer, so do not invent one."""

    from app.app_server_reasoning import ReasoningManagedLoomAppServerService as Service

    service = object.__new__(Service)
    service.runtime = type("_Runtime", (), {"computer_sessions": None})()

    assert (
        service._hud_point_from_live_frame(
            "session-1", "computer_action", {"action": {"type": "click", "point": {"x": 0.5, "y": 0.5}}}, {}
        )
        is None
    )


def test_actions_without_a_point_do_not_move_the_cursor():
    from app.app_server_reasoning import ReasoningManagedLoomAppServerService as Service

    service = object.__new__(Service)
    service.runtime = type("_Runtime", (), {"computer_sessions": None})()

    for action in ({"type": "screenshot"}, {"type": "wait"}, {"type": "hotkey", "keys": ["ctrl", "c"]}):
        assert (
            service._hud_point_from_live_frame("s", "computer_action", {"action": action}, {})
            is None
        )


def test_browser_actions_without_a_real_screen_point_hold_the_hud_cursor():
    from app.app_server_reasoning import ReasoningManagedLoomAppServerService as Service

    for tool, args in (
        ("browser_click", {"index": 8}),
        ("browser_back", {}),
        ("browser_screenshot", {}),
        ("browser_scroll", {"direction": "up"}),
    ):
        assert Service._hud_point(tool, args, {}) is None
