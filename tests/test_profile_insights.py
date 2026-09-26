from __future__ import annotations

import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.ai import ModelResponse, ModelUsage
from app.agent_runtime import (
    AgentEvent,
    AgentEventKind,
    AgentSession,
    DurableAgentRuntime,
    FileAgentSessionStore,
    PermissionMode,
    ToolRegistry,
)
from app.app_server import PROTOCOL_VERSION
from app.app_server_thread_management import (
    ManagedStreamingLoomAppServerService,
    ManagedStreamingLoomRpcController,
)


class ProfilePlatform:
    def __init__(self) -> None:
        self.responses = [
            ModelResponse(text="first answer", usage=ModelUsage(120, 30, 150)),
            ModelResponse(text="second answer", usage=ModelUsage(40, 10, 50)),
        ]

    def execute_chat(self, _profile_id, request):
        # Auto-title generation is detached from the visible turn. Give it a
        # harmless deterministic answer without consuming a scripted turn.
        if int(getattr(request, "max_output_tokens", 0) or 0) == 48:
            return ModelResponse(text="Usage profile test")
        if not self.responses:
            raise AssertionError("scripted profile responses exhausted")
        return self.responses.pop(0)


def _wait_until(predicate, *, timeout: float = 3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.01)
    raise AssertionError("timed out waiting for profile test turn")


def _build_service(tmp_path: Path):
    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = FileAgentSessionStore(home)
    runtime = DurableAgentRuntime(
        platform=ProfilePlatform(),
        store=store,
        tools=ToolRegistry(()),
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


def test_profile_insights_uses_durable_model_usage_and_rpc(tmp_path: Path) -> None:
    service, runtime, workspace = _build_service(tmp_path)
    try:
        thread_id = service.thread_start({"workspace": str(workspace)})["thread"]["id"]
        # A custom title prevents the detached auto-title request from competing
        # with the two scripted model responses below.
        service.thread_rename({"threadId": thread_id, "title": "Usage profile test"})

        service.turn_start({"threadId": thread_id, "input": "first"})
        _wait_until(lambda: thread_id not in service.runtime_status()["activeThreadIds"])
        service.turn_start({"threadId": thread_id, "input": "second"})
        _wait_until(lambda: thread_id not in service.runtime_status()["activeThreadIds"])

        snapshot = service.profile_insights({"days": 90})
        assert snapshot["totals"]["sessions"] == 1
        assert snapshot["totals"]["turns"] == 2
        assert snapshot["totals"]["modelCalls"] == 2
        assert snapshot["totals"]["inputTokens"] == 160
        assert snapshot["totals"]["outputTokens"] == 40
        assert snapshot["totals"]["totalTokens"] == 200
        assert snapshot["totals"]["activeDays"] == 1
        assert snapshot["peakDay"]["totalTokens"] == 200
        assert sum(day["totalTokens"] for day in snapshot["days"]) == 200
        assert snapshot["models"][0] == {
            "name": "test-model",
            "calls": 2,
            "tokens": 200,
        }

        controller = ManagedStreamingLoomRpcController(service)
        initialized = controller.handle(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": PROTOCOL_VERSION,
                    "clientInfo": {"name": "pytest", "version": "1"},
                },
            }
        )
        assert initialized["result"]["capabilities"]["profileInsights"] == {
            "usage": True,
            "heatmap": True,
        }

        response = controller.handle(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "profile/insights",
                "params": {"days": 90},
            }
        )
        assert response["result"]["totals"]["totalTokens"] == 200
    finally:
        runtime.close()


def _local_stamp(days_ago: int, hour: int, minute: int = 0, second: int = 0) -> datetime:
    midnight = datetime.now().astimezone().replace(hour=0, minute=0, second=0, microsecond=0)
    day = (midnight - timedelta(days=days_ago)).date()
    return datetime(day.year, day.month, day.day, hour, minute, second).astimezone()


class _SyntheticHistory:
    """Writes durable events with exact timestamps, bypassing the turn loop."""

    def __init__(self, store: FileAgentSessionStore, workspace: Path) -> None:
        self.store = store
        self.workspace = workspace

    def session(self, *, model: str) -> str:
        session_id = uuid.uuid4().hex
        stamp = datetime.now(timezone.utc).isoformat()
        self.store.create(AgentSession(
            session_id=session_id,
            profile_id="agent.fast",
            system_prompt="",
            workspace_dir=str(self.workspace),
            created_at=stamp,
            updated_at=stamp,
            model=model,
        ))
        return session_id

    def event(self, session_id: str, turn_id: str, kind: AgentEventKind, at: datetime, **data) -> None:
        self.store.append_event(AgentEvent(
            event_id=uuid.uuid4().hex,
            session_id=session_id,
            turn_id=turn_id,
            kind=kind,
            created_at=at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
            data=data,
        ))

    def response(self, session_id: str, turn_id: str, at: datetime, tokens: int) -> None:
        self.event(
            session_id, turn_id, AgentEventKind.MODEL_RESPONSE, at,
            usage={"input_tokens": tokens, "output_tokens": 0, "total_tokens": tokens},
        )


def test_profile_insights_measures_work_attribution_and_streaks(tmp_path: Path) -> None:
    service, runtime, workspace = _build_service(tmp_path)
    try:
        history = _SyntheticHistory(service.store, workspace)
        known = history.session(model="alpha-model")
        kind = AgentEventKind

        # Five seconds of work, then a cancel two days later. The cancel is not
        # the end of the work.
        stale = _local_stamp(3, 10)
        history.event(known, "stale", kind.TURN_STARTED, stale)
        history.event(known, "stale", kind.MODEL_REQUESTED, stale + timedelta(seconds=1))
        history.response(known, "stale", stale + timedelta(seconds=5), 100)
        history.event(known, "stale", kind.TURN_CANCELLED, _local_stamp(1, 10))

        # Half an hour waiting on an approval prompt belongs to the user.
        waited = _local_stamp(2, 11)
        history.event(known, "waited", kind.TURN_STARTED, waited)
        history.event(known, "waited", kind.TOOL_APPROVAL_REQUIRED, waited + timedelta(seconds=10))
        history.event(known, "waited", kind.TOOL_APPROVED, waited + timedelta(minutes=30, seconds=10))
        history.response(known, "waited", waited + timedelta(minutes=31), 200)
        history.event(known, "waited", kind.TURN_COMPLETED, waited + timedelta(minutes=31))

        # The genuinely longest turn: 150 seconds, completed normally.
        longest = _local_stamp(1, 9)
        history.event(known, "longest", kind.TURN_STARTED, longest)
        history.response(known, "longest", longest + timedelta(minutes=2), 300)
        history.event(known, "longest", kind.TURN_COMPLETED, longest + timedelta(minutes=2, seconds=30))

        # A session that never recorded its model.
        unknown = history.session(model="")
        legacy = _local_stamp(1, 10, 30)
        history.event(unknown, "legacy", kind.TURN_STARTED, legacy)
        history.response(unknown, "legacy", legacy + timedelta(seconds=3), 40)
        history.event(unknown, "legacy", kind.TURN_COMPLETED, legacy + timedelta(seconds=4))

        snapshot = service.profile_insights({"days": 90})

        assert snapshot["longestTurnSeconds"] == 150
        assert snapshot["longestTurnDate"] == longest.date().isoformat()

        # Nothing has happened today yet, and the run through yesterday counts.
        assert snapshot["streaks"] == {"current": 3, "longest": 3, "activeToday": False}

        assert [row["name"] for row in snapshot["models"]] == ["alpha-model"]
        assert snapshot["models"][0]["tokens"] == 600
        assert snapshot["unrecordedModel"] == {"calls": 1, "tokens": 40}
        assert snapshot["modelKinds"] == 1

        hours = snapshot["hours"]
        assert len(hours) == 24
        assert (hours[9], hours[10], hours[11]) == (1, 2, 1)
        assert sum(hours) == snapshot["totals"]["turns"] == 4
        assert snapshot["activeHour"] == {"hour": 10, "turns": 2}
    finally:
        runtime.close()


def test_profile_streak_counts_today_once_it_is_active(tmp_path: Path) -> None:
    service, runtime, workspace = _build_service(tmp_path)
    try:
        history = _SyntheticHistory(service.store, workspace)
        session = history.session(model="alpha-model")
        for days_ago in (4, 1, 0):
            history.event(session, f"turn-{days_ago}", AgentEventKind.TURN_STARTED, _local_stamp(days_ago, 9))

        streaks = service.profile_insights({"days": 90})["streaks"]
        assert streaks == {"current": 2, "longest": 2, "activeToday": True}
    finally:
        runtime.close()
