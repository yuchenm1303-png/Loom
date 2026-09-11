from __future__ import annotations

from pathlib import Path

from app.agent_runtime.computer_driver import ComputerDriverEvent
from app.agent_runtime.computer_driver_runtime import ComputerDriverRuntime, _safe_driver_data
from app.agent_runtime.contracts import AgentEvent, AgentEventKind
from app.agent_runtime.tools import ToolContext
from app.app_server_reasoning import ReasoningManagedLoomAppServerService


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


def test_driver_nested_action_has_distinct_tool_identity_and_continuous_hud(tmp_path: Path):
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

    requested = emitted[0][1]
    completed = emitted[-1][1]
    assert requested["tool"] == "computer_driver_action"
    assert requested["hud_continuous"] is True
    assert completed["tool"] == "computer_driver_action"
    assert completed["hud_continuous"] is True
    assert pending[0] is None


def test_nested_ufo_action_completion_does_not_terminally_hide_hud():
    service = object.__new__(ReasoningManagedLoomAppServerService)
    notifications: list[tuple[str, dict[str, object]]] = []
    service._notify = lambda method, payload: notifications.append((method, payload))  # type: ignore[attr-defined]

    event = AgentEvent(
        event_id="event-1",
        session_id="session-1",
        turn_id="turn-1",
        kind=AgentEventKind.TOOL_COMPLETED,
        created_at="2026-09-11T00:00:00Z",
        data={
            "call_id": "driver:task-1:1",
            "tool": "computer_driver_action",
            "nested": True,
            "hud_continuous": True,
            "data": {"driver": "ufo2-sidecar"},
        },
    )

    service._emit_automation_hud(event)

    assert len(notifications) == 1
    method, payload = notifications[0]
    assert method == "hud/update"
    assert payload["visible"] is True
    assert payload["terminal"] is False
    assert payload["phase"] == 2
    assert payload["actionSource"] == "Microsoft UFO² + 目标窗口 UIA/视觉坐标"
