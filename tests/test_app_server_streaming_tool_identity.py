from __future__ import annotations

import json
from types import SimpleNamespace

from app.agent_runtime import AgentEvent, AgentEventKind, AgentStreamEvent, AgentStreamEventKind, PermissionMode
from app.app_server_streaming import StreamingLoomAppServerService


class _RuntimeStub:
    provider_streaming_enabled = True

    def __init__(self) -> None:
        self.runtime_listener = None
        self.stream_listener = None

    def subscribe(self, listener) -> None:
        self.runtime_listener = listener

    def subscribe_stream(self, listener) -> None:
        self.stream_listener = listener


def _stream_event(*, call_id: str = "", fragment: str, tool: str = "") -> AgentStreamEvent:
    return AgentStreamEvent(
        session_id="thread-1",
        turn_id="turn-1",
        step_id="step-1",
        kind=AgentStreamEventKind.TOOL_CALL_ARGUMENT_DELTA,
        created_at="2026-09-05T00:00:00.000+00:00",
        data={
            "index": 0,
            "call_id": call_id,
            "tool": tool,
            "arguments_delta": fragment,
        },
    )


def _runtime_event(kind: AgentEventKind, *, event_id: str, data: dict[str, object]) -> AgentEvent:
    return AgentEvent(
        event_id=event_id,
        session_id="thread-1",
        turn_id="turn-1",
        kind=kind,
        created_at="2026-09-05T00:00:01.000+00:00",
        data=data,
    )


def test_streamed_tool_arguments_keep_the_durable_call_item_identity(tmp_path) -> None:
    runtime = _RuntimeStub()
    service = StreamingLoomAppServerService(
        runtime=runtime,
        store=SimpleNamespace(root=tmp_path),
        model="test-model",
        default_workspace=tmp_path,
        default_permission_mode=PermissionMode.WORKSPACE,
    )
    observed: list[tuple[str, dict[str, object]]] = []
    service.subscribe_notifications(lambda method, params: observed.append((method, params)))

    assert runtime.stream_listener is not None
    assert runtime.runtime_listener is not None

    # OpenAI-compatible providers may emit argument bytes before the chunk that
    # first carries the tool-call id. That prefix must stay buffered instead of
    # creating a provisional model-tool:<step>:<index> item.
    runtime.stream_listener(_stream_event(fragment='{"value":', tool="echo"))
    assert observed == []

    runtime.stream_listener(_stream_event(call_id="call-1", fragment='"ok"}'))
    runtime.runtime_listener(
        _runtime_event(
            AgentEventKind.TOOL_REQUESTED,
            event_id="evt-requested",
            data={
                "call_id": "call-1",
                "tool": "echo",
                "arguments": {"value": "ok"},
                "nested": False,
            },
        )
    )
    runtime.runtime_listener(
        _runtime_event(
            AgentEventKind.TOOL_STARTED,
            event_id="evt-started",
            data={"call_id": "call-1", "tool": "echo"},
        )
    )
    runtime.runtime_listener(
        _runtime_event(
            AgentEventKind.TOOL_COMPLETED,
            event_id="evt-completed",
            data={"call_id": "call-1", "tool": "echo", "result": {"value": "ok"}},
        )
    )

    started = [
        params["item"]
        for method, params in observed
        if method == "item/started" and params.get("item", {}).get("type") == "tool_call"
    ]
    assert len(started) == 1
    assert started[0]["id"] == "tool:call-1"
    assert started[0]["callId"] == "call-1"

    argument_deltas = [
        params
        for method, params in observed
        if method == "item/delta" and params.get("delta", {}).get("kind") == "tool_call_argument"
    ]
    assert [params["delta"]["arguments"] for params in argument_deltas] == [
        '{"value":',
        '"ok"}',
    ]
    assert {params["itemId"] for params in argument_deltas} == {"tool:call-1"}

    lifecycle_deltas = [
        params
        for method, params in observed
        if method == "item/delta" and "status" in params.get("delta", {})
    ]
    assert lifecycle_deltas
    assert {params["itemId"] for params in lifecycle_deltas} == {"tool:call-1"}

    completed = [
        params["item"]
        for method, params in observed
        if method == "item/completed" and params.get("item", {}).get("type") == "tool_call"
    ]
    assert len(completed) == 1
    assert completed[0]["id"] == "tool:call-1"
    assert completed[0]["callId"] == "call-1"

    assert "model-tool:" not in json.dumps(observed, ensure_ascii=False)
