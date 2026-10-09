"""Opening a long thread must not parse every model request the conversation ever made.

``thread/read`` for the transcript and ``thread/context`` for the meter run on every
thread switch. The model request payloads are most of a long log and neither reads them.
"""
from __future__ import annotations

from pathlib import Path

from test_app_server_context_budget import _RECORDED_REQUEST, _build_service

from app.agent_runtime import AgentEventKind as E

BODY = "REQUEST-BODY-SENTINEL"


def _seed(service, runtime, workspace: Path) -> str:
    thread_id = service.thread_start(
        {"workspace": str(workspace), "permissionMode": "workspace", "title": "Open reads"}
    )["thread"]["id"]
    session = runtime.store.load(thread_id)
    layout = {"sections": [{"role": "user", "name": "history", "estimated_tokens": n} for n in range(40)], "note": BODY}
    for number in range(1, 5):
        session.current_turn_id = f"turn-{number}"
        runtime._record(session, E.TURN_STARTED, data={"source": "user"})
        runtime._record(session, E.USER_MESSAGE, data={"text": f"question {number}"})
        runtime._record(session, E.MODEL_REQUESTED, data={**_RECORDED_REQUEST, "request_layout": layout})
        runtime._record(session, E.MODEL_RESPONSE, data={"text": f"working {number}", "usage": {"total_tokens": 5}, "step_id": f"s{number}"})
        runtime._record(session, E.TOOL_REQUESTED, data={"call_id": f"c{number}", "tool": "read", "arguments": {"path": "a"}})
        runtime._record(session, E.TOOL_COMPLETED, data={"call_id": f"c{number}", "tool": "read", "ok": True, "content": "body"})
        runtime._record(session, E.MODEL_REQUESTED, data={**_RECORDED_REQUEST, "request_layout": layout, "message_count": 9 + number})
        runtime._record(session, E.TURN_COMPLETED, data={"text": f"answer {number}", "final_step_id": f"s{number}"})
    return thread_id


class _Spy:
    """Delegates to a real store and notes which read each caller asks for."""

    def __init__(self, inner, *, hide=()):
        self._inner, self._hide, self.calls = inner, set(hide), []

    def __getattr__(self, name):
        if name in self._hide:
            raise AttributeError(name)
        value = getattr(self._inner, name)
        if name in {"events", "presentation_events", "context_state"} and callable(value):
            def recorded(*args, **kwargs):
                self.calls.append(name)
                return value(*args, **kwargs)
            return recorded
        return value


def test_the_transcript_read_matches_the_full_log_read_and_skips_request_payloads(tmp_path):
    service, runtime, workspace = _build_service(tmp_path, [])
    try:
        thread_id = _seed(service, runtime, workspace)
        params = {"threadId": thread_id, "presentationOnly": True, "turnLimit": 2}
        spy = _Spy(runtime.store)
        service.store = spy
        light = service.thread_read(params)
        older = service.thread_read({**params, "beforeTurnId": light["oldestTurnId"]})
        assert spy.calls == ["presentation_events", "presentation_events"]

        service.store = runtime.store
        service._transcript_events = lambda session_id: runtime.store.events(session_id)
        assert light == service.thread_read(params)
        assert older == service.thread_read({**params, "beforeTurnId": light["oldestTurnId"]})
        assert [turn["id"] for turn in light["turns"]] == ["turn-3", "turn-4"] and light["hasMoreTurns"] is True
        assert [turn["id"] for turn in older["turns"]] == ["turn-1", "turn-2"] and older["hasMoreTurns"] is False
        assert light["finalText"] == "answer 4"
    finally:
        runtime.close()


def test_the_diagnostic_read_still_returns_every_event_in_full(tmp_path):
    service, runtime, workspace = _build_service(tmp_path, [])
    try:
        thread_id = _seed(service, runtime, workspace)
        spy = _Spy(runtime.store)
        service.store = spy
        result = service.thread_read({"threadId": thread_id})
        assert spy.calls == ["events"]
        requests = [event for event in result["events"] if event["kind"] == "model_requested"]
        assert len(requests) == 8 and all(event["data"]["request_layout"]["note"] == BODY for event in requests)
    finally:
        runtime.close()


def test_the_context_report_reads_checkpoints_and_the_newest_request_only(tmp_path):
    service, runtime, workspace = _build_service(tmp_path, [])
    try:
        thread_id = _seed(service, runtime, workspace)
        session = runtime.store.load(thread_id)
        runtime._record(session, E.CONTEXT_CHECKPOINTED, data={"checkpoint_id": "one", "replacement_estimated_tokens": 900})
        session.current_turn_id = "turn-5"
        runtime._record(session, E.TURN_STARTED, data={"source": "user"})
        runtime._record(session, E.MODEL_REQUESTED, data={**_RECORDED_REQUEST, "message_count": 77})

        spy = _Spy(runtime.store)
        service.store = spy
        report = service.thread_context({"threadId": thread_id})["context"]

        assert spy.calls == ["context_state"]
        assert report["compactions"] == 1 and report["messageCount"] == 77
        assert report["usedTokens"] == _RECORDED_REQUEST["calibrated_input_tokens_after"]
    finally:
        runtime.close()


def test_a_store_without_the_lighter_reads_gives_the_same_answers(tmp_path):
    service, runtime, workspace = _build_service(tmp_path, [])
    try:
        thread_id = _seed(service, runtime, workspace)
        session = runtime.store.load(thread_id)
        runtime._record(session, E.CONTEXT_CHECKPOINTED, data={"checkpoint_id": "one", "replacement_estimated_tokens": 900})

        params = {"threadId": thread_id, "presentationOnly": True, "turnLimit": 3}
        read, context = service.thread_read(params), service.thread_context({"threadId": thread_id})

        plain = _Spy(runtime.store, hide=("presentation_events", "context_state", "checkpoint_events"))
        service.store = plain
        assert service.thread_read(params) == read
        assert service.thread_context({"threadId": thread_id}) == context
        checkpoint = [event for event in plain.events(thread_id) if event.kind is E.CONTEXT_CHECKPOINTED][-1]
        assert service._context_history(thread_id) == (1, checkpoint.created_at)
        assert plain.calls.count("events") >= 3
    finally:
        runtime.close()


def test_insights_aggregate_the_same_numbers_without_parsing_request_payloads(tmp_path):
    service, runtime, workspace = _build_service(tmp_path, [])
    try:
        thread_id = _seed(service, runtime, workspace)
        session = runtime.store.load(thread_id)
        session.model = "test-model"
        runtime.store.save(session)

        spy = _Spy(runtime.store)
        service.store = spy
        light = service.profile_insights({"days": 90})
        assert "events" not in spy.calls and "presentation_events" in spy.calls

        service.store = runtime.store
        service._transcript_events = lambda session_id: runtime.store.events(session_id)
        full = service.profile_insights({"days": 90})
        assert light["totals"]["modelCalls"] == 4 and light["totals"]["turns"] == 4
        assert {**light, "generatedAt": ""} == {**full, "generatedAt": ""}
    finally:
        runtime.close()


def test_answering_an_approval_reads_the_lighter_log(tmp_path):
    from test_app_server import _build_service as build_tool_service, _wait_until
    from app.agent_runtime import AgentTool, PermissionMode, ToolEffect, ToolResult
    from app.ai import ModelResponse, ToolCall

    tool = AgentTool(
        name="sensitive_test_tool",
        description="A sensitive test action.",
        input_schema={"type": "object", "properties": {}, "additionalProperties": False},
        handler=lambda _context, _arguments: ToolResult(ok=True, content="ran"),
        effect=ToolEffect.SENSITIVE,
    )
    service, runtime, store, _platform, workspace = build_tool_service(
        tmp_path,
        [ModelResponse(tool_calls=(ToolCall(call_id="call-1", name="sensitive_test_tool", arguments={}),)), ModelResponse(text="done")],
        tools=(tool,),
        permission_mode=PermissionMode.APPROVAL,
    )
    try:
        thread_id = service.thread_start({"workspace": str(workspace), "permissionMode": "approval"})["thread"]["id"]
        service.turn_start({"threadId": thread_id, "input": "go"})
        pending = _wait_until(lambda: service.thread_read({"threadId": thread_id})["pendingApproval"])

        spy = _Spy(store)
        service.store = spy
        service.approval_respond({"threadId": thread_id, "turnId": pending["turnId"], "requestId": pending["requestId"],
                                  "callId": "call-1", "decision": "accept"})
        assert "events" not in spy.calls and "presentation_events" in spy.calls
        _wait_until(lambda: thread_id not in service.runtime_status()["activeThreadIds"])
    finally:
        runtime.close()
