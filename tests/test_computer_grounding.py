from __future__ import annotations

from app.agent_runtime.computer_grounding import UITarsGroundingBackend
from app.agent_runtime.computer_types import (
    ComputerControl,
    ComputerFrame,
    ComputerObservation,
    ComputerRect,
    ComputerWindow,
)
from app.ai import ModelResponse


class FakeVisionPlatform:
    def __init__(self):
        self.calls = []

    def execute_chat(self, profile_id, request):
        self.calls.append((profile_id, request))
        return ModelResponse(text="Thought: Save is visible.\nAction: click(start_box='(500,500)')")


def test_ui_tars_backend_is_one_step_vision_adapter_with_uia_context():
    platform = FakeVisionPlatform()
    backend = UITarsGroundingBackend(platform, "gui-profile")
    frame = ComputerFrame(
        frame_id="frame",
        origin_x=100,
        origin_y=200,
        width=1000,
        height=800,
        window_id="0x1",
    )
    window = ComputerWindow(
        window_id="0x1",
        title="Editor",
        rect=ComputerRect(100, 200, 1100, 1000),
        foreground=True,
    )
    observation = ComputerObservation(
        observation_id="obs",
        frame=frame,
        image_png=b"fake-png",
        active_window=window,
        windows=(window,),
        controls=(
            ComputerControl(
                control_id="uia:7",
                name="Save",
                control_type="Button",
                rect=ComputerRect(550, 550, 650, 650),
            ),
        ),
    )

    prediction = backend.predict("Click Save", observation)
    assert prediction.action.point.x == 0.5
    assert prediction.action.point.y == 0.5
    profile_id, request = platform.calls[0]
    assert profile_id == "gui-profile"
    assert request.uses_vision is True
    prompt = request.messages[1].content[0].text
    assert "uia:7" in prompt
    assert "Save" in prompt
    assert "1000x800" in prompt
