"""Batched desktop actions and Set-of-Mark hints in the single-loop Computer Use path.

Model round trips are ~90% of a Computer Use turn's time (scripts/computer_use_report.py
on the real logs, 2026-11-01), so one screenshot may now carry several actions.
The risk is a stale plan: these tests pin that a batch stops - with the rest unsent -
when an action fails or cannot be confirmed, the foreground window changes or
moves, the area around the next target changed, or an action is refused; and
that everything is validated before the first input.
"""

from __future__ import annotations

import io
from dataclasses import replace
from types import SimpleNamespace

import pytest
from PIL import Image, ImageDraw

from app.agent_runtime.computer_single_loop_runtime import (
    MAX_BATCH_ACTIONS,
    SingleLoopComputerRuntime,
    _model_observation_text,
    _resolve_frame_target,
    _set_of_mark_image,
)
from app.agent_runtime.computer_transient import ComputerTransientInputPlatform
from app.agent_runtime.computer_types import (
    ComputerControl,
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
from app.ai import AGENT_FAST_ROLE, ModelResponse, ToolCall

FIELD = (0.25, 0.33)  # a text field
BUTTON = (0.75, 0.66)  # a button far from the field


def _mark(screen, point, size=16, colour=(20, 20, 20)):
    x, y = round(point[0] * 399), round(point[1] * 299)
    ImageDraw.Draw(screen.image).rectangle((x - size // 2, y - size // 2, x + size // 2, y + size // 2), fill=colour)


def visible_feedback(screen, action):
    """What a working click or keystroke looks like: a small repaint where it landed."""

    point = (action.point.x, action.point.y) if action.point is not None else (0.05, 0.9)
    _mark(screen, point)


class Desktop:
    name = "fake-desktop"

    def __init__(self):
        self.image = Image.new("RGB", (400, 300), (235, 235, 235))
        self.window_id = "0x10"
        self.origin = (0, 0)
        self.size = (400, 300)
        self.executed = []
        self.effects = {}
        self.results = {}
        self.observe_count = 0
        self.controls = ()
        self.before_observe = []  # callables run (once) before the next observations

    def status(self):
        return {"backend": self.name}

    def observe(self):
        if self.before_observe:
            self.before_observe.pop(0)(self)
        self.observe_count += 1
        buffer = io.BytesIO()
        self.image.save(buffer, format="PNG")
        left, top = self.origin
        width, height = self.size
        window = ComputerWindow(self.window_id, "Form", process_name="form.exe",
                                rect=ComputerRect(left, top, left + width, top + height), foreground=True)
        return ComputerObservation(
            observation_id=f"obs-{self.observe_count}",
            frame=ComputerFrame(f"frame-{self.observe_count}", left, top, width, height, window_id=self.window_id),
            image_data=buffer.getvalue(),
            image_media_type="image/png",
            active_window=window,
            windows=(window,),
            controls=self.controls,
        )

    def execute(self, action, observation):
        index = len(self.executed)
        self.executed.append(action)
        self.effects.get(index, visible_feedback)(self, action)
        make = self.results.get(index)
        return make(action) if make else ComputerExecution(True, "ok", action, delivery="delivered")

    def close(self):
        pass


@pytest.fixture
def rig(tmp_path, monkeypatch):
    import app.agent_runtime.computer_single_loop_runtime as loop

    monkeypatch.setattr(loop, "_SETTLE_POLL_S", 0.01)
    monkeypatch.setattr(loop, "_SETTLE_MAX_S", 0.2)
    monkeypatch.setattr(loop, "_RECHECK_DELAY_S", 0.01)
    desktop = Desktop()
    runtime = SingleLoopComputerRuntime(
        platform=SimpleNamespace(execute_chat=lambda *a, **k: ModelResponse(text="done")),
        store=FileAgentSessionStore(tmp_path / "state"),
        tools=loom_default_tools(),
        sandbox_manager=SandboxManager(policy=SandboxPolicy.OFF),
        web_search_provider=None,
        auto_configure_web_search=False,
        auto_configure_browser=False,
        computer_operator=desktop,
        auto_configure_computer=False,
        computer_settle_delay=0,
    )
    workspace = tmp_path / "project"
    workspace.mkdir()
    session = runtime.create_session(AGENT_FAST_ROLE.role_id, workspace_dir=workspace,
                                     permission_mode=PermissionMode.FULL_ACCESS)
    context = ToolContext(session_id=session.session_id, turn_id="turn", workspace=workspace,
                          permission_mode=session.permission_mode.value)
    tool = runtime.tools.get("computer_action")
    runtime.computer_sessions.observe(session.session_id)
    yield SimpleNamespace(desktop=desktop, runtime=runtime, context=context,
                          run=lambda arguments: tool.handler(context, arguments))
    runtime.close()


def _click(point):
    return {"type": "click", "point": {"x": point[0], "y": point[1]}}


FORM = [_click(FIELD), {"type": "type", "text": "Ada"}, {"type": "key", "keys": ["tab"]},
        _click(BUTTON)]


def test_a_batch_runs_every_action_when_each_one_checks_out(rig):
    result = rig.run({"actions": FORM})

    assert result.ok is True
    assert [action.type.value for action in rig.desktop.executed] == ["click", "type", "key", "click"]
    assert result.data["batch"]["executed"] == 4
    assert result.data["batch"]["stopped_reason"] == ""
    assert "Executed 4 of 4" in result.content


def test_a_click_with_no_visible_effect_ends_the_batch(rig):
    rig.desktop.effects[0] = lambda screen, action: None

    result = rig.run({"actions": FORM})

    assert result.ok is False
    assert len(rig.desktop.executed) == 1
    assert result.data["batch"]["stopped_reason"] == "action_not_confirmed"
    assert result.data["batch"]["stopped_at"] == 2
    assert "2-4 were not sent" in result.content


def test_an_uncertain_native_write_ends_the_batch_and_says_why(rig):
    rig.desktop.results[0] = lambda action: ComputerExecution(False, "UI Automation did not confirm invoking", action,
                                                             delivery="uncertain")

    result = rig.run({"actions": FORM})

    assert len(rig.desktop.executed) == 1
    assert result.data["batch"]["steps"][0]["delivery"] == "uncertain"
    assert "did not confirm invoking" in result.content


def test_a_popup_over_the_next_target_ends_the_batch_before_it(rig):
    def opens_popup(screen, action):
        visible_feedback(screen, action)
        _mark(screen, BUTTON, size=60, colour=(255, 255, 255))  # a plain white popup on a light app

    rig.desktop.effects[0] = opens_popup

    result = rig.run({"actions": [_click(FIELD), _click(BUTTON)]})

    assert len(rig.desktop.executed) == 1
    assert result.data["batch"]["stopped_reason"] == "target_region_changed"


def test_keyboard_steps_do_not_need_an_unchanged_target(rig):
    rig.desktop.effects[0] = lambda screen, action: (visible_feedback(screen, action),
                                                     _mark(screen, (0.05, 0.9), size=60))

    result = rig.run({"actions": [_click(FIELD), {"type": "type", "text": "Ada"}]})

    assert result.ok is True and len(rig.desktop.executed) == 2


def test_losing_the_foreground_window_ends_the_batch(rig):
    def opens_other_app(screen, action):
        visible_feedback(screen, action)
        screen.window_id = "0x99"

    rig.desktop.effects[0] = opens_other_app

    result = rig.run({"actions": FORM})

    assert len(rig.desktop.executed) == 1
    assert result.data["batch"]["stopped_reason"] == "foreground_changed"


def test_steps_planned_on_the_window_a_switch_left_are_not_sent(rig, monkeypatch):
    def switches(screen, action):
        screen.window_id = "0x20"
        screen.image = Image.new("RGB", (400, 300), (30, 30, 60))

    rig.desktop.effects[0] = switches
    store = rig.runtime.computer_sessions
    monkeypatch.setattr(store, "_verify", lambda before, after, action, execution: {"target_confirmed": True})

    result = rig.run({"actions": [{"type": "switch_window", "window_id": "0x20"}, _click(BUTTON)]})

    assert [action.type.value for action in rig.desktop.executed] == ["switch_window"]
    assert result.data["batch"]["stopped_reason"] == "foreground_changed"


def test_a_target_that_cannot_be_compared_is_not_acted_on():
    from app.agent_runtime.computer_single_loop_runtime import _batch_stop_reason
    from app.agent_runtime.computer_types import ComputerAction, ComputerActionType, ComputerPoint

    frame = ComputerFrame("f", 0, 0, 400, 300, window_id="0x10")
    unreadable = SimpleNamespace(observation=SimpleNamespace(frame=frame, image_data=b"not an image"))
    click = ComputerAction(ComputerActionType.CLICK, point=ComputerPoint(0.5, 0.5))

    assert _batch_stop_reason(unreadable, unreadable, click) == "target_unverifiable"
    assert _batch_stop_reason(unreadable, unreadable, ComputerAction(ComputerActionType.KEY, keys=("tab",))) == ""


def test_a_moved_window_ends_the_batch_before_the_next_pointer_action(rig):
    def moves(screen, action):
        visible_feedback(screen, action)
        screen.origin = (40, 0)

    rig.desktop.effects[1] = moves  # the type step moves the window

    result = rig.run({"actions": [_click(FIELD), {"type": "type", "text": "Ada"}, _click(BUTTON)]})

    assert [action.type.value for action in rig.desktop.executed] == ["click", "type"]
    assert result.data["batch"]["stopped_reason"] == "window_moved"


def test_a_refused_step_ends_the_batch_and_nothing_after_it_runs(rig):
    result = rig.run({"actions": [_click(FIELD), {"type": "hotkey", "keys": ["win", "d"]}, _click(BUTTON)]})

    assert len(rig.desktop.executed) == 1
    assert result.data["batch"]["stopped_reason"] == "refused:destructive_global_hotkey_refused"
    assert "shows the desktop" in result.content


@pytest.mark.parametrize(
    "arguments, message",
    [
        ({"actions": [_click(FIELD), {"type": "screenshot"}]}, "cannot be part of an actions batch"),
        ({"actions": [_click(FIELD), {"type": "type", "text": "x"}, {"type": "click"}]}, "requires point or control_id"),
        ({"actions": [_click(FIELD)] * (MAX_BATCH_ACTIONS + 1)}, "at most"),
        ({"actions": []}, "non-empty"),
        ({"action": _click(FIELD), "actions": [_click(FIELD)]}, "either action or actions"),
    ],
)
def test_a_malformed_batch_sends_nothing(rig, arguments, message):
    with pytest.raises(ValueError, match=message):
        rig.run(arguments)
    assert rig.desktop.executed == []


def test_a_single_action_still_works_and_reports_its_executor_message(rig):
    rig.desktop.results[0] = lambda action: ComputerExecution(False, "native console input failed", action)

    result = rig.run({"action": {"type": "type", "text": "Ada"}})

    assert result.ok is False
    assert "native console input failed" in result.content
    assert "batch" not in result.data


def test_typed_text_inside_a_batch_never_reaches_history():
    class Inner:
        def execute_chat(self, profile_id, request):
            return ModelResponse(tool_calls=(ToolCall("c1", "computer_action", {
                "actions": [_click(FIELD), {"type": "type", "text": "hunter2"}],
            }),))

    response = ComputerTransientInputPlatform(Inner()).execute_chat("p", None)
    batch = response.tool_calls[0].arguments["actions"]
    assert batch[0] == _click(FIELD)
    assert batch[1]["text"].startswith("loom-transient-computer:")
    assert "hunter2" not in str(response.tool_calls[0].arguments)


# -- Set-of-Mark ------------------------------------------------------------


def _observation_with_controls(media_type="image/png"):
    image = Image.new("RGB", (200, 100), (250, 250, 250))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG" if media_type == "image/png" else "JPEG")
    frame = ComputerFrame("frame-1", 1000, 500, 400, 200, window_id="0x10")  # image is downscaled 2x
    control = ComputerControl("uia:17", "Save", "Button", ComputerRect(1100, 560, 1180, 600))
    return ComputerObservation("obs", frame, buffer.getvalue(), image_media_type=media_type, controls=(control,)), control


def test_set_of_mark_boxes_each_hint_where_it_is_on_the_downscaled_image():
    observation, control = _observation_with_controls()

    data, media_type = _set_of_mark_image(observation, [control])
    marked = Image.open(io.BytesIO(data)).convert("RGB")

    assert media_type == "image/png"
    # The control spans x 1100..1180, y 560..600 on screen -> 50..90, 30..50 in the image.
    edge = marked.getpixel((70, 50))
    assert edge[0] > 180 and edge[1] < 80 and edge[2] > 100  # magenta outline
    assert marked.getpixel((70, 40)) == (250, 250, 250)  # inside the box is untouched
    assert marked.getpixel((150, 80)) == (250, 250, 250)  # elsewhere is untouched


def test_set_of_mark_leaves_the_capture_alone_and_says_so_in_the_text():
    observation, control = _observation_with_controls()
    original = observation.image_data

    assert _set_of_mark_image(observation, []) is None
    _set_of_mark_image(observation, [control])
    assert observation.image_data == original

    snapshot = SimpleNamespace(observation=observation)
    assert "[17] Button 'Save'" in _model_observation_text(snapshot, marked=True)
    assert "boxed in magenta" in _model_observation_text(snapshot, marked=True)
    assert "boxed in magenta" not in _model_observation_text(snapshot, marked=False)


def test_the_number_on_the_box_is_a_valid_control_id_for_click_and_type():
    observation, control = _observation_with_controls()
    expected = control.rect.center_in(observation.frame).to_dict()

    for value in ("17", "[17]", "uia:17"):
        resolved = _resolve_frame_target({"type": "type", "text": "x", "control_id": value, "frame_id": "frame-1"},
                                         observation)
        assert resolved["point"] == expected


def test_the_model_sees_the_marked_copy_unless_disabled(rig, monkeypatch):
    rig.desktop.controls = (ComputerControl("uia:3", "Go", "Button", ComputerRect(250, 150, 330, 190)),)
    store = rig.runtime.computer_sessions
    store.observe(rig.context.session_id)
    session = rig.runtime.get_session(rig.context.session_id)
    images = []

    def capture(messages, text, **kwargs):
        images.append(kwargs["image"].image_url)
        return messages

    monkeypatch.setattr("app.agent_runtime.tool_observation.attach_observation", capture)
    for flag in ("1", "0"):
        monkeypatch.setenv("LOOM_COMPUTER_SET_OF_MARK", flag)
        rig.runtime._computer_feedback_turns[session.session_id] = session.current_turn_id
        _, extra = rig.runtime._collect_model_observations(session, None)
        assert extra["computer_observation"]["set_of_mark"] is (flag == "1")
    assert images[0] != images[1]
    assert images[1] == store.latest(rig.context.session_id).observation.image_data_url()


def test_jpeg_noise_around_an_unchanged_target_does_not_stop_the_batch():
    from app.agent_runtime.computer_single_loop_runtime import _target_region_changed
    from app.agent_runtime.computer_types import ComputerPoint

    def jpeg(image):
        buffer = io.BytesIO()
        image.save(buffer, format="JPEG", quality=70)
        return SimpleNamespace(image_data=buffer.getvalue())

    screen = Image.new("RGB", (400, 300), (235, 235, 235))
    draw = ImageDraw.Draw(screen)
    for x in range(0, 400, 7):  # busy text-like content everywhere
        draw.line((x, 0, x + 40, 300), fill=(30, 30, 30))
    planned = jpeg(screen)
    draw.rectangle((10, 10, 120, 80), fill=(200, 30, 30))  # something changed far away
    current = jpeg(screen)

    assert _target_region_changed(planned, current, ComputerPoint(0.75, 0.66)) is False
    assert _target_region_changed(planned, current, ComputerPoint(0.15, 0.15)) is True


def _caret(screen, point):
    x, y = round(point[0] * 399), round(point[1] * 299)
    ImageDraw.Draw(screen.image).line((x, y - 8, x, y + 8), fill=(0, 0, 0), width=1)


def test_a_caret_at_the_click_point_counts_as_the_click_working(rig):
    rig.desktop.effects[0] = lambda screen, action: _caret(screen, FIELD)

    result = rig.run({"actions": [_click(FIELD), {"type": "type", "text": "Ada"}]})

    assert result.ok is True and len(rig.desktop.executed) == 2
    assert result.data["batch"]["steps"][0]["effect_reason"] == "local_change_at_target"


def test_a_caret_elsewhere_is_not_evidence_for_this_click(rig):
    rig.desktop.effects[0] = lambda screen, action: _caret(screen, BUTTON)

    result = rig.run({"actions": [_click(FIELD), {"type": "type", "text": "Ada"}]})

    assert len(rig.desktop.executed) == 1
    assert result.data["batch"]["stopped_reason"] == "action_not_confirmed"


def test_a_repaint_that_lags_is_caught_by_the_second_look(rig):
    # Nothing at the post-action capture; the caret blinks on for the next one.
    rig.desktop.effects[0] = lambda screen, action: screen.before_observe.extend(
        [lambda s: None, lambda s: _caret(s, FIELD)])

    result = rig.run({"actions": [_click(FIELD), {"type": "type", "text": "Ada"}]})

    assert result.ok is True and len(rig.desktop.executed) == 2
    assert result.data["batch"]["steps"][0]["effect_reason"] == "delayed_change_at_target"


def test_the_caret_of_an_empty_field_appears_at_its_start_not_under_the_click(rig):
    # A click in the middle of an empty 0.30..0.70-wide field; its caret shows at the left edge.
    rig.desktop.effects[0] = lambda screen, action: _caret(screen, (0.31, FIELD[1]))

    result = rig.run({"actions": [_click((0.5, FIELD[1])), {"type": "type", "text": "Ada"}]})

    assert result.ok is True and len(rig.desktop.executed) == 2


def test_a_repaint_in_another_row_is_not_evidence_for_this_click(rig):
    rig.desktop.effects[0] = lambda screen, action: _caret(screen, (0.31, FIELD[1] + 0.2))

    result = rig.run({"actions": [_click((0.5, FIELD[1])), {"type": "type", "text": "Ada"}]})

    assert len(rig.desktop.executed) == 1


def test_the_model_sees_its_own_earlier_text_while_the_session_keeps_only_handles():
    """A handle or a placeholder in history reads as a value and models copied it into
    new calls (live runs, 2026-11-01). The model is shown the text it wrote; the
    durable session keeps the handle; a forgotten handle reads as the placeholder,
    and typing the placeholder is refused.
    """

    from app.agent_runtime.computer_transient import _WITHHELD
    from app.ai import AIMessage, ChatRequest, MessageRole

    seen = []

    class Inner:
        def execute_chat(self, profile_id, request):
            seen.append(request)
            return ModelResponse(tool_calls=(ToolCall("c2", "computer_action", {
                "action": {"type": "type", "text": _WITHHELD}}),))

    platform = ComputerTransientInputPlatform(Inner())
    handle = platform._stash("hunter2")
    forgotten = "loom-transient-computer:" + "0" * 32
    past = AIMessage(role=MessageRole.ASSISTANT, content="", tool_calls=(
        ToolCall("c1", "computer_action", {"actions": [_click(FIELD), {"type": "type", "text": handle},
                                                       {"type": "type", "text": forgotten}]}),))
    request = ChatRequest(messages=(AIMessage(role=MessageRole.USER, content="go"), past))

    assert platform.consume(handle) == "hunter2"  # typed already; still shown back
    response = platform.execute_chat("p", request)

    sent = seen[0].messages[1].tool_calls[0].arguments["actions"]
    assert sent[1]["text"] == "hunter2" and sent[2]["text"] == _WITHHELD and sent[0] == _click(FIELD)
    assert request.messages[1].tool_calls[0].arguments["actions"][1]["text"] == handle  # the session keeps it
    with pytest.raises(RuntimeError):
        platform.consume(handle)  # still one-shot for typing
    with pytest.raises(RuntimeError, match="history placeholder"):
        platform.consume(response.tool_calls[0].arguments["action"]["text"])
    platform.clear()
    assert platform._recall(handle) == _WITHHELD


def test_a_copied_handle_is_never_shown_back_as_typed_text():
    from app.agent_runtime.computer_transient import _WITHHELD

    platform = ComputerTransientInputPlatform(object())
    original = platform._stash("hunter2")
    copy = platform._stash(original)
    assert platform._recall(copy) == _WITHHELD


@pytest.mark.parametrize(
    "reference, expected",
    [
        ("0x30", "0x30"),
        ("Loom Form Test", "0x30"),
        ("loom form test", "0x30"),
        ("pythonw.exe", "0x30"),
        ("pythonw", "0x30"),
        ("Form", "0x30"),
    ],
)
def test_switch_window_takes_a_title_or_process_that_names_one_window(reference, expected):
    from app.agent_runtime.computer_single_loop_runtime import _resolve_window_reference

    observation = SimpleNamespace(windows=(
        ComputerWindow("0x10", "Claude", process_name="claude.exe"),
        ComputerWindow("0x20", "Loom", process_name="Loom.exe"),
        ComputerWindow("0x30", "Loom Form Test", process_name="pythonw.exe"),
    ))
    assert _resolve_window_reference(reference, observation) == expected


@pytest.mark.parametrize("reference, message", [("Lo", "several windows"), ("Notepad", "no known window")])
def test_a_vague_or_unknown_window_name_is_refused_not_guessed(reference, message):
    from app.agent_runtime.computer_single_loop_runtime import _resolve_window_reference

    observation = SimpleNamespace(windows=(
        ComputerWindow("0x20", "Loom", process_name="Loom.exe"),
        ComputerWindow("0x30", "Loom Form Test", process_name="pythonw.exe"),
    ))
    with pytest.raises(ValueError, match=message):
        _resolve_window_reference(reference, observation)


def test_switch_window_by_title_reaches_the_operator_as_its_id(rig):
    rig.desktop.effects[0] = lambda screen, action: None

    rig.run({"action": {"type": "switch_window", "window_id": "Form"}})

    assert rig.desktop.executed[0].window_id == "0x10"


@pytest.mark.parametrize("arguments", [
    {"action": {"actions": [_click(FIELD), {"type": "type", "text": "hunter2"}]}},
    {"frame_id": "f", "actions": [{"type": "type", "text": "hunter2"}]},
    {"action": {"type": "type", "text": "hunter2"}},
])
def test_typed_text_is_stashed_in_any_shape_the_model_sends(arguments):
    """A malformed call is rejected only after the response is durable, so its text must be stashed too."""

    class Inner:
        def execute_chat(self, profile_id, request):
            return ModelResponse(tool_calls=(ToolCall("c1", "computer_action", arguments),))

    platform = ComputerTransientInputPlatform(Inner())
    stashed = platform.execute_chat("p", None).tool_calls[0].arguments
    assert "hunter2" not in str(stashed)
    assert "loom-transient-computer:" in str(stashed)


def test_a_frame_id_beside_the_batch_applies_to_each_step(rig):
    frame_id = rig.runtime.computer_sessions.latest(rig.context.session_id).observation.frame.frame_id
    rig.desktop.controls = ()

    result = rig.run({"frame_id": frame_id, "actions": [_click(FIELD), {"type": "type", "text": "Ada"}]})

    assert result.ok is True and len(rig.desktop.executed) == 2
    with pytest.raises(ValueError, match="stale frame_id"):
        rig.run({"frame_id": "an-old-frame", "actions": [_click(FIELD)]})


def test_a_batch_nested_inside_action_runs_as_a_batch(rig):
    result = rig.run({"action": {"actions": [_click(FIELD), {"type": "type", "text": "Ada"}]}})

    assert result.ok is True and len(rig.desktop.executed) == 2
    with pytest.raises(ValueError, match="not both"):
        rig.run({"action": {"type": "click", "actions": [_click(FIELD)]}})


def test_a_frame_id_beside_a_single_action_is_its_frame_id(rig):
    rig.desktop.controls = (ComputerControl("uia:3", "Go", "Button", ComputerRect(250, 150, 330, 190)),)
    store = rig.runtime.computer_sessions
    frame_id = store.observe(rig.context.session_id).observation.frame.frame_id

    rig.run({"frame_id": frame_id, "action": {"type": "click", "control_id": "3"}})

    assert rig.desktop.executed[0].point is not None


def test_a_screen_still_changing_is_observed_until_it_stops(rig):
    # The click opens something that keeps repainting for two more captures.
    frames = iter([(0.6, 0.2), (0.6, 0.5), (0.6, 0.8)])

    def keeps_painting(screen, action):
        visible_feedback(screen, action)
        screen.before_observe.extend([lambda s: _mark(s, next(frames), size=30)] * 3)

    rig.desktop.effects[0] = keeps_painting
    result = rig.run({"action": _click(FIELD)})

    assert result.data["verification"]["settled"] is True
    final = rig.runtime.computer_sessions.latest(rig.context.session_id).observation
    assert result.data["after"]["image_sha256"] == final.image_sha256
    assert not rig.desktop.before_observe  # every repaint was waited out


def test_a_screen_that_never_stops_changing_is_not_waited_on_forever(rig):
    import time as clock

    def forever(screen, action):
        visible_feedback(screen, action)
        screen.before_observe.extend(
            [lambda s, i=i: _mark(s, (0.05 + (i % 20) * 0.045, 0.95), size=12, colour=(i * 7 % 255, 0, 0))
             for i in range(400)])

    rig.desktop.effects[0] = forever
    started = clock.monotonic()
    result = rig.run({"action": _click(FIELD)})

    assert result.data["verification"]["settled"] is False
    assert clock.monotonic() - started < 2.0


def test_a_small_status_change_away_from_the_click_counts_as_an_effect(rig):
    # Clicking seat F7 rewrites a short status label at the bottom of the window:
    # a few hundred pixels, invisible in the 128x72 sample.
    def status_label(screen, action):
        # About 80 changed pixels: over the full-resolution threshold, under what
        # the 128x72 sample can register.
        ImageDraw.Draw(screen.image).rectangle((20, 270, 29, 277), fill=(0, 0, 0))

    rig.desktop.effects[0] = status_label
    result = rig.run({"actions": [_click(BUTTON), _click(FIELD)]})

    assert result.data["batch"]["steps"][0]["effect"] == "changed"
    assert len(rig.desktop.executed) == 2


def test_a_lone_blinking_caret_elsewhere_is_not_an_effect():
    from app.agent_runtime.computer_single_loop_runtime import _changed_pixels, _FULL_RES_MIN_CHANGED

    def png(draw_caret):
        image = Image.new("RGB", (400, 300), (235, 235, 235))
        if draw_caret:
            ImageDraw.Draw(image).line((50, 40, 50, 56), fill=(0, 0, 0))
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        return SimpleNamespace(image_data=buffer.getvalue(), image_sha256=str(draw_caret))

    assert 0 < _changed_pixels(png(False), png(True)) < _FULL_RES_MIN_CHANGED


def test_every_walked_control_is_numbered_on_a_dense_window():
    # A 10x8 seat grid: cut at 40, half the seats had no number and the model
    # clicked a numbered neighbour.
    from app.agent_runtime.computer_single_loop_runtime import _hint_controls

    frame = ComputerFrame("f", 0, 0, 800, 600)
    controls = tuple(
        ComputerControl(f"uia:{i}", f"S{i}", "Button", ComputerRect(10 + (i % 10) * 70, 10 + (i // 10) * 60,
                                                                     60 + (i % 10) * 70, 50 + (i // 10) * 60))
        for i in range(80))
    observation = SimpleNamespace(frame=frame, controls=controls)

    assert [c.control_id for c in _hint_controls(observation)] == [c.control_id for c in controls]


# -- zoom and background windows ---------------------------------------------


def test_zoom_attaches_an_enlarged_view_beside_the_screenshot_with_the_coordinate_rule(rig, monkeypatch):
    regions = []

    def capture_region(frame, region):
        regions.append(region)
        return b"\xff\xd8zoomed", "image/jpeg", 3.0

    rig.desktop.capture_region = capture_region
    result = rig.run({"action": {"type": "zoom", "region": {"x0": 0.1, "y0": 0.2, "x1": 0.4, "y1": 0.5}}})

    assert result.ok is True and regions == [(0.1, 0.2, 0.4, 0.5)]
    assert rig.desktop.executed == []
    attached = []
    monkeypatch.setattr("app.agent_runtime.tool_observation.attach_observation",
                        lambda messages, text, **kw: attached.append((text, kw.get("image"))) or messages)
    session = rig.runtime.get_session(rig.context.session_id)
    rig.runtime._computer_feedback_turns[session.session_id] = session.current_turn_id
    view = rig.runtime._computer_extra_views[session.session_id]
    rig.runtime._computer_extra_views[session.session_id] = (session.current_turn_id, *view[1:])
    _, extra = rig.runtime._collect_model_observations(session, None)
    assert extra["computer_observation"]["extra_view"] is True
    assert len(attached) == 2 and "x = 0.100 + u*0.300" in attached[1][0]
    _, extra = rig.runtime._collect_model_observations(session, None)  # one-shot
    assert "computer_observation" not in extra or extra["computer_observation"]["extra_view"] is False


@pytest.mark.parametrize("region", [
    {"x0": 0.5, "y0": 0.2, "x1": 0.4, "y1": 0.5},
    {"x0": 0.1, "y0": 0.2, "x1": 0.105, "y1": 0.5},
    {"x0": 0.1, "y0": 0.2},
])
def test_a_malformed_zoom_region_is_refused(rig, region):
    rig.desktop.capture_region = lambda frame, region: (b"x", "image/jpeg", 1.0)
    with pytest.raises((ValueError, KeyError)):
        rig.run({"action": {"type": "zoom", "region": region}})


def test_screenshot_of_a_background_window_does_not_switch_to_it(rig):
    captured = []
    rig.desktop.capture_window = lambda window_id: captured.append(window_id) or (b"png", "image/png", {})
    rig.desktop.window_id = "0x10"

    original = rig.desktop.observe

    def with_second_window():
        observation = original()
        other = ComputerWindow("0x77", "Verification Code", process_name="pythonw.exe")
        return replace(observation, windows=(*observation.windows, other))

    rig.desktop.observe = with_second_window
    result = rig.run({"action": {"type": "screenshot", "window_id": "Verification Code"}})

    assert result.ok is True and captured == ["0x77"] and result.data["peeked"] == "0x77"
    assert rig.desktop.executed == []  # nothing was activated
    assert "switch_window 0x77 first" in rig.runtime._computer_extra_views[rig.context.session_id][3]


def test_zoom_and_peek_are_not_batch_steps(rig):
    for step in ({"type": "zoom", "region": {"x0": 0, "y0": 0, "x1": 0.5, "y1": 0.5}}, {"type": "screenshot"}):
        with pytest.raises(ValueError, match="cannot be part of an actions batch"):
            rig.run({"actions": [_click(FIELD), step]})


def test_a_hint_can_be_named_instead_of_numbered():
    frame = ComputerFrame("frame-1", 0, 0, 400, 300)
    controls = (
        ComputerControl("uia:5", "F7", "Button", ComputerRect(100, 100, 130, 120)),
        ComputerControl("uia:6", "F8", "Button", ComputerRect(140, 100, 170, 120)),
        ComputerControl("uia:7", "OK", "Button", ComputerRect(10, 10, 40, 30)),
        ComputerControl("uia:8", "OK", "Button", ComputerRect(10, 50, 40, 70)),
        ComputerControl("uia:9", "Off", "Button", ComputerRect(10, 90, 40, 110), enabled=False),
    )
    observation = SimpleNamespace(frame=frame, controls=controls)

    resolved = _resolve_frame_target({"type": "click", "control_id": "f7", "frame_id": "frame-1"}, observation)
    assert resolved["point"] == controls[0].rect.center_in(frame).to_dict()
    with pytest.raises(ValueError, match="2 controls are named"):
        _resolve_frame_target({"type": "click", "control_id": "OK", "frame_id": "frame-1"}, observation)
    with pytest.raises(ValueError):
        _resolve_frame_target({"type": "click", "control_id": "Off", "frame_id": "frame-1"}, observation)
    with pytest.raises(ValueError):
        _resolve_frame_target({"type": "click", "control_id": "F", "frame_id": "frame-1"}, observation)
