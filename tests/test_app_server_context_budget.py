from __future__ import annotations

import time
from pathlib import Path

import pytest

from app.agent_runtime import (
    AgentEventKind,
    DurableAgentRuntime,
    FileAgentSessionStore,
    PermissionMode,
    ToolRegistry,
)
from app.agent_runtime.context_report import context_report_from_request, empty_context_report
from app.app_server_thread_management import ManagedStreamingLoomAppServerService
from app.ai import AIMessage, MessageRole, ModelResponse


# A real request payload from a 64k-window session, trimmed to the fields the
# report reads. Keeping a recorded shape rather than an invented one is the
# point: the meter must agree with what the runtime actually sent.
_RECORDED_REQUEST = {
    "message_count": 46,
    "calibrated_input_tokens_after": 45_372,
    "active_context_tokens": 48_267,
    "token_accounting_source": "provider_usage",
    "tool_schema_tokens": 7_994,
    "tool_outputs_reduced": 5,
    "tool_outputs_collapsed": 19,
    "user_messages_truncated": 0,
    "tool_schema_plan": {
        "mode": "compact",
        "original_schema_tokens": 10_657,
        "planned_schema_tokens": 8_205,
        "omitted_count": 0,
        "omitted_names": [],
    },
    "context_limits": {
        "context_window_tokens": 65_536,
        "effective_context_window_tokens": 58_982,
        "input_budget_tokens": 50_790,
        "output_reserve_tokens": 8_192,
        "auto_compact_token_limit": 49_152,
        "tool_output_token_limit": 4_000,
        "source": "model_profile",
        "window_known": True,
    },
}


def _build_service(tmp_path: Path, responses):
    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = FileAgentSessionStore(home)

    class _Platform:
        def __init__(self) -> None:
            self.responses = list(responses)

        def execute_chat(self, _profile_id, _request):
            if not self.responses:
                raise AssertionError("scripted platform ran out of responses")
            return self.responses.pop(0)

    runtime = DurableAgentRuntime(
        platform=_Platform(),
        store=store,
        tools=ToolRegistry(),
        default_permission_mode=PermissionMode.WORKSPACE,
        auto_drain_queue=False,
    )
    service = ManagedStreamingLoomAppServerService(
        runtime=runtime,
        store=store,
        model="test-model",
        default_workspace=workspace,
        default_permission_mode=PermissionMode.WORKSPACE,
    )
    return service, runtime, workspace


def _wait_until(predicate, *, timeout: float = 4.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.01)
    raise AssertionError("timed out waiting for app-server state")


def test_the_report_separates_what_the_agent_chose_from_what_it_was_given() -> None:
    report = context_report_from_request(_RECORDED_REQUEST, compactions=48)

    assert report["usedTokens"] == 45_372
    assert report["inputBudgetTokens"] == 50_790
    assert report["usedPercent"] == 89.3
    assert report["freeTokens"] == 5_418

    segments = {segment["key"]: segment["tokens"] for segment in report["segments"]}
    # Definitions are the one part of a request the agent never asked for, so
    # they are reported apart from its own conversation.
    assert segments["toolSchemas"] == 7_994
    assert segments["conversation"] == 45_372 - 7_994
    assert segments["free"] == 5_418
    assert sum(segments.values()) == report["inputBudgetTokens"]


def test_the_report_says_out_loud_when_the_agent_has_been_blinded() -> None:
    report = context_report_from_request(_RECORDED_REQUEST)

    # 19 collapsed observations is not "a smaller context": it is an agent that
    # cannot read the output of the commands it just ran, which is exactly the
    # state users were left to diagnose from the model's own complaints.
    assert report["pressure"]["toolOutputsCollapsed"] == 19
    assert report["pressure"]["blinded"] is True

    calm = dict(_RECORDED_REQUEST, tool_outputs_collapsed=0)
    assert context_report_from_request(calm)["pressure"]["blinded"] is False


def test_an_unmeasured_thread_reports_its_budget_rather_than_nothing() -> None:
    report = empty_context_report(_RECORDED_REQUEST["context_limits"])

    assert report["inputBudgetTokens"] == 50_790
    assert report["usedTokens"] == 0
    assert report["freeTokens"] == 50_790
    assert report["pressure"]["blinded"] is False


def test_thread_context_reports_the_last_real_request(tmp_path: Path) -> None:
    service, runtime, workspace = _build_service(tmp_path, [ModelResponse(text="done")])
    try:
        thread_id = service.thread_start(
            {"workspace": str(workspace), "permissionMode": "workspace"}
        )["thread"]["id"]

        # Before any model step the budget is knowable and nothing is spent.
        before = service.thread_context({"threadId": thread_id})["context"]
        assert before["usedTokens"] == 0
        assert before["threadId"] == thread_id

        service.turn_start({"threadId": thread_id, "input": "hello"})
        _wait_until(lambda: not service._is_active(thread_id))

        after = service.thread_context({"threadId": thread_id})["context"]
        assert after["messageCount"] >= 1
        assert after["measuredAt"]
        assert after["compactions"] == 0
    finally:
        runtime.close()


def test_checkpoint_replaces_stale_request_usage_with_compacted_estimate(tmp_path: Path) -> None:
    service, runtime, workspace = _build_service(tmp_path, [ModelResponse(text="done")])
    try:
        thread_id = service.thread_start({"workspace": str(workspace)})["thread"]["id"]
        session = runtime.store.load(thread_id)
        runtime._record(session, AgentEventKind.MODEL_REQUESTED, data=dict(_RECORDED_REQUEST))
        compacted = dict(_RECORDED_REQUEST)
        compacted.update(
            {
                "calibrated_input_tokens_after": 12_000,
                "active_context_tokens": 12_000,
                "token_accounting_source": "post_compaction_estimate",
                "tool_outputs_reduced": 0,
                "tool_outputs_collapsed": 0,
            }
        )
        runtime._record(
            session,
            AgentEventKind.CONTEXT_CHECKPOINTED,
            data={"checkpoint_id": "ctx-new", "context_after_compaction": compacted},
        )

        report = service.thread_context({"threadId": thread_id})["context"]

        assert report["usedTokens"] == 12_000
        assert report["accounting"] == "post_compaction_estimate"
        assert report["pressure"]["blinded"] is False
        assert report["compactions"] == 1
    finally:
        runtime.close()


def test_legacy_checkpoint_marks_pre_compaction_usage_stale(tmp_path: Path) -> None:
    service, runtime, workspace = _build_service(tmp_path, [])
    try:
        thread_id = service.thread_start({"workspace": str(workspace)})["thread"]["id"]
        session = runtime.store.load(thread_id)
        runtime._record(session, AgentEventKind.MODEL_REQUESTED, data=dict(_RECORDED_REQUEST))
        runtime._record(
            session,
            AgentEventKind.CONTEXT_CHECKPOINTED,
            data={"checkpoint_id": "ctx-legacy"},
        )

        report = service.thread_context({"threadId": thread_id})["context"]

        assert report["accounting"] == "stale_pre_compaction"
    finally:
        runtime.close()


def test_the_meter_updates_live_without_a_second_round_trip(tmp_path: Path) -> None:
    service, runtime, workspace = _build_service(tmp_path, [ModelResponse(text="done")])
    notifications: list[tuple[str, dict]] = []
    service.subscribe_notifications(lambda method, params: notifications.append((method, params)))
    try:
        thread_id = service.thread_start(
            {"workspace": str(workspace), "permissionMode": "workspace"}
        )["thread"]["id"]
        service.turn_start({"threadId": thread_id, "input": "hello"})
        _wait_until(lambda: not service._is_active(thread_id))

        updates = [params for method, params in notifications if method == "context/updated"]
        assert updates
        assert updates[-1]["threadId"] == thread_id
        assert "usedTokens" in updates[-1]["context"]
    finally:
        runtime.close()


def test_live_updates_do_not_un_count_earlier_compactions(tmp_path: Path) -> None:
    """A restart must not make a heavily compacted thread report zero."""

    service, runtime, workspace = _build_service(tmp_path, [ModelResponse(text="done")])
    notifications: list[tuple[str, dict]] = []
    try:
        thread_id = service.thread_start(
            {"workspace": str(workspace), "permissionMode": "workspace"}
        )["thread"]["id"]
        runtime._record(
            runtime.store.load(thread_id),
            AgentEventKind.CONTEXT_CHECKPOINTED,
            data={"checkpoint_id": "ctx-test", "archived_messages": 4, "retained_messages": 2},
        )

        # Nothing has populated the in-memory cache, exactly as after a restart.
        service._context_counts.clear()
        service.subscribe_notifications(lambda method, params: notifications.append((method, params)))
        service.turn_start({"threadId": thread_id, "input": "hello"})
        _wait_until(lambda: not service._is_active(thread_id))

        updates = [params for method, params in notifications if method == "context/updated"]
        assert updates
        assert all(params["context"]["compactions"] >= 1 for params in updates)
    finally:
        runtime.close()


def test_manual_compaction_refuses_to_run_underneath_an_active_turn(tmp_path: Path) -> None:
    service, runtime, workspace = _build_service(tmp_path, [ModelResponse(text="done")])
    try:
        thread_id = service.thread_start(
            {"workspace": str(workspace), "permissionMode": "workspace"}
        )["thread"]["id"]
        session = runtime.store.load(thread_id)
        session.status = type(session.status).RUNNING
        runtime.store.save(session)

        with pytest.raises(RuntimeError, match="while a turn is active"):
            service.thread_compact({"threadId": thread_id})
    finally:
        runtime.close()


def test_manual_compaction_validates_how_much_history_it_is_asked_to_keep(tmp_path: Path) -> None:
    service, runtime, workspace = _build_service(tmp_path, [ModelResponse(text="done")])
    try:
        thread_id = service.thread_start(
            {"workspace": str(workspace), "permissionMode": "workspace"}
        )["thread"]["id"]

        with pytest.raises(ValueError, match="keepRecent"):
            service.thread_compact({"threadId": thread_id, "keepRecent": 0})
        with pytest.raises(ValueError, match="keepRecent"):
            service.thread_compact({"threadId": thread_id, "keepRecent": 500})
    finally:
        runtime.close()


def test_manual_compaction_publishes_visible_lifecycle(tmp_path: Path) -> None:
    service, runtime, workspace = _build_service(tmp_path, [ModelResponse(text="handoff summary")])
    notifications: list[tuple[str, dict]] = []
    service.subscribe_notifications(lambda method, params: notifications.append((method, params)))
    try:
        thread_id = service.thread_start(
            {"workspace": str(workspace), "permissionMode": "workspace"}
        )["thread"]["id"]
        session = runtime.store.load(thread_id)
        session.messages.append(AIMessage(role=MessageRole.USER, content="work already completed"))
        runtime.store.save(session)
        runtime.compact_context_with_model = lambda _session_id, keep_recent=24: object()  # type: ignore[attr-defined]

        result = service.thread_compact({"threadId": thread_id})
        assert result["operationId"]
        _wait_until(lambda: not service._is_active(thread_id))

        progress = [params for method, params in notifications if method == "context/compaction"]
        assert progress
        assert progress[-1]["status"] != "failed", progress[-1]["error"]
        assert {item["stage"] for item in progress} >= {
            "queued",
            "preparing",
            "summarizing",
            "completed",
        }, progress
        assert all(item["operationId"] == result["operationId"] for item in progress)
        assert not any(item["status"] == "failed" for item in progress)
    finally:
        runtime.close()
