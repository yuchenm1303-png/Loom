"""Regressions from a real Computer Use trace that failed silently.

The trace (session 5ff69674, 2026-09-15) shows a grounding policy clicking the
same dead control three times while every step reported ``execution_ok: true``,
``visual_changed: true`` and ``stuck_detected: false``. Nothing in the loop was
able to say "that press did nothing" or "that press hit the wrong thing", so the
policy had no reason to try anything else.
"""

from __future__ import annotations

from app.agent_runtime.computer_runtime import ComputerSessionStore
from app.agent_runtime.computer_types import (
    ComputerAction,
    ComputerActionType,
    ComputerControl,
    ComputerExecution,
    ComputerFrame,
    ComputerObservation,
    ComputerPoint,
    ComputerPrediction,
    ComputerRect,
    ComputerTrajectoryEntry,
    ComputerWindow,
)


class LiveScreenOperator:
    """A window that repaints on every observation but never acts on a click.

    This is the ordinary case, not a contrived one: a caret, a hover highlight or
    a progress spinner is enough to change the screenshot hash between two
    observations of a screen that is going nowhere.
    """

    name = "live-screen"

    def __init__(self) -> None:
        self.observe_count = 0
        self.executed: list[ComputerAction] = []

    def status(self):
        return {"backend": self.name}

    def observe(self):
        self.observe_count += 1
        frame = ComputerFrame(
            frame_id=f"frame-{self.observe_count}",
            origin_x=1059,
            origin_y=479,
            width=442,
            height=581,
            window_id="0x30ef2",
        )
        active = ComputerWindow(
            window_id="0x30ef2",
            title="WeChat",
            rect=ComputerRect(1059, 479, 1501, 1060),
            foreground=True,
        )
        enter = ComputerControl(
            control_id="uia:9",
            name="Enter WeChat",
            control_type="Button",
            rect=ComputerRect(1180, 830, 1380, 890),
        )
        switch = ComputerControl(
            control_id="uia:12",
            name="Switch account",
            control_type="Button",
            rect=ComputerRect(1180, 900, 1380, 940),
        )
        return ComputerObservation(
            observation_id=f"obs-{self.observe_count}",
            frame=frame,
            # Every observation differs, exactly as a live screen does.
            image_data=b"JPEG" + bytes([self.observe_count]),
            image_media_type="image/jpeg",
            active_window=active,
            windows=(active,),
            controls=(enter, switch),
        )

    def execute(self, action, observation):
        self.executed.append(action)
        return ComputerExecution(ok=True, message="invoked", action=action, native=True)

    def close(self):
        return None


class JitteringGrounder:
    """Re-proposes one target with the small coordinate drift a VLM really has."""

    name = "jittering"

    def __init__(self, points) -> None:
        self.points = list(points)
        self.trajectories: list[tuple[ComputerTrajectoryEntry, ...]] = []

    def predict(self, instruction, observation, trajectory=()):
        self.trajectories.append(tuple(trajectory))
        point = self.points[min(len(self.trajectories) - 1, len(self.points) - 1)]
        return ComputerPrediction(
            action=ComputerAction(type=ComputerActionType.CLICK, point=point),
            thought="press the login button",
        )


def test_repeated_press_on_a_repainting_screen_is_detected_as_stuck():
    """The screenshot hash cannot be the only evidence that nothing happened."""

    operator = LiveScreenOperator()
    # The real trace's three coordinates: 0.770 once, then 0.757 twice. All land
    # inside the same control, which is what makes them one attempt.
    grounder = JitteringGrounder(
        [ComputerPoint(0.495, 0.770), ComputerPoint(0.498, 0.757), ComputerPoint(0.498, 0.757)]
    )
    store = ComputerSessionStore(operator, grounder, settle_delay=0)

    store.step("owner", "Log in")
    store.step("owner", "Log in")
    outcome = store.step("owner", "Log in")

    assert outcome.verification["stuck_detected"] is True
    assert outcome.prediction.action.type is ComputerActionType.WAIT
    # The third press must never reach the desktop.
    assert len(operator.executed) == 2


def test_a_click_reports_which_control_it_actually_hit():
    """"execution_ok" alone cannot distinguish the right button from its neighbour."""

    operator = LiveScreenOperator()
    grounder = JitteringGrounder([ComputerPoint(0.498, 0.757)])
    store = ComputerSessionStore(operator, grounder, settle_delay=0)

    outcome = store.step("owner", "Log in")

    # 0.757 of a 581px window lands on "Switch account", not "Enter WeChat".
    assert outcome.verification["target_label"] == "Button 'Switch account'"
    assert outcome.prediction.action.control_id == "uia:12"

    # And the policy sees it on its next turn, which is the only place it can
    # act on the information.
    store.step("owner", "Log in")
    history = grounder.trajectories[-1]
    assert "hit=Button 'Switch account'" in history[-1].prompt_line()


def test_deliberate_repetition_is_not_treated_as_stuck():
    """Scrolling and typing are repeated on purpose and must keep working."""

    class Repeater:
        name = "repeater"

        def predict(self, instruction, observation, trajectory=()):
            return ComputerPrediction(
                action=ComputerAction(type=ComputerActionType.SCROLL, amount=600),
                thought="keep scrolling",
            )

    operator = LiveScreenOperator()
    store = ComputerSessionStore(operator, Repeater(), settle_delay=0)

    store.step("owner", "Scroll to the bottom")
    store.step("owner", "Scroll to the bottom")
    outcome = store.step("owner", "Scroll to the bottom")

    assert outcome.verification.get("stuck_detected") is not True
    assert len(operator.executed) == 3
