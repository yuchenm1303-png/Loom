"""End-to-end cover for the model-facing Computer Use path.

Every other Computer Use test drives either the driver or a tool handler
directly. That leaves the seam the product actually depends on untested: a
model emits a ``computer_run_task`` call, Runtime v2 persists it, the transient
boundary rewrites it, and only then does a handler resolve it back and hand it
to the driver. A break anywhere in that chain is invisible to driver-level
tests and total in the app, so it is exercised here through ``start_turn``.
"""

from __future__ import annotations

import json

from app.agent_runtime.computer_driver import ComputerDriverEvent, ComputerDriverResult
from app.agent_runtime.computer_driver_runtime import ComputerDriverRuntime
from app.agent_runtime.contracts import AgentStatus, PermissionMode
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


class FakeDriver:
    name = "fake-ufo"

    def __init__(self, *, ready: bool = True, reason: str = ""):
        self._ready = ready
        self._reason = reason
        self.tasks: list[tuple[str, str, int | None]] = []
        self.closed = False

    def status(self):
        status = {"name": self.name, "ready": self._ready, "engine": "ufo2"}
        if not self._ready:
            status["reason"] = self._reason
        return status

    def run_task(self, task, *, stop_when="", max_steps=None, on_event=None, is_cancelled=None):
        self.tasks.append((task, stop_when, max_steps))
        if on_event is not None:
            on_event(
                ComputerDriverEvent(
                    task_id="task-1",
                    sequence=1,
                    kind="action.started",
                    data={
                        "action": "click_input",
                        "window": {"name": "Notepad"},
                        "hud_point": {"x_norm": 0.4, "y_norm": 0.6},
                    },
                )
            )
            on_event(
                ComputerDriverEvent(
                    task_id="task-1",
                    sequence=2,
                    kind="action.completed",
                    data={
                        "action": "click_input",
                        "result": {"ok": True, "status": "success"},
                        "window": {"name": "Notepad"},
                    },
                )
            )
        return ComputerDriverResult(
            task_id="task-1",
            status="FINISH",
            ok=True,
            summary="typed the note",
            data={"engine": "ufo2", "steps_used": 2},
        )

    def pause(self):
        return True

    def resume(self):
        return True

    def cancel(self, *, reason="user_requested"):
        return True

    def close(self):
        self.closed = True


def _runtime(tmp_path, responses, *, driver, mode="ufo"):
    platform = ScriptedPlatform(responses)
    runtime = ComputerDriverRuntime(
        platform=platform,
        store=FileAgentSessionStore(tmp_path / "state"),
        tools=loom_default_tools(),
        sandbox_manager=SandboxManager(policy=SandboxPolicy.OFF),
        web_search_provider=None,
        auto_configure_web_search=False,
        auto_configure_browser=False,
        auto_configure_computer=False,
        computer_settle_delay=0,
        computer_driver=driver,
        computer_driver_mode=mode,
    )
    workspace = tmp_path / "project"
    workspace.mkdir()
    session = runtime.create_session(
        AGENT_FAST_ROLE.role_id,
        workspace_dir=workspace,
        permission_mode=PermissionMode.FULL_ACCESS,
    )
    return runtime, platform, session


def _run_task_responses(task: str, stop_when: str = ""):
    arguments: dict[str, object] = {"task": task}
    if stop_when:
        arguments["stop_when"] = stop_when
    return [
        ModelResponse(
            tool_calls=(
                ToolCall(call_id="run-task-1", name="computer_run_task", arguments=arguments),
            )
        ),
        ModelResponse(text="desktop task finished"),
    ]


def test_model_tool_call_reaches_the_driver_with_resolved_task_text(tmp_path):
    task = "Open Notepad and type the release note"
    driver = FakeDriver()
    runtime, platform, session = _runtime(
        tmp_path, _run_task_responses(task, "the note is visible"), driver=driver
    )

    result = runtime.start_turn(session.session_id, "Do the desktop work.")

    assert result.status is AgentStatus.COMPLETED
    assert result.final_text == "desktop task finished"
    # The driver must receive the real instruction, not the transient handle the
    # model's arguments were rewritten into.
    assert driver.tasks == [(task, "the note is visible", None)]
    assert len(platform.requests) == 2
    runtime.close()
    assert driver.closed is True


def test_run_task_is_exposed_to_the_model_and_its_result_is_fed_back(tmp_path):
    driver = FakeDriver()
    runtime, platform, session = _runtime(
        tmp_path, _run_task_responses("Open Notepad"), driver=driver
    )

    offered = {
        tool.name for tool in runtime.tools.router(capability_settings={}).all()
    }
    assert "computer_run_task" in offered

    runtime.start_turn(session.session_id, "Do the desktop work.")

    # The second request is the one that carries the tool result back, which is
    # how the outer model learns whether the desktop task actually succeeded.
    _, follow_up = platform.requests[1]
    transcript = json.dumps([
        {"role": message.role.value, "content": str(message.content or "")}
        for message in follow_up.messages
    ])
    assert "typed the note" in transcript
    runtime.close()


def test_task_text_never_crosses_the_durable_boundary(tmp_path):
    secret = "passphrase hunter2 for the release console"
    driver = FakeDriver()
    runtime, _, session = _runtime(tmp_path, _run_task_responses(secret), driver=driver)

    runtime.start_turn(session.session_id, "Do the desktop work.")

    assert driver.tasks[0][0] == secret
    persisted = json.dumps([
        {
            "content": str(message.content or ""),
            "tool_calls": [
                {"name": call.name, "arguments": call.arguments}
                for call in (message.tool_calls or ())
            ],
        }
        for message in runtime.get_session(session.session_id).messages
    ])
    assert secret not in persisted
    assert "loom-transient-computer:" in persisted
    events = "\n".join(
        json.dumps(event.data) for event in runtime.store.events(session.session_id)
    )
    assert secret not in events
    runtime.close()


def test_driver_actions_surface_as_nested_transcript_events(tmp_path):
    driver = FakeDriver()
    runtime, _, session = _runtime(
        tmp_path, _run_task_responses("Open Notepad"), driver=driver
    )

    runtime.start_turn(session.session_id, "Do the desktop work.")

    nested = [
        event
        for event in runtime.store.events(session.session_id)
        if bool(event.data.get("driver_progress"))
    ]
    # The HUD only reacts to computer_* tool identities, so the opening event of
    # each driver action has to carry one.
    assert nested, "driver actions produced no transcript events"
    assert nested[0].data["tool"] == "computer_action"
    assert nested[0].data["arguments"]["action"]["point"] == {"x": 0.4, "y": 0.6}
    call_ids = {str(event.data.get("call_id") or "") for event in nested}
    assert len(call_ids) == 1
    runtime.close()


def test_unready_driver_reports_one_clear_reason_instead_of_failing_the_turn(tmp_path):
    driver = FakeDriver(ready=False, reason="UFO sidecar is not provisioned")
    runtime, _, session = _runtime(
        tmp_path, _run_task_responses("Open Notepad"), driver=driver
    )

    result = runtime.start_turn(session.session_id, "Do the desktop work.")

    assert result.status is AgentStatus.COMPLETED
    assert driver.tasks == []
    failures = [
        event
        for event in runtime.store.events(session.session_id)
        if event.data.get("tool") == "computer_run_task" and event.data.get("ok") is False
    ]
    assert failures
    assert "UFO sidecar is not provisioned" in str(failures[-1].data.get("content") or "")
    runtime.close()
