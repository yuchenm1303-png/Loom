from __future__ import annotations

from app.agent_runtime.computer_runtime import ComputerSessionStore
from app.agent_runtime.computer_types import (
    ComputerAction,
    ComputerControl,
    ComputerExecution,
    ComputerFrame,
    ComputerObservation,
    ComputerPrediction,
    ComputerRect,
    ComputerWindow,
)


class PromotionOperator:
    name = "promotion-test"

    def __init__(self) -> None:
        self.observe_count = 0
        self.executed: list[ComputerAction] = []

    def status(self):
        return {"backend": self.name}

    def observe(self) -> ComputerObservation:
        self.observe_count += 1
        frame = ComputerFrame(
            frame_id=f"frame-{self.observe_count}",
            origin_x=-1280,
            origin_y=0,
            width=1920,
            height=1080,
            window_id="0x10",
            monitor_id="DISPLAY2",
            dpi_x=144,
            dpi_y=144,
        )
        active = ComputerWindow(
            window_id="0x10",
            title="Editor",
            rect=ComputerRect(-1280, 0, 640, 1080),
            foreground=True,
        )
        outer = ComputerControl(
            control_id="uia:outer",
            name="Toolbar",
            control_type="Pane",
            rect=ComputerRect(-400, 450, -100, 650),
        )
        save = ComputerControl(
            control_id="uia:save",
            name="Save",
            control_type="Button",
            rect=ComputerRect(-320, 520, -220, 570),
        )
        return ComputerObservation(
            observation_id=f"obs-{self.observe_count}",
            frame=frame,
            image_png=b"PNG" + bytes([self.observe_count]),
            active_window=active,
            windows=(active,),
            controls=(outer, save),
        )

    def execute(self, action: ComputerAction, observation: ComputerObservation) -> ComputerExecution:
        self.executed.append(action)
        return ComputerExecution(
            ok=True,
            message="executed",
            action=action,
            native=bool(action.control_id),
            fallback_used=not bool(action.control_id),
        )

    def close(self) -> None:
        pass


class PointGrounder:
    name = "point-grounder"

    def predict(self, instruction, observation, trajectory=()):
        save = next(control for control in observation.controls if control.control_id == "uia:save")
        return ComputerPrediction(
            action=ComputerAction(type="click", point=save.rect.center_in(observation.frame)),
            thought="click Save",
        )


def test_visual_single_click_is_promoted_to_smallest_enabled_uia_control():
    operator = PromotionOperator()
    store = ComputerSessionStore(operator, PointGrounder(), settle_delay=0)

    outcome = store.step("owner", "Click Save")

    assert len(operator.executed) == 1
    assert operator.executed[0].control_id == "uia:save"
    assert outcome.prediction.action.control_id == "uia:save"
    assert outcome.execution is not None
    assert outcome.execution.native is True
