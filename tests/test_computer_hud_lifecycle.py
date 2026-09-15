from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from app.agent_runtime import AgentEventKind
from app.app_server_reasoning import ReasoningManagedLoomAppServerService


def _event(
    kind: AgentEventKind,
    *,
    tool: str = "",
    event_id: str = "event-1",
):
    data: dict[str, object] = {}
    if tool:
        data = {
            "tool": tool,
            "call_id": "call-1",
            "arguments": {
                "action": {
                    "type": "click",
                    "point": {"x": 0.5, "y": 0.5},
                }
            },
        }
    return SimpleNamespace(
        kind=kind,
        data=data,
        session_id="session-1",
        turn_id="turn-1",
        event_id=event_id,
    )


def _hud_probe() -> tuple[ReasoningManagedLoomAppServerService, list[tuple[str, dict[str, object]]]]:
    service = object.__new__(ReasoningManagedLoomAppServerService)
    notifications: list[tuple[str, dict[str, object]]] = []
    service._notify = lambda method, payload: notifications.append((method, payload))
    return service, notifications


def test_single_loop_computer_action_keeps_hud_visible_until_turn_finishes():
    service, notifications = _hud_probe()

    service._emit_automation_hud(
        _event(AgentEventKind.TOOL_COMPLETED, tool="computer_action")
    )

    method, action_payload = notifications[-1]
    assert method == "hud/update"
    assert action_payload["visible"] is True
    assert action_payload["terminal"] is False
    assert action_payload["phase"] == 4
    assert action_payload["title"] == "Loom 正在继续 Computer Use"

    service._emit_automation_hud(
        _event(AgentEventKind.TURN_COMPLETED, event_id="event-2")
    )

    method, turn_payload = notifications[-1]
    assert method == "hud/update"
    assert turn_payload["visible"] is False
    assert turn_payload["terminal"] is True


def test_single_loop_computer_failure_stays_visible_for_replanning():
    service, notifications = _hud_probe()
    event = _event(AgentEventKind.TOOL_FAILED, tool="computer_action")
    event.data["content"] = "pointer had no observable effect"

    service._emit_automation_hud(event)

    _, payload = notifications[-1]
    assert payload["visible"] is True
    assert payload["terminal"] is False
    assert payload["phase"] == 4
    assert payload["title"] == "Loom 正在恢复 Computer Use"


def test_browser_tool_completion_keeps_existing_per_tool_terminal_semantics():
    service, notifications = _hud_probe()

    service._emit_automation_hud(
        _event(AgentEventKind.TOOL_COMPLETED, tool="browser_click")
    )

    _, payload = notifications[-1]
    assert payload["visible"] is True
    assert payload["terminal"] is True


def test_electron_hud_only_hides_for_explicit_terminal_and_reuses_window():
    source = (
        Path(__file__).resolve().parents[1]
        / "desktop-react"
        / "electron"
        / "hudWindow.ts"
    ).read_text(encoding="utf-8")

    assert "return payload.terminal === true;" in source
    assert "asNumber(payload.phase, 0) >= 4" not in source
    assert "DESTROY_AFTER_IDLE_MS" not in source
    assert "scheduleDestroy" not in source
    assert "if (terminalPayload(payload)) hideHudWindow(2300);" in source
