from __future__ import annotations

import json

import pytest

from app.agent_runtime.computer_grounding import parse_ui_tars_prediction
from app.agent_runtime.computer_runtime import ComputerSessionStore, ComputerUseRuntime
from app.agent_runtime.computer_single_loop_runtime import SingleLoopComputerRuntime
from app.agent_runtime.mcp_configured_runtime import ConfiguredMCPRuntime
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
    ComputerWindow,
)
from app.agent_runtime.contracts import AgentStatus, PermissionMode, ToolEffect
from app.agent_runtime.sandbox import SandboxManager, SandboxPolicy
from app.agent_runtime.storage import FileAgentSessionStore
from app.agent_runtime.workspace_tools import loom_default_tools
from app.ai import AGENT_FAST_ROLE, ImagePart, ModelResponse, ToolCall


class ScriptedPlatform:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def execute_chat(self, profile_id, request):
        self.requests.append((profile_id, request))
        if not self.responses:
            raise AssertionError("scripted platform ran out of responses")
        return self.responses.pop(0)


class FakeOperator:
    name = "fake-computer"

    def __init__(self, *, static_image: bool = False):
        self.static_image = static_image
        self.observe_count = 0
        self.executed: list[ComputerAction] = []
        self.closed = False

    def status(self):
        return {"backend": self.name, "secure_desktop": False}

    def observe(self):
        self.observe_count += 1
        marker = 1 if self.static_image else self.observe_count
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
            title="Editor - private draft",
            process_name="editor.exe",
            rect=ComputerRect(-1280, 0, 640, 1080),
            foreground=True,
        )
        control = ComputerControl(
            control_id="uia:0",
            name="Save private draft",
            control_type="Button",
            rect=ComputerRect(-340, 500, -220, 580),
        )
        return ComputerObservation(
            observation_id=f"obs-{self.observe_count}",
            frame=frame,
            image_data=b"PNG" + bytes([marker]),
            active_window=active,
            windows=(active,),
            controls=(control,),
        )

    def execute(self, action, observation):
        self.executed.append(action)
        return ComputerExecution(
            ok=True,
            message="fake execution",
            action=action,
            native=bool(action.control_id),
            fallback_used=not bool(action.control_id),
        )

    def close(self):
        self.closed = True


class FakeGrounder:
    name = "fake-grounder"

    def __init__(self, action: ComputerAction | None = None):
        self.action = action or ComputerAction(type="click", point=ComputerPoint(0.5, 0.5))
        self.calls = []

    def predict(self, instruction, observation, trajectory=()):
        self.calls.append((instruction, observation.observation_id, tuple(trajectory)))
        return ComputerPrediction(action=self.action, thought="click target")


def _runtime(
    tmp_path,
    responses,
    *,
    mode=PermissionMode.FULL_ACCESS,
    static_image=False,
    configured=False,
):
    platform = ScriptedPlatform(responses)
    operator = FakeOperator(static_image=static_image)
    runtime_cls = ConfiguredMCPRuntime if configured else SingleLoopComputerRuntime
    kwargs = dict(
        platform=platform,
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
    if configured:
        kwargs["mcp_servers"] = ()
    runtime = runtime_cls(**kwargs)
    workspace = tmp_path / "project"
    workspace.mkdir()
    session = runtime.create_session(
        AGENT_FAST_ROLE.role_id,
        workspace_dir=workspace,
        permission_mode=mode,
    )
    return runtime, platform, operator, session


def _tool_context(session, workspace, *, turn_id="test-turn"):
    from app.agent_runtime.tools import ToolContext

    return ToolContext(
        session_id=session.session_id,
        turn_id=turn_id,
        workspace=workspace,
        permission_mode=session.permission_mode.value,
    )


def _visible_computer_tools(runtime):
    return {
        tool.name
        for tool in runtime.tools.router(capability_settings={}).all()
        if tool.name.startswith("computer_")
    }


def test_default_configured_mcp_stack_uses_single_loop_not_ufo_driver(tmp_path):
    runtime, _, operator, session = _runtime(
        tmp_path,
        [ModelResponse(text="done")],
        configured=True,
    )

    assert isinstance(runtime, SingleLoopComputerRuntime)
    assert isinstance(runtime, ComputerUseRuntime)
    assert _visible_computer_tools(runtime) == {"computer_action", "computer_status"}
    status = runtime.computer_status(session.session_id)
    assert status["architecture"] == "single-model-single-loop"
    assert status["model_control"] == "current conversation model via Loom TurnRunner"
    assert status["ufo_default_path"] is False
    runtime.close()
    assert operator.closed is True


def test_single_loop_tool_surface_is_one_action_plus_status(tmp_path):
    runtime, _, _, session = _runtime(tmp_path, [ModelResponse(text="done")])
    assert _visible_computer_tools(runtime) == {"computer_action", "computer_status"}
    assert runtime.tools.get("computer_status").effect is ToolEffect.READ_ONLY
    assert runtime.tools.get("computer_action").effect is ToolEffect.SENSITIVE
    assert runtime.tools.get("computer_observe").exposure.value == "hidden"
    assert runtime.tools.get("computer_step").exposure.value == "hidden"
    assert runtime.tools.get("computer_run_task").exposure.value == "hidden"
    status = runtime.computer_status(session.session_id)
    assert status["coordinate_execution"] == "primary"
    assert status["uia_role"].startswith("advisory")
    runtime.close()


def test_screenshot_is_ephemeral_model_input_not_durable_history(tmp_path):
    runtime, platform, _, session = _runtime(
        tmp_path,
        [
            ModelResponse(
                tool_calls=(
                    ToolCall(
                        call_id="screen-1",
                        name="computer_action",
                        arguments={"action": {"type": "screenshot"}},
                    ),
                )
            ),
            ModelResponse(text="I can see the desktop now."),
        ],
    )

    result = runtime.start_turn(session.session_id, "Look at the desktop.")

    assert result.status is AgentStatus.COMPLETED
    assert len(platform.requests) == 2
    _, follow_up = platform.requests[1]
    visual_messages = [message for message in follow_up.messages if message.uses_vision]
    assert len(visual_messages) == 1
    assert any(isinstance(part, ImagePart) for part in visual_messages[0].content)
    assert "LOOM_COMPUTER_OBSERVATION" in str(visual_messages[0].content)

    durable = runtime.get_session(session.session_id)
    durable_text = repr(durable.messages)
    assert "data:image/" not in durable_text
    assert "private draft" not in durable_text
    runtime.close()


def test_coordinate_click_with_no_observable_effect_returns_false(tmp_path):
    runtime, _, operator, session = _runtime(
        tmp_path,
        [ModelResponse(text="unused")],
        static_image=True,
    )
    tool = runtime.tools.get("computer_action")
    context = _tool_context(session, tmp_path / "project")

    screen = tool.handler(context, {"action": {"type": "screenshot"}})
    assert screen.ok is True
    result = tool.handler(
        context,
        {"action": {"type": "click", "point": {"x": 0.5, "y": 0.5}}},
    )

    assert len(operator.executed) == 1
    assert result.ok is False
    assert result.data["verification"]["execution_ok"] is True
    assert result.data["verification"]["effect"] == "unchanged"
    assert result.data["verification"]["effect_reason"] == "no_observable_change_after_pointer_input"
    assert "no observable UI change" in result.content
    runtime.close()


def test_coordinate_click_is_not_promoted_to_uia_even_when_control_is_under_point(tmp_path):
    runtime, _, operator, session = _runtime(
        tmp_path,
        [ModelResponse(text="unused")],
        static_image=False,
    )
    tool = runtime.tools.get("computer_action")
    context = _tool_context(session, tmp_path / "project")
    tool.handler(context, {"action": {"type": "screenshot"}})

    result = tool.handler(
        context,
        {"action": {"type": "click", "point": {"x": 0.5, "y": 0.5}}},
    )

    assert result.ok is True
    assert operator.executed[-1].control_id == ""
    assert operator.executed[-1].point == ComputerPoint(0.5, 0.5)
    assert result.data["execution"]["native"] is False
    runtime.close()


def test_third_identical_no_effect_pointer_action_is_blocked_before_input(tmp_path):
    runtime, _, operator, session = _runtime(
        tmp_path,
        [ModelResponse(text="unused")],
        static_image=True,
    )
    tool = runtime.tools.get("computer_action")
    context = _tool_context(session, tmp_path / "project")
    args = {"action": {"type": "click", "point": {"x": 0.5, "y": 0.5}}}
    tool.handler(context, {"action": {"type": "screenshot"}})

    first = tool.handler(context, args)
    second = tool.handler(context, args)
    third = tool.handler(context, args)

    assert first.ok is False
    assert second.ok is False
    assert third.ok is False
    assert third.data["stuck_detected"] is True
    assert third.data["effect_reason"] == "repeated_no_effect_blocked_before_input"
    assert len(operator.executed) == 2
    runtime.close()


def test_single_loop_requires_screenshot_before_blind_input(tmp_path):
    runtime, _, operator, session = _runtime(tmp_path, [ModelResponse(text="unused")])
    tool = runtime.tools.get("computer_action")
    context = _tool_context(session, tmp_path / "project")

    result = tool.handler(
        context,
        {"action": {"type": "click", "point": {"x": 0.5, "y": 0.5}}},
    )

    assert result.ok is False
    assert result.data["effect"] == "not_executed"
    assert operator.executed == []
    assert operator.observe_count == 1
    runtime.close()


def test_computer_action_requires_approval_before_screen_capture(tmp_path):
    runtime, platform, operator, session = _runtime(
        tmp_path,
        [
            ModelResponse(
                tool_calls=(
                    ToolCall(
                        call_id="computer-screen-1",
                        name="computer_action",
                        arguments={"action": {"type": "screenshot"}},
                    ),
                )
            ),
            ModelResponse(text="done"),
        ],
        mode=PermissionMode.APPROVAL,
    )

    first = runtime.start_turn(session.session_id, "Use the desktop.")
    assert first.status is AgentStatus.WAITING_APPROVAL
    assert operator.observe_count == 0
    pending = runtime.get_session(session.session_id).pending_approval
    assert pending is not None
    assert pending.tool_name == "computer_action"

    result = runtime.resume_approval(session.session_id, "computer-screen-1", approved=True)
    assert result.status is AgentStatus.COMPLETED
    assert operator.observe_count == 1
    assert len(platform.requests) == 2
    assert any(message.uses_vision for message in platform.requests[1][1].messages)
    runtime.close()


def test_model_produced_type_text_never_crosses_durable_boundary(tmp_path):
    secret = "sk-test-super-secret-value"
    runtime, _, operator, session = _runtime(
        tmp_path,
        [
            ModelResponse(
                tool_calls=(
                    ToolCall(
                        call_id="computer-type-1",
                        name="computer_action",
                        arguments={"action": {"type": "type", "text": secret}},
                    ),
                )
            ),
            ModelResponse(text="done"),
        ],
        mode=PermissionMode.APPROVAL,
    )
    runtime.computer_sessions.observe(session.session_id)

    first = runtime.start_turn(session.session_id, "Enter the credential.")
    assert first.status is AgentStatus.WAITING_APPROVAL
    persisted = json.dumps(runtime.get_session(session.session_id).pending_approval.arguments)
    assert secret not in persisted
    assert "loom-transient-computer:" in persisted

    result = runtime.resume_approval(session.session_id, "computer-type-1", approved=True)
    assert result.status is AgentStatus.COMPLETED
    assert operator.executed[-1].text == secret
    events = "\n".join(json.dumps(event.data) for event in runtime.store.events(session.session_id))
    assert secret not in events
    runtime.close()


def test_detailed_computer_diagnostics_do_not_persist_window_or_control_text(tmp_path, monkeypatch):
    monkeypatch.setenv("LOOM_COMPUTER_LOG_DIR", str(tmp_path / "computer-logs"))
    monkeypatch.setenv("LOOM_COMPUTER_DIAGNOSTICS", "detailed")
    runtime, _, _, session = _runtime(tmp_path, [ModelResponse(text="unused")])
    tool = runtime.tools.get("computer_action")
    tool.handler(
        _tool_context(session, tmp_path / "project"),
        {"action": {"type": "screenshot"}},
    )
    events_path = tmp_path / "computer-logs" / "events.jsonl"
    text = events_path.read_text(encoding="utf-8")
    assert "private draft" not in text
    assert "Save private draft" not in text
    runtime.close()


def test_legacy_store_still_reuses_same_session_revision_for_compatibility(tmp_path):
    operator = FakeOperator()
    store = ComputerSessionStore(operator, FakeGrounder(), settle_delay=0)
    first = store.observe("owner")
    store.observe("owner")

    outcome = store.execute(
        "owner",
        first.state_revision,
        ComputerAction(type="click", point=ComputerPoint(0.5, 0.5)),
    )

    assert outcome.before.state_revision == first.state_revision
    assert outcome.verification.get("revision_autofixed") is not True


def test_unknown_session_revision_still_fails_closed(tmp_path):
    store = ComputerSessionStore(FakeOperator(), FakeGrounder(), settle_delay=0)
    with pytest.raises(RuntimeError, match="stale computer state_revision"):
        store.execute(
            "a-different-session",
            1,
            ComputerAction(type="click", point=ComputerPoint(0.5, 0.5)),
        )


def test_ui_tars_parser_remains_importable_during_legacy_retirement():
    parsed = parse_ui_tars_prediction(
        "Thought: Save is visible.\nAction: click(start_box='(250,750)')"
    )
    assert parsed.action.type is ComputerActionType.CLICK
    assert parsed.action.point == ComputerPoint(0.25, 0.75)

    drag = parse_ui_tars_prediction(
        "Thought: Move it.\nAction: drag(start_box='(100,200)', end_box='(800,900)')"
    )
    assert drag.action.type is ComputerActionType.DRAG
    assert drag.action.end_point == ComputerPoint(0.8, 0.9)
