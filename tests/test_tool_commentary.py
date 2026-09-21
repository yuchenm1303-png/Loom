from __future__ import annotations

from app.ai import ToolCall
from app.agent_runtime.contracts import AgentEvent, AgentEventKind
from app.agent_runtime.tool_commentary import (
    runtime_tool_commentary,
    should_emit_runtime_tool_commentary,
)


def _call(name: str, arguments=None) -> ToolCall:
    return ToolCall(call_id=f"call-{name}", name=name, arguments=arguments or {})


def _event(kind: AgentEventKind, **data) -> AgentEvent:
    return AgentEvent(
        event_id=f"event-{kind.value}-{len(data)}",
        session_id="session-1",
        turn_id="turn-1",
        kind=kind,
        created_at="2026-01-01T00:00:00Z",
        data=data,
    )


def test_runtime_tool_commentary_is_language_aware_and_never_reflects_arguments():
    secret = "super-secret-password"
    text = runtime_tool_commentary(
        [_call("exec", {"cmd": f"ssh user:{secret}@host"})],
        communication_language="zh",
        continuing=False,
    )

    assert text.startswith("我先")
    assert "命令结果" in text
    assert secret not in text


def test_runtime_tool_commentary_describes_continuation_without_tool_jargon():
    text = runtime_tool_commentary(
        [_call("read_workspace_text")],
        communication_language="latin",
        continuing=True,
    )

    assert text.startswith("I'll continue")
    assert "read_workspace_text" not in text


def test_first_silent_tool_batch_gets_visible_commentary():
    assert should_emit_runtime_tool_commentary((), turn_id="turn-1") is True


def test_recent_meaningful_commentary_suppresses_template_spam():
    events = (
        _event(AgentEventKind.MODEL_RESPONSE, text="I found the package source."),
        _event(AgentEventKind.TOOL_STARTED, tool="web_search"),
        _event(AgentEventKind.TOOL_COMPLETED, tool="web_search"),
    )

    assert should_emit_runtime_tool_commentary(events, turn_id="turn-1") is False


def test_runtime_commentary_returns_after_bounded_silent_tool_batch():
    events = [_event(AgentEventKind.MODEL_RESPONSE, text="Checking the available channels.")]
    for index in range(8):
        events.append(_event(AgentEventKind.TOOL_STARTED, tool=f"tool-{index}"))
        events.append(_event(AgentEventKind.TOOL_COMPLETED, tool=f"tool-{index}"))

    assert should_emit_runtime_tool_commentary(events, turn_id="turn-1") is True


def test_other_turn_events_do_not_suppress_first_commentary():
    event = AgentEvent(
        event_id="other",
        session_id="session-1",
        turn_id="turn-other",
        kind=AgentEventKind.MODEL_RESPONSE,
        created_at="2026-01-01T00:00:00Z",
        data={"text": "unrelated"},
    )

    assert should_emit_runtime_tool_commentary((event,), turn_id="turn-1") is True
