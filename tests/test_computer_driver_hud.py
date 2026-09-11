from __future__ import annotations

from pathlib import Path

from app.agent_runtime.computer_driver import ComputerDriverEvent
from app.agent_runtime.computer_driver_runtime import ComputerDriverRuntime, _safe_driver_data
from app.agent_runtime.contracts import AgentEventKind
from app.agent_runtime.tools import ToolContext


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


def test_driver_action_is_hud_progress_not_nested_terminal_tool(tmp_path: Path):
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

    assert len(emitted) == 1
    kind, progress = emitted[0]
    assert kind is AgentEventKind.TOOL_STARTED
    assert progress["tool"] == "computer_action"
    assert progress["nested"] is True
    assert progress["driver_progress"] is True
    assert progress["arguments"]["action"]["point"] == {"x": 0.65, "y": 0.4}
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

    # The completion stays in driver diagnostics/trace; it must not become a
    # nested TOOL_COMPLETED event because the HUD treats tool completion as
    # terminal. Only the outer computer_run_task owns task terminal lifecycle.
    assert len(emitted) == 1
    assert pending[0] is None
