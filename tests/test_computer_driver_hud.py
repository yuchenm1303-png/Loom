from __future__ import annotations

from pathlib import Path

from app.agent_runtime.computer_driver import ComputerDriverEvent
from app.agent_runtime.computer_driver_runtime import ComputerDriverRuntime, _safe_driver_data
from app.agent_runtime.contracts import AgentEventKind
from app.agent_runtime.tools import (
    AgentTool,
    ToolContext,
    ToolExposure,
    ToolRegistry,
    ToolResult,
)


def _stub_tool(name: str) -> AgentTool:
    return AgentTool(
        name=name,
        description=f"stub {name}",
        input_schema={"type": "object", "properties": {}, "additionalProperties": False},
        handler=lambda _context, _arguments: ToolResult(True, "ok", {}),
    )


def test_driver_result_redacts_stderr_and_provider_payloads():
    safe = _safe_driver_data(
        {
            "engine": "ufo2",
            "stderr_tail": "secret task text from provider stderr",
            "error": "provider echoed the user request",
            "preview": "raw provider response",
            "traceback": "raw traceback with task text",
            "nested": {"text": "typed secret", "status": "failed"},
        }
    )

    assert safe["stderr_tail"] == "[REDACTED_DRIVER_DATA]"
    assert safe["error"] == "[REDACTED_DRIVER_DATA]"
    assert safe["preview"] == "[REDACTED_DRIVER_DATA]"
    assert safe["traceback"] == "[REDACTED_DRIVER_DATA]"
    assert safe["nested"]["text"] == "[REDACTED_DRIVER_DATA]"
    assert safe["nested"]["status"] == "failed"
    assert "secret task text" not in repr(safe)
    assert "typed secret" not in repr(safe)


def test_mature_driver_keeps_low_level_computer_tools_deferred():
    runtime = object.__new__(ComputerDriverRuntime)
    runtime.computer_driver_mode = "ufo"
    runtime.computer_driver = object()
    runtime.tools = ToolRegistry(
        tuple(
            _stub_tool(name)
            for name in (
                "computer_status",
                "computer_observe",
                "computer_action",
                "computer_step",
                "computer_run_task",
            )
        )
    )

    runtime._install_driver_task_tool()

    direct_names = {
        tool.name
        for tool in runtime.tools.router(capability_settings={}).all()
    }
    deferred = {
        tool.name: tool.exposure
        for tool in runtime.tools.deferred(capability_settings={})
    }
    assert "computer_status" in direct_names
    assert "computer_run_task" in direct_names
    assert "computer_observe" not in direct_names
    assert "computer_action" not in direct_names
    assert "computer_step" not in direct_names
    assert deferred == {
        "computer_action": ToolExposure.DEFERRED,
        "computer_observe": ToolExposure.DEFERRED,
        "computer_step": ToolExposure.DEFERRED,
    }


def test_legacy_mode_preserves_direct_low_level_computer_tools():
    runtime = object.__new__(ComputerDriverRuntime)
    runtime.computer_driver_mode = "legacy"
    runtime.computer_driver = None
    runtime.tools = ToolRegistry(
        tuple(
            _stub_tool(name)
            for name in (
                "computer_status",
                "computer_observe",
                "computer_action",
                "computer_step",
                "computer_run_task",
            )
        )
    )

    runtime._install_driver_task_tool()

    direct_names = {
        tool.name
        for tool in runtime.tools.router(capability_settings={}).all()
    }
    assert {
        "computer_status",
        "computer_observe",
        "computer_action",
        "computer_step",
        "computer_run_task",
    }.issubset(direct_names)


def test_driver_action_has_balanced_transcript_lifecycle_without_terminal_hud_identity(tmp_path: Path):
    emitted: list[tuple[AgentEventKind, dict[str, object]]] = []
    context = ToolContext(
        session_id="session-1",
        turn_id="turn-1",
        workspace=tmp_path,
        emit_event=lambda kind, data: emitted.append((kind, data)),
    )
    pending: list[str | None] = [None]

    ComputerDriverRuntime._emit_action_event(
        context,
        ComputerDriverEvent(
            task_id="task-1",
            sequence=1,
            kind="action.started",
            data={
                "action": "click_on_coordinates",
                "parameters": {"x": 0.5, "y": 0.25},
                "window": {"name": "WeChat"},
                "hud_point": {"x_norm": 0.65, "y_norm": 0.4},
            },
        ),
        pending,
    )

    assert [kind for kind, _ in emitted] == [
        AgentEventKind.TOOL_REQUESTED,
        AgentEventKind.TOOL_STARTED,
    ]
    requested = emitted[0][1]
    started = emitted[1][1]
    assert requested["tool"] == "computer_action"
    assert started["tool"] == "computer_action"
    assert requested["call_id"] == started["call_id"]
    assert requested["nested"] is True
    assert requested["driver_progress"] is True
    assert requested["arguments"]["action"]["point"] == {"x": 0.65, "y": 0.4}
    assert pending[0] is not None

    ComputerDriverRuntime._emit_action_event(
        context,
        ComputerDriverEvent(
            task_id="task-1",
            sequence=2,
            kind="action.completed",
            data={
                "action": "click_on_coordinates",
                "result": {"ok": True, "status": "success"},
                "window": {"name": "WeChat"},
            },
        ),
        pending,
    )

    assert len(emitted) == 3
    kind, completed = emitted[-1]
    assert kind is AgentEventKind.TOOL_COMPLETED
    assert completed["call_id"] == requested["call_id"]
    # app_server.py closes tool transcript items by call_id, so the completion
    # can use a non-computer identity. The HUD ignores it because only
    # computer_* tool names drive Computer Use presentation.
    assert completed["tool"] == "driver_action_result"
    assert completed["driver_progress"] is True
    assert pending[0] is None
