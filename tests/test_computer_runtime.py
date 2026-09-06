from __future__ import annotations

import json

import pytest

from app.agent_runtime.computer_grounding import parse_ui_tars_prediction
from app.agent_runtime.computer_runtime import ComputerSessionStore, ComputerUseRuntime
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
from app.ai import AGENT_FAST_ROLE, ModelResponse, ToolCall


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
            title="Editor",
            rect=ComputerRect(-1280, 0, 640, 1080),
            foreground=True,
        )
        control = ComputerControl(
            control_id="uia:0",
            name="Save",
            control_type="Button",
            rect=ComputerRect(-320, 520, -220, 570),
        )
        return ComputerObservation(
            observation_id=f"obs-{self.observe_count}",
            frame=frame,
            image_png=b"PNG" + bytes([marker]),
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


def _runtime(tmp_path, responses, *, mode=PermissionMode.FULL_ACCESS, static_image=False, grounder=None):
    platform = ScriptedPlatform(responses)
    operator = FakeOperator(static_image=static_image)
    grounder = grounder or FakeGrounder()
    runtime = ComputerUseRuntime(
        platform=platform,
        store=FileAgentSessionStore(tmp_path / "state"),
        tools=loom_default_tools(),
        sandbox_manager=SandboxManager(policy=SandboxPolicy.OFF),
        web_search_provider=None,
        auto_configure_web_search=False,
        auto_configure_browser=False,
        computer_operator=operator,
        computer_grounder=grounder,
        auto_configure_computer=False,
        computer_settle_delay=0,
    )
    workspace = tmp_path / "project"
    workspace.mkdir()
    session = runtime.create_session(
        AGENT_FAST_ROLE.role_id,
        workspace_dir=workspace,
        permission_mode=mode,
    )
    return runtime, platform, operator, grounder, session


def test_default_configured_mcp_stack_composes_computer_use_without_second_agent_loop(tmp_path):
    platform = ScriptedPlatform([ModelResponse(text="done")])
    operator = FakeOperator()
    runtime = ConfiguredMCPRuntime(
        platform=platform,
        store=FileAgentSessionStore(tmp_path / "state"),
        tools=loom_default_tools(),
        sandbox_manager=SandboxManager(policy=SandboxPolicy.OFF),
        web_search_provider=None,
        auto_configure_web_search=False,
        auto_configure_browser=False,
        computer_operator=operator,
        computer_grounder=FakeGrounder(),
        auto_configure_computer=False,
        computer_settle_delay=0,
        mcp_servers=(),
    )
    assert isinstance(runtime, ComputerUseRuntime)
    assert runtime.tools.get("computer_status") is not None
    assert runtime.tools.get("computer_step") is not None
    runtime.close()
    assert operator.closed is True


def test_computer_runtime_registers_hybrid_tools_and_status(tmp_path):
    runtime, _, operator, _, session = _runtime(tmp_path, [ModelResponse(text="done")])
    names = {tool.name for tool in runtime.tools.all()}
    assert {"computer_status", "computer_observe", "computer_action", "computer_step"}.issubset(names)
    assert runtime.tools.get("computer_status").effect is ToolEffect.READ_ONLY
    assert runtime.tools.get("computer_observe").effect is ToolEffect.SENSITIVE
    status = runtime.computer_status(session.session_id)
    assert status["enabled"] is True
    assert status["operator"] == "fake-computer"
    assert status["grounder"] == "fake-grounder"
    assert status["policy_step_enabled"] is True
    runtime.close()
    assert operator.closed is True


def test_computer_step_executes_exactly_one_policy_action_and_reobserves(tmp_path):
    runtime, _, operator, grounder, session = _runtime(tmp_path, [ModelResponse(text="unused")])
    tool = runtime.tools.get("computer_step")
    result = tool.handler(
        _tool_context(session, tmp_path / "project"),
        {"instruction": "Click Save"},
    )
    assert result.ok is True
    assert len(grounder.calls) == 1
    assert len(operator.executed) == 1
    assert operator.observe_count == 2
    assert result.data["action"]["type"] == "click"
    assert result.data["verification"]["visual_changed"] is True
    assert "image_png" not in json.dumps(result.data)
    runtime.close()


def test_computer_action_rejects_stale_revision(tmp_path):
    runtime, _, _, _, session = _runtime(tmp_path, [ModelResponse(text="unused")])
    store = runtime.computer_sessions
    first = store.observe(session.session_id)
    store.observe(session.session_id)
    with pytest.raises(RuntimeError, match="stale computer state_revision"):
        store.execute(
            session.session_id,
            first.state_revision,
            ComputerAction(type="click", point=ComputerPoint(0.5, 0.5)),
        )
    runtime.close()


def test_computer_step_requires_approval_before_screen_capture(tmp_path):
    runtime, platform, operator, _, session = _runtime(
        tmp_path,
        [
            ModelResponse(
                tool_calls=(
                    ToolCall(
                        call_id="computer-step-1",
                        name="computer_step",
                        arguments={"instruction": "Click Save"},
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
    assert pending.arguments["instruction"].startswith("loom-transient-computer:")
    result = runtime.resume_approval(session.session_id, "computer-step-1", approved=True)
    assert result.status is AgentStatus.COMPLETED
    assert operator.observe_count == 2
    assert len(platform.requests) == 2
    runtime.close()


def test_model_produced_type_text_never_crosses_durable_boundary(tmp_path):
    secret = "sk-test-super-secret-value"
    runtime, _, operator, _, session = _runtime(
        tmp_path,
        [
            ModelResponse(
                tool_calls=(
                    ToolCall(
                        call_id="computer-type-1",
                        name="computer_action",
                        arguments={
                            "state_revision": 1,
                            "action": {"type": "type", "control_id": "uia:0", "text": secret},
                        },
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


def test_stuck_detection_blocks_third_identical_policy_action_on_unchanged_screen(tmp_path):
    grounder = FakeGrounder(ComputerAction(type="click", point=ComputerPoint(0.5, 0.5)))
    operator = FakeOperator(static_image=True)
    store = ComputerSessionStore(operator, grounder, settle_delay=0)
    store.step("owner", "Click Save")
    store.step("owner", "Click Save")
    with pytest.raises(RuntimeError, match="stuck detection"):
        store.step("owner", "Click Save")
    assert len(operator.executed) == 2


def test_ui_tars_parser_normalizes_coordinates_and_action_aliases():
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


def _tool_context(session, workspace):
    from app.agent_runtime.tools import ToolContext

    return ToolContext(
        session_id=session.session_id,
        turn_id="test-turn",
        workspace=workspace,
        permission_mode=session.permission_mode.value,
    )
