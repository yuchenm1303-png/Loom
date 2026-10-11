from __future__ import annotations

import dataclasses
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

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


def test_model_events_keep_sample_identity_when_configuration_changes(tmp_path: Path) -> None:
    service, runtime, workspace = _build_service(tmp_path)
    try:
        selected = {"model": "first-model", "provider": "test-provider", "credential_ref": "not-event-data"}
        runtime.platform.registry = SimpleNamespace(
            get=lambda _: SimpleNamespace(as_safe_dict=lambda: dict(selected)),
        )
        execute = runtime.platform.execute_chat

        def changing_model(profile_id, request):
            # Simulate configuration changing while a response is in flight.
            selected["model"] = "second-model"
            return execute(profile_id, request)

        runtime.platform.execute_chat = changing_model
        thread_id = service.thread_start({"workspace": str(workspace)})["thread"]["id"]
        service.thread_rename({"threadId": thread_id, "title": "Model attribution test"})
        for prompt in ("first", "second"):
            service.turn_start({"threadId": thread_id, "input": prompt})
            _wait_until(lambda: thread_id not in service.runtime_status()["activeThreadIds"])
        events = service.store.events(thread_id)
        for kind in (AgentEventKind.MODEL_REQUESTED, AgentEventKind.MODEL_RESPONSE):
            samples = [event.data for event in events if event.kind is kind]
            assert [sample["model"] for sample in samples] == ["first-model", "second-model"]
            assert all(sample["provider"] == "test-provider" for sample in samples)
            assert all("credential_ref" not in sample for sample in samples)
        models = {row["name"]: row for row in service.profile_insights({"days": 90})["models"]}
        assert models["first-model"]["tokens"] == 150
        assert models["second-model"]["tokens"] == 50
    finally:
        runtime.close()


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
        # The desktop service stamps each thread's model when it is started
        # (app_server_reasoning); this base service does not, so record it the
        # same way. Calls are credited to the model the thread recorded.
        session = service.store.load(thread_id)
        session.model = "test-model"
        service.store.save(session)

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
            system_prompt="Synthetic profile history.",
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


# --- reuse across requests --------------------------------------------------------
#
# The home screen asks for these numbers at every launch and the profile page asks
# again. Parsing every log each time took seconds; only the few fields the profile
# reads are kept now, until a log changes.


def _watch_reads(service) -> list[str]:
    reads: list[str] = []
    real = service.store.presentation_events
    service.store.presentation_events = lambda session_id: reads.append(session_id) or real(session_id)
    return reads


def _without_clock(snapshot: dict) -> dict:
    return {key: value for key, value in snapshot.items() if key != "generatedAt"}


def test_a_second_request_does_not_read_unchanged_logs_again(tmp_path: Path) -> None:
    service, runtime, workspace = _build_service(tmp_path)
    try:
        history = _SyntheticHistory(service.store, workspace)
        session = history.session(model="alpha-model")
        history.event(session, "turn", AgentEventKind.TURN_STARTED, _local_stamp(1, 9))
        history.response(session, "turn", _local_stamp(1, 9, 1), 120)
        reads = _watch_reads(service)

        first = service.profile_insights({"days": 90})
        second = service.profile_insights({"days": 90})

        assert reads == [session]
        assert _without_clock(first) == _without_clock(second)
        assert first["totals"]["totalTokens"] == 120
    finally:
        runtime.close()


def test_a_log_that_grew_is_read_again_and_counted(tmp_path: Path) -> None:
    service, runtime, workspace = _build_service(tmp_path)
    try:
        history = _SyntheticHistory(service.store, workspace)
        session = history.session(model="alpha-model")
        history.event(session, "turn", AgentEventKind.TURN_STARTED, _local_stamp(1, 9))
        history.response(session, "turn", _local_stamp(1, 9, 1), 120)
        reads = _watch_reads(service)
        assert service.profile_insights({"days": 90})["totals"]["totalTokens"] == 120

        history.response(session, "turn", _local_stamp(1, 9, 2), 80)

        assert service.profile_insights({"days": 90})["totals"]["totalTokens"] == 200
        assert reads == [session, session]
    finally:
        runtime.close()


def test_a_deleted_conversation_leaves_no_cached_events_behind(tmp_path: Path) -> None:
    import shutil

    service, runtime, workspace = _build_service(tmp_path)
    try:
        history = _SyntheticHistory(service.store, workspace)
        kept = history.session(model="alpha-model")
        removed = history.session(model="alpha-model")
        for session in (kept, removed):
            history.event(session, "turn", AgentEventKind.TURN_STARTED, _local_stamp(1, 9))
        service.profile_insights({"days": 90})
        assert set(service._profile_events_cache) == {kept, removed}

        shutil.rmtree(service.store.session_dir(removed))

        assert service.profile_insights({"days": 90})["totals"]["sessions"] == 1
        assert set(service._profile_events_cache) == {kept}
    finally:
        runtime.close()


def test_an_event_copied_into_a_fork_is_still_counted_once(tmp_path: Path) -> None:
    """A fork carries its parent's history, event ids included; that spend happened once."""
    service, runtime, workspace = _build_service(tmp_path)
    try:
        history = _SyntheticHistory(service.store, workspace)
        parent = history.session(model="alpha-model")
        fork = history.session(model="alpha-model")
        stamp = _local_stamp(1, 9)
        shared = AgentEvent(
            event_id="shared-event",
            session_id=parent,
            turn_id="turn",
            kind=AgentEventKind.MODEL_RESPONSE,
            created_at=stamp.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
            data={"usage": {"input_tokens": 70, "output_tokens": 0, "total_tokens": 70}},
        )
        service.store.append_event(shared)
        service.store.append_event(dataclasses.replace(shared, session_id=fork))

        for _ in range(2):  # the second request is answered from the cache
            snapshot = service.profile_insights({"days": 90})
            assert snapshot["totals"]["totalTokens"] == 70
            assert snapshot["totals"]["modelCalls"] == 1
    finally:
        runtime.close()


def test_cached_answers_equal_freshly_computed_ones(tmp_path: Path) -> None:
    service, runtime, workspace = _build_service(tmp_path)
    try:
        history = _SyntheticHistory(service.store, workspace)
        kind = AgentEventKind
        for model, days in (("alpha-model", (5, 3, 1)), ("beta-model", (4, 1, 0))):
            session = history.session(model=model)
            for index, days_ago in enumerate(days):
                at = _local_stamp(days_ago, 9 + index)
                history.event(session, f"turn-{index}", kind.TURN_STARTED, at)
                history.event(session, f"turn-{index}", kind.TOOL_REQUESTED, at + timedelta(seconds=2), tool="shell")
                history.response(session, f"turn-{index}", at + timedelta(seconds=5), 100 * (index + 1))
                history.event(session, f"turn-{index}", kind.TURN_COMPLETED, at + timedelta(seconds=9))

        warm = service.profile_insights({"days": 90})
        warm_again = service.profile_insights({"days": 90})
        service._profile_events_cache.clear()
        cold = service.profile_insights({"days": 90})

        assert _without_clock(warm) == _without_clock(warm_again) == _without_clock(cold)
        assert cold["totals"]["toolCalls"] == 6
    finally:
        runtime.close()


def test_the_cache_stays_within_its_budget_and_answers_stay_right(tmp_path: Path, monkeypatch) -> None:
    from app import app_server_thread_management

    service, runtime, workspace = _build_service(tmp_path)
    try:
        history = _SyntheticHistory(service.store, workspace)
        sessions = []
        for index in range(3):
            session = history.session(model="alpha-model")
            history.event(session, "turn", AgentEventKind.TURN_STARTED, _local_stamp(index + 1, 9))
            history.response(session, "turn", _local_stamp(index + 1, 9, 1), 10)
            sessions.append(session)
        # Room for the events of one log only (each has two relevant events).
        monkeypatch.setattr(app_server_thread_management, "_PROFILE_CACHE_EVENT_BUDGET", 3)

        first = service.profile_insights({"days": 90})
        held = sum(len(entry[1]) for entry in service._profile_events_cache.values())
        second = service.profile_insights({"days": 90})

        assert held <= 3, f"{held} events cached against a budget of 3"
        assert 0 < len(service._profile_events_cache) < len(sessions)
        assert _without_clock(first) == _without_clock(second)
        assert first["totals"]["totalTokens"] == 30
    finally:
        runtime.close()
