"""Typed text must survive the session-scoped model platform.

From a live run (2026-09-19, session fd8b584f): every attempt to type into a
WeChat search box failed, and the model looped - announcing the same "now type
it" step over and over while nothing reached the desktop. No type action ever
appears in the Computer Use event log for that turn, because it never got that
far. The tool returned:

    computer transient input is no longer available; retry the Computer Use
    action instead of recovering typed data from durable state

Typed text never crosses durable state: the platform stashes it in RAM and
substitutes a one-shot handle, and the tool handler redeems that handle. But a
session that picked its own model has its own platform wrapper, with its own
stash. Redeeming without the session id looks in the default platform, which
never held the handle - so every type action failed, every time, for any
session with a per-session model. Which is every real session.

Every other Computer Use tool passed the session id. The single-loop
``computer_action`` handler, the one actually shipped, did not.
"""

from __future__ import annotations

from app.agent_runtime.computer_single_loop_runtime import SingleLoopComputerRuntime
from app.agent_runtime.computer_types import (
    ComputerAction,
    ComputerActionType,
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


SECRET = "ClawBot"


class _Platform:
    """A model platform, as distinct from any other instance of one."""

    def __init__(self, label: str) -> None:
        self.label = label

    def execute_chat(self, profile_id, request):
        return ModelResponse(
            text="",
            tool_calls=(
                ToolCall(
                    call_id="call_1",
                    name="computer_action",
                    arguments={"action": {"type": "type", "text": SECRET}},
                ),
            ),
        )


class _TypingOperator:
    name = "typing"

    def __init__(self) -> None:
        self.typed: list[str] = []
        self.actions: list[ComputerAction] = []
        self.count = 0
        self.closed = False

    def status(self):
        return {"backend": self.name}

    def observe(self):
        return self.observe_layered()

    def observe_layered(self, *, semantics="best_effort", deadline_ms=0.0):
        self.count += 1
        window = ComputerWindow(
            window_id="0x1",
            title="Chat",
            process_name="chat.exe",
            rect=ComputerRect(0, 0, 800, 600),
            foreground=True,
        )
        return ComputerObservation(
            observation_id=f"obs-{self.count}",
            frame=ComputerFrame(frame_id=f"f-{self.count}", origin_x=0, origin_y=0, width=800, height=600),
            image_data=bytes([self.count % 251]) * 64,
            active_window=window,
            windows=(window,),
            controls=(),
        )

    def execute(self, action: ComputerAction, observation):
        self.actions.append(action)
        self.typed.append(action.text)
        return ComputerExecution(ok=True, message="typed", action=action, native=False)

    def close(self):
        self.closed = True


def _runtime(tmp_path):
    operator = _TypingOperator()
    runtime = SingleLoopComputerRuntime(
        platform=_Platform("default"),
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
    workspace = tmp_path / "project"
    workspace.mkdir()
    session = runtime.create_session(
        AGENT_FAST_ROLE.role_id,
        workspace_dir=workspace,
        permission_mode=PermissionMode.FULL_ACCESS,
    )
    return runtime, operator, session, workspace


def _stash_through(runtime, session_id: str, text: str) -> str:
    """Run one model response through the session's platform, as a turn does."""

    platform = runtime.platform_for_session(session_id)
    response = platform.execute_chat("profile", object())
    handle = response.tool_calls[0].arguments["action"]["text"]
    assert handle != text, "the platform must not pass raw typed text through durable state"
    return handle


def test_typing_works_when_the_session_has_its_own_model(tmp_path):
    """The regression. A per-session model is the normal case, not an edge case."""

    runtime, operator, session, workspace = _runtime(tmp_path)
    try:
        # Selecting a model for this session gives it its own platform wrapper,
        # which is where the typed text will be stashed.
        runtime.set_session_model(session.session_id, _Platform("session"))

        handle = _stash_through(runtime, session.session_id, SECRET)

        context = ToolContext(
            session_id=session.session_id,
            turn_id="turn-1",
            workspace=workspace,
            permission_mode=session.permission_mode.value,
        )
        runtime.computer_sessions.observe(session.session_id)

        result = runtime._handle_single_action(
            context, {"action": {"type": "type", "text": handle}}
        )

        assert result.ok is True, result.content
        assert operator.typed == [SECRET], "the text the model asked for must reach the desktop"
    finally:
        runtime.close()


def test_typing_still_works_without_a_session_scoped_model(tmp_path):
    """The default platform path must keep working too."""

    runtime, operator, session, workspace = _runtime(tmp_path)
    try:
        handle = _stash_through(runtime, session.session_id, SECRET)

        context = ToolContext(
            session_id=session.session_id,
            turn_id="turn-1",
            workspace=workspace,
            permission_mode=session.permission_mode.value,
        )
        runtime.computer_sessions.observe(session.session_id)

        result = runtime._handle_single_action(
            context, {"action": {"type": "type", "text": handle}}
        )

        assert result.ok is True, result.content
        assert operator.typed == [SECRET]
        assert result.data["verification"]["semantic_verified"] is False
        assert "not verified" in result.content
    finally:
        runtime.close()


def test_common_click_button_right_dialect_is_normalized(tmp_path):
    runtime, operator, session, workspace = _runtime(tmp_path)
    try:
        context = ToolContext(
            session_id=session.session_id,
            turn_id="turn-1",
            workspace=workspace,
            permission_mode=session.permission_mode.value,
        )
        runtime.computer_sessions.observe(session.session_id)

        result = runtime._handle_single_action(
            context,
            {"action": {"type": "click", "button": "right", "point": {"x": 0.5, "y": 0.5}}},
        )

        assert result.ok is True
        assert operator.actions[-1].type is ComputerActionType.RIGHT_CLICK
    finally:
        runtime.close()
