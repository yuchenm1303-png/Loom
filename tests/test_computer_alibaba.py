from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.agent_runtime.computer_alibaba import (
    AlibabaGUIPlusGroundingBackend,
    GUI_PLUS_DEFAULT_MODEL,
    parse_gui_plus_prediction,
)
from app.agent_runtime.computer_types import (
    ComputerActionType,
    ComputerControl,
    ComputerFrame,
    ComputerObservation,
    ComputerRect,
    ComputerWindow,
)


def _prediction(arguments: str, action_text: str = "Use the requested control.") -> str:
    return (
        f"Action: {action_text}\n"
        "<tool_call>\n"
        f'{{"name":"computer_use","arguments":{arguments}}}\n'
        "</tool_call>"
    )


def _observation() -> ComputerObservation:
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
    return ComputerObservation(
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


class _FakeCompletions:
    def __init__(self, text: str):
        self.text = text
        self.calls: list[dict[str, object]] = []

    def create(self, **kwargs):
        self.calls.append(dict(kwargs))
        return SimpleNamespace(
            choices=(SimpleNamespace(message=SimpleNamespace(content=self.text)),)
        )


class _FakeClient:
    def __init__(self, text: str):
        self.completions = _FakeCompletions(text)
        self.chat = SimpleNamespace(completions=self.completions)


def test_gui_plus_click_coordinates_are_normalized():
    prediction = parse_gui_plus_prediction(
        _prediction('{"action":"left_click","coordinate":[500,250]}')
    )
    assert prediction.action.type is ComputerActionType.CLICK
    assert prediction.action.point is not None
    assert prediction.action.point.x == 0.5
    assert prediction.action.point.y == 0.25
    assert prediction.thought == "Use the requested control."


def test_gui_plus_drag_requires_start_and_end_coordinates():
    prediction = parse_gui_plus_prediction(
        _prediction(
            '{"action":"left_click_drag","coordinate":[100,200],"coordinate2":[900,800]}'
        )
    )
    assert prediction.action.type is ComputerActionType.DRAG
    assert prediction.action.point is not None
    assert prediction.action.end_point is not None
    assert prediction.action.point.x == 0.1
    assert prediction.action.point.y == 0.2
    assert prediction.action.end_point.x == 0.9
    assert prediction.action.end_point.y == 0.8


@pytest.mark.parametrize(
    ("arguments", "action_type", "keys"),
    [
        ('{"action":"key","keys":["ENTER"]}', ComputerActionType.KEY, ("enter",)),
        (
            '{"action":"key","keys":["CTRL","S"]}',
            ComputerActionType.HOTKEY,
            ("ctrl", "s"),
        ),
    ],
)
def test_gui_plus_key_actions(arguments, action_type, keys):
    prediction = parse_gui_plus_prediction(_prediction(arguments))
    assert prediction.action.type is action_type
    assert prediction.action.keys == keys


def test_gui_plus_scroll_maps_sign_and_magnitude_to_loom_wheel_action():
    up = parse_gui_plus_prediction(
        _prediction('{"action":"scroll","pixels":3,"coordinate":[400,600]}')
    ).action
    down = parse_gui_plus_prediction(
        _prediction('{"action":"scroll","pixels":-2}')
    ).action
    assert up.type is ComputerActionType.SCROLL
    assert up.direction == "up"
    assert up.amount == 360
    assert up.point is not None and up.point.x == 0.4 and up.point.y == 0.6
    assert down.direction == "down"
    assert down.amount == 240


def test_gui_plus_wait_and_terminal_actions_are_bounded_and_safe():
    wait = parse_gui_plus_prediction(_prediction('{"action":"wait","time":1.5}')).action
    success = parse_gui_plus_prediction(
        _prediction('{"action":"terminate","status":"success"}')
    ).action
    failure = parse_gui_plus_prediction(
        _prediction('{"action":"terminate","status":"failure"}')
    ).action
    interact = parse_gui_plus_prediction(
        _prediction('{"action":"interact","text":"Please solve the blocking dialog"}')
    ).action
    assert wait.type is ComputerActionType.WAIT
    assert wait.duration_ms == 1500
    assert success.type is ComputerActionType.FINISH
    assert failure.type is ComputerActionType.CALL_USER
    assert interact.type is ComputerActionType.CALL_USER


@pytest.mark.parametrize(
    "text",
    [
        "Action: click only",
        '<tool_call>{"name":"other","arguments":{"action":"left_click","coordinate":[1,2]}}</tool_call>',
        '<tool_call>{"name":"computer_use","arguments":{"action":"middle_click","coordinate":[1,2]}}</tool_call>',
        '<tool_call>{"name":"computer_use","arguments":{"action":"left_click","coordinate":[1001,2]}}</tool_call>',
        '<tool_call>{not-json}</tool_call>',
    ],
)
def test_gui_plus_parser_fails_closed_on_invalid_or_unsupported_output(text):
    with pytest.raises(ValueError):
        parse_gui_plus_prediction(text)


def test_gui_plus_backend_sends_one_vision_request_and_does_not_retain_secret():
    fake = _FakeClient(
        _prediction('{"action":"left_click","coordinate":[500,500]}', "Click Save.")
    )
    backend = AlibabaGUIPlusGroundingBackend(
        api_key="runtime-only-secret",
        client=fake,
    )

    prediction = backend.predict("Click Save", _observation())

    assert prediction.action.type is ComputerActionType.CLICK
    assert prediction.action.point is not None
    assert prediction.action.point.x == 0.5
    assert prediction.action.point.y == 0.5
    assert not hasattr(backend, "api_key")
    assert "runtime-only-secret" not in repr(backend.safe_config())

    call = fake.completions.calls[0]
    assert call["model"] == GUI_PLUS_DEFAULT_MODEL
    assert call["extra_body"] == {
        "vl_high_resolution_images": True,
        "enable_thinking": False,
    }
    messages = call["messages"]
    assert isinstance(messages, list)
    user_content = messages[1]["content"]
    assert user_content[0]["type"] == "image_url"
    assert user_content[0]["image_url"]["url"].startswith("data:image/png;base64,")
    assert "uia:7" in user_content[1]["text"]
    assert "Save" in user_content[1]["text"]


def test_gui_plus_environment_configuration_prefers_dedicated_computer_key_without_exposing_it():
    fake = _FakeClient(_prediction('{"action":"wait","time":1}'))
    backend = AlibabaGUIPlusGroundingBackend.from_environment(
        {
            "LOOM_COMPUTER_API_KEY": "computer-secret",
            "DASHSCOPE_API_KEY": "general-secret",
            "LOOM_COMPUTER_MODEL": "gui-plus-custom",
            "LOOM_COMPUTER_BASE_URL": "https://example.invalid/compatible-mode/v1",
            "LOOM_COMPUTER_HIGH_RES": "false",
            "LOOM_COMPUTER_ENABLE_THINKING": "true",
        },
        client=fake,
    )
    assert backend is not None
    assert backend.model == "gui-plus-custom"
    assert backend.high_resolution_images is False
    assert backend.enable_thinking is True
    safe = backend.safe_config()
    assert "computer-secret" not in repr(safe)
    assert "general-secret" not in repr(safe)


def test_gui_plus_environment_configuration_is_optional_without_a_runtime_secret():
    assert AlibabaGUIPlusGroundingBackend.from_environment({}, client=_FakeClient("unused")) is None
