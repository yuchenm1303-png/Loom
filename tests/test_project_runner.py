"""Exercise durable project execution with real Loom turns and Git workspaces."""
import json
from pathlib import Path
import sqlite3
import threading
import uuid

import pytest

from app.ai import ModelResponse, ModelUsage, ToolCall
from app.agent_runtime import AgentTool, ToolEffect, ToolResult, DurableAgentRuntime, FileAgentSessionStore, ToolRegistry
from app.app_server import LoomAppServerService, LoomRpcController
from app.project_runner.service import ProjectServiceMixin, ProjectControllerMixin, git
from app.project_runner.store import ProjectStore
from test_app_server import RecordingPlatform, _build_service, _wait_until


class Service(ProjectServiceMixin, LoomAppServerService):
    pass


class Controller(ProjectControllerMixin, LoomRpcController):
    pass


def build(tmp_path, responses, *, tools=(), service_class=Service):
    _, runtime, store, platform, workspace = _build_service(tmp_path, responses, tools=tools)
    git(workspace, "init")
    git(workspace, "config", "user.name", "Project test")
    git(workspace, "config", "user.email", "test@example.invalid")
    (workspace / "README.md").write_text("initial\n")
    git(workspace, "add", "README.md")
    git(workspace, "commit", "-m", "initial")
    service = service_class(runtime=runtime, store=store, model="test-model", default_workspace=workspace)
    # Drive the same scheduler deterministically; the background loop is tested separately.
    service.project_runner.stop.set()
    service.project_runner.thread.join()
    service.project_runner.stop.clear()
    source = service.thread_start({"workspaceDir": str(workspace), "permissionMode": "workspace"})["thread"]["id"]
    return service, runtime, platform, workspace, source


def create(service, source, **kwargs):
    return service.project_run_create({"operationId": str(uuid.uuid4()), "threadId": source,
        "objective": "Implement a working project", "tokenBudget": 1000, "maxEpisodes": 8,
        "reviewPolicy": "manual", **kwargs})


def control(service, run, action):
    return service.project_control({"operationId": str(uuid.uuid4()), "runId": run["id"],
                                    "expectedRevision": run["revision"]}, action)


def cycle(service):
    service.project_runner.tick()
    # Wait for the Host's completion signal rather than racing a three-second
    # polling deadline against real Git/filesystem work on Windows.
    with service._guard:
        completions = list(getattr(service, "_operation_done", {}).values())
    for done in completions:
        assert done.wait(10), service._task_errors


def reply(value, tokens=5):
    return ModelResponse(text=json.dumps(value), usage=ModelUsage(tokens, 0, tokens))


PLAN = {"tasks": [{"instructions": "Implement the app", "acceptance": ["App starts and saves data"]}]}


def test_real_runtime_plans_continues_and_returns_candidate_in_isolated_directory(tmp_path):
    def implement(context, arguments):
        context.resolve_workspace_path("app.txt").write_text("implemented in isolated workspace")
        return ToolResult(ok=True, content="App implementation saved")
    tool = AgentTool(name="implement_test_app", description="Implement the test app",
        input_schema={"type": "object", "properties": {}}, effect=ToolEffect.MUTATING, handler=implement)
    service, runtime, platform, workspace, source = build(tmp_path, [reply(PLAN),
        ModelResponse(tool_calls=(ToolCall(call_id="write-app", name="implement_test_app", arguments={}),)),
        reply({"action": "continue", "summary": "First part saved; finish the app"}),
        reply({"action": "candidate", "summary": "App implemented and checked"})], tools=(tool,))
    try:
        run = create(service, source)
        cycle(service)
        assert not platform.requests  # creation/idle never samples
        control(service, run, "start")
        for _ in range(4):
            cycle(service)
        final = service.project_store.get(run["id"])
        assert final["state"] == "ready_for_review"
        assert final["episodesUsed"] == 3 and final["tokensUsed"] == 15
        assert final["plan"][0]["state"] == "candidate"  # never falsely "done"
        worker = runtime.get_session(final["sessions"]["worker-0"])
        assert Path(worker.workspace_dir) != workspace
        assert Path(worker.workspace_dir, "README.md").read_text() == "initial\n"
        assert Path(worker.workspace_dir, "app.txt").read_text() == "implemented in isolated workspace"
        assert not (workspace / "app.txt").exists()
        assert runtime.get_session(final["sessions"]["planner"]).permission_mode.value == "read-only"
        assert len(platform.requests) == 4
        assert "First part saved" in str(platform.requests[-1].messages)
    finally:
        runtime.close()


def test_pause_is_durable_and_budget_stops_automatic_continuation(tmp_path):
    service, runtime, platform, _, source = build(tmp_path, [reply(PLAN), reply({"action": "continue", "summary": "progress"})])
    try:
        run = create(service, source, maxEpisodes=1)
        run = control(service, run, "start")
        run = control(service, run, "pause")
        cycle(service)
        assert not platform.requests
        control(service, run, "resume")
        cycle(service)
        cycle(service)
        final = service.project_store.get(run["id"])
        assert final["control"] == "budget_limited"
        assert len(platform.requests) == 1
        with pytest.raises(ValueError, match="exhausted"):
            control(service, final, "resume")
    finally:
        runtime.close()


def test_store_idempotency_cas_and_real_connection_claim_constraint(tmp_path):
    store = ProjectStore(tmp_path)
    other = ProjectStore(tmp_path)
    try:
        values = {"maxEpisodes": 3, "tokenBudget": 100}
        run = store.create("create-once", values)
        assert other.create("create-once", values)["id"] == run["id"]
        with pytest.raises(ValueError, match="different"):
            store.create("create-once", {**values, "tokenBudget": 101})
        started = store.control("start", run["id"], run["revision"], "start")
        with pytest.raises(ValueError, match="stale"):
            other.control("pause", run["id"], run["revision"], "pause")
        assert store.control("start", run["id"], run["revision"], "start")["revision"] == started["revision"]
        with store.transaction() as db:
            db.execute("INSERT INTO project_episodes VALUES('e',?,'s','t','prepared','{}')", (run["id"],))
        with pytest.raises(sqlite3.IntegrityError):
            with other.transaction() as db:
                db.execute("INSERT INTO project_episodes VALUES('e2',?,'s','t2','prepared','{}')", (run["id"],))
                db.execute("UPDATE project_runs SET revision=999")
        assert store.get(run["id"])["revision"] == started["revision"]
        assert len(store.events(run["id"])) == 2
    finally:
        other.close()
        store.close()


def test_invalid_plan_and_plain_done_never_complete_project(tmp_path):
    # A reply without a plan goes back to the planner twice, then the run stops; it never completes.
    service, runtime, platform, _, source = build(tmp_path, [ModelResponse(text="All done!", usage=ModelUsage(5, 0, 5))] * 3)
    try:
        run = create(service, source)
        control(service, run, "start")
        for _ in range(5):
            cycle(service)
        final = service.project_store.get(run["id"])
        assert final["state"] == "needs_review" and len(platform.requests) == 3 and not final["plan"]
    finally:
        runtime.close()


def test_restart_reconciles_finished_turn_without_resampling(tmp_path):
    service, runtime, platform, _, source = build(tmp_path, [reply(PLAN)])
    run = create(service, source)
    control(service, run, "start")
    cycle(service)  # turn ends, project still has durable intent
    assert service.project_store.get(run["id"])["activeEpisode"]
    current = service.project_store.get(run["id"])
    control(service, current, "pause")
    session_home = service.store.root.parent.parent
    workspace = service.default_workspace
    runtime.close()
    # Discard both service and runtime. Only their durable stores survive.
    store = FileAgentSessionStore(session_home)
    new_platform = RecordingPlatform([])
    runtime = DurableAgentRuntime(platform=new_platform, store=store, tools=ToolRegistry(()), auto_drain_queue=False)
    service = Service(runtime=runtime, store=store, model="test-model", default_workspace=workspace)
    service.project_runner.stop.set()
    service.project_runner.thread.join()
    service.project_runner.stop.clear()
    try:
        cycle(service)
        final = service.project_store.get(run["id"])
        assert final["phase"] == "working" and final["activeEpisode"] is None
        assert final["control"] == "paused" and len(platform.requests) == 1 and not new_platform.requests
    finally:
        runtime.close()


def test_workspace_project_create_and_project_run_create_are_distinct(tmp_path):
    """Production mixin must not steal the existing project/create RPC."""
    service, runtime, _, workspace, source = build(tmp_path, [])
    try:
        rpc = Controller(service)
        project = rpc._dispatch("project/create", {"root": str(workspace)})["project"]
        assert Path(project["root"]).resolve() == workspace.resolve()

        run = rpc._dispatch("projectRun/create", {
            "operationId": "create-regression-run",
            "threadId": source,
            "objective": "Verify distinct project creation commands",
            "tokenBudget": 1000,
            "maxEpisodes": 2,
        })["run"]
        assert run["sourceThreadId"] == source
        assert run["state"] == "created"
        assert service.project_store.get(run["id"])["id"] == run["id"]
    finally:
        runtime.close()


def test_rpc_exposes_run_and_event_cursor_and_rejects_dirty_source(tmp_path):
    service, runtime, _, workspace, source = build(tmp_path, [])
    try:
        rpc = Controller(service)
        run = create(service, source)
        assert rpc._dispatch("projectRun/get", {"runId": run["id"]})["run"]["id"] == run["id"]
        events = rpc._dispatch("projectRun/events", {"runId": run["id"]})["events"]
        assert events[0]["kind"] == "created"
        assert rpc._dispatch("projectRun/events", {"runId": run["id"], "afterSequence": events[0]["sequence"]}) == {"events": []}
        (workspace / "README.md").write_text("uncommitted")
        with pytest.raises(ValueError, match="existing workspace"):
            create(service, source)
    finally:
        runtime.close()


def test_create_retry_keeps_original_snapshot_after_repository_changes(tmp_path):
    service, runtime, _, workspace, source = build(tmp_path, [])
    params = {"operationId": "stable-create", "threadId": source, "objective": "Build app",
              "tokenBudget": 100, "maxEpisodes": 3}
    try:
        run = service.project_run_create(params)
        (workspace / "README.md").write_text("a later unrelated edit")
        assert service.project_run_create(params)["baseCommit"] == run["baseCommit"]
        assert service.project_run_create(params)["id"] == run["id"]
    finally:
        runtime.close()


def test_cancel_during_preparation_prevents_first_model_call(tmp_path):
    service, runtime, platform, _, source = build(tmp_path, [])
    entered, release = threading.Event(), threading.Event()
    def prepare(session):
        entered.set()
        assert release.wait(5)
    service._prepare_turn = prepare
    try:
        run = create(service, source)
        control(service, run, "start")
        service.project_runner.tick()
        assert entered.wait(2)
        current = service.project_store.get(run["id"])
        control(service, current, "cancel")
        queued = create(service, source)
        queued = control(service, queued, "start")
        service.project_runner.tick()
        assert service.project_store.get(queued["id"])["episodesUsed"] == 0
        control(service, queued, "pause")
        release.set()
        _wait_until(lambda: not service._active_sessions)
        cycle(service)
        final = service.project_store.get(run["id"])
        assert final["state"] == "cancelled" and final["activeEpisode"] is None
        assert not platform.requests
    finally:
        release.set()
        runtime.close()


def test_missing_dispatch_receipt_is_never_replayed(tmp_path):
    service, runtime, platform, _, source = build(tmp_path, [])
    service.project_runner.executor.dispatch = lambda episode: None
    try:
        run = create(service, source)
        control(service, run, "start")
        for _ in range(4):
            cycle(service)
        final = service.project_store.get(run["id"])
        assert final["state"] == "needs_review" and final["episodesUsed"] == 1
        with pytest.raises(ValueError, match="reconciliation"):
            control(service, final, "resume")
        assert not platform.requests
    finally:
        runtime.close()


def test_project_preserves_sensitive_tool_approval(tmp_path):
    calls = []
    tool = AgentTool(name="sensitive_project_test", description="Sensitive operation",
        input_schema={"type": "object", "properties": {}}, effect=ToolEffect.SENSITIVE,
        handler=lambda context, arguments: calls.append(True) or ToolResult(ok=True, content="ran"))
    service, runtime, platform, _, source = build(tmp_path, [reply(PLAN),
        ModelResponse(tool_calls=(ToolCall(call_id="sensitive", name=tool.name, arguments={}),))], tools=(tool,))
    try:
        run = create(service, source)
        control(service, run, "start")
        for _ in range(4):
            cycle(service)
        final = service.project_store.get(run["id"])
        episode = final["activeEpisode"]
        assert runtime.get_session(episode["sessionId"]).status.value == "waiting_approval"
        assert not calls and len(platform.requests) == 2
        control(service, final, "cancel")
        cycle(service)
        assert service.project_store.get(run["id"])["state"] == "cancelled"
    finally:
        runtime.close()


def test_owner_lock_and_preparation_failure(tmp_path):
    service, runtime, platform, _, source = build(tmp_path, [])
    from app.project_runner.runner import ProjectRunner
    second_store = ProjectStore(service.project_store.home)
    try:
        with pytest.raises(RuntimeError, match="owner"):
            ProjectRunner(second_store, service.project_runner.executor)
        def failed_prepare(session):
            raise RuntimeError("credentials unavailable")
        service._prepare_turn = failed_prepare
        run = create(service, source)
        control(service, run, "start")
        cycle(service)
        cycle(service)
        final = service.project_store.get(run["id"])
        assert final["state"] == "needs_review" and final["activeEpisode"] is None
        assert "credentials unavailable" in final["reason"] and not platform.requests
    finally:
        second_store.close()
        runtime.close()


def test_background_scheduler_runs_without_renderer(tmp_path):
    service, runtime, platform, _, source = build(tmp_path, [reply(PLAN),
        reply({"action": "candidate", "summary": "Candidate ready"})])
    try:
        run = create(service, source)
        control(service, run, "start")
        service.project_runner.start()
        _wait_until(lambda: service.project_store.get(run["id"])["state"] == "ready_for_review", timeout=12)
        assert len(platform.requests) == 2
    finally:
        runtime.close()


def test_production_host_loads_both_schedulers_and_routes_project_rpc(tmp_path):
    from app.app_server_browser_policy import BrowserPolicyLoomAppServerService, BrowserPolicyLoomRpcController
    service, runtime, platform, _, source = build(tmp_path, [reply(PLAN),
        reply({"action": "candidate", "summary": "Candidate saved"})], service_class=BrowserPolicyLoomAppServerService)
    try:
        rpc = BrowserPolicyLoomRpcController(service)
        run = rpc._dispatch("projectRun/create", {"operationId": "production-create", "threadId": source,
            "objective": "Build the product", "tokenBudget": 1000, "maxEpisodes": 4, "reviewPolicy": "manual"})["run"]
        control(service, run, "start")
        for _ in range(3):
            cycle(service)
        final = rpc._dispatch("projectRun/get", {"runId": run["id"]})["run"]
        assert final["state"] == "ready_for_review", final["reason"]
        assert service.ambient.snapshot()["online"]
        assert len(platform.requests) == 2
    finally:
        runtime.close()
    assert not service.ambient.owner
    assert service.project_runner.lease.closed


def test_next_task_preserves_prior_candidate_commit(tmp_path):
    def implement(context, arguments):
        directory = context.resolve_workspace_path(".")
        (directory / "app.txt").write_text("candidate")
        git(directory, "add", "app.txt")
        git(directory, "commit", "-m", "candidate task one")
        return ToolResult(ok=True, content="candidate committed")
    tool = AgentTool(name="commit_candidate_test", description="Commit an implementation candidate",
        input_schema={"type": "object", "properties": {}}, effect=ToolEffect.MUTATING, handler=implement)
    plan = {"tasks": PLAN["tasks"] * 2}
    service, runtime, _, workspace, source = build(tmp_path, [reply(plan),
        ModelResponse(tool_calls=(ToolCall(call_id="commit", name=tool.name, arguments={}),)),
        reply({"action": "candidate", "summary": "First task committed"}),
        reply({"action": "candidate", "summary": "Second task checked against first candidate"})], tools=(tool,))
    try:
        run = create(service, source)
        control(service, run, "start")
        for _ in range(4):
            cycle(service)
        final = service.project_store.get(run["id"])
        assert final["state"] == "ready_for_review", final["reason"]
        worker = runtime.get_session(final["sessions"]["worker-1"])
        assert (Path(worker.workspace_dir) / "app.txt").read_text() == "candidate"
        assert git(workspace, "rev-parse", "HEAD") == run["baseCommit"]
    finally:
        runtime.close()


def test_per_episode_step_limit_continues_in_the_next_episode(tmp_path):
    from app.agent_runtime.contracts import AgentLimits
    def step(context, arguments):
        context.resolve_workspace_path("app.txt").write_text("part one")
        return ToolResult(ok=True, content="saved part one")
    tool = AgentTool(name="save_part", description="Save part of the work", input_schema={"type": "object", "properties": {}},
                     effect=ToolEffect.MUTATING, handler=step)
    service, runtime, platform, _, source = build(tmp_path, [reply(PLAN),
        ModelResponse(tool_calls=(ToolCall(call_id="c1", name="save_part", arguments={}),), usage=ModelUsage(5, 0, 5)),
        reply({"action": "candidate", "summary": "Finished after the bound"})], tools=(tool,))
    runtime.limits = AgentLimits(max_model_steps=1)
    try:
        run = control(service, create(service, source), "start")
        for _ in range(4):
            cycle(service)
        final = service.project_store.get(run["id"])
        # The bound ended episode 2, not the project: episode 3 resumed the same session.
        assert final["state"] == "ready_for_review", final["reason"]
        assert final["episodesUsed"] == 3 and final["plan"][0]["state"] == "candidate"
        assert final["episodeLimitStreak"] == 0
        assert "per-episode bound" in str(platform.requests[-1].messages)
        events = [item["kind"] for item in service.project_store.events(run["id"], limit=200)]
        assert "episode_limit_reached" in events
    finally:
        runtime.close()


def test_repeated_episode_limits_stop_for_review(tmp_path, monkeypatch):
    from app.project_runner import runner as runner_module
    monkeypatch.setattr(runner_module, "MAX_EPISODE_BOUND_RETRIES", 1)
    from app.agent_runtime.contracts import AgentLimits
    tool = AgentTool(name="noop_step", description="Do nothing useful", input_schema={"type": "object", "properties": {}},
                     effect=ToolEffect.READ_ONLY, handler=lambda context, arguments: ToolResult(ok=True, content="nothing"))
    looping = ModelResponse(tool_calls=(ToolCall(call_id="c", name="noop_step", arguments={}),), usage=ModelUsage(5, 0, 5))
    service, runtime, platform, _, source = build(tmp_path, [reply(PLAN), looping, looping, looping], tools=(tool,))
    runtime.limits = AgentLimits(max_model_steps=1)
    try:
        run = control(service, create(service, source), "start")
        for _ in range(4):
            cycle(service)
        final = service.project_store.get(run["id"])
        assert final["state"] == "needs_review" and "consecutive episodes" in final["reason"]
        assert final["episodesUsed"] == 3
    finally:
        runtime.close()


def test_continued_episode_starts_from_a_compacted_handoff_without_a_model_call(tmp_path, monkeypatch):
    import test_app_server
    from app.agent_runtime import ContextAgentRuntime
    from app.agent_runtime.contracts import AgentLimits
    from app.project_runner import service as service_module
    # Production runtimes compose ContextAgentRuntime; the plain durable one cannot compact.
    monkeypatch.setattr(test_app_server, "DurableAgentRuntime", ContextAgentRuntime)
    monkeypatch.setattr(service_module, "EPISODE_CARRY_TOKENS", 0)
    tool = AgentTool(name="save_part", description="Save part of the work", input_schema={"type": "object", "properties": {}},
                     effect=ToolEffect.MUTATING, handler=lambda context, arguments: ToolResult(ok=True, content="saved part one " * 200))
    service, runtime, platform, _, source = build(tmp_path, [reply(PLAN),
        ModelResponse(tool_calls=(ToolCall(call_id="c1", name="save_part", arguments={}),), usage=ModelUsage(5, 0, 5)),
        reply({"action": "candidate", "summary": "Finished after the bound"})], tools=(tool,))
    runtime.limits = AgentLimits(max_model_steps=1)
    try:
        run = control(service, create(service, source), "start")
        for _ in range(4):
            cycle(service)
        final = service.project_store.get(run["id"])
        assert final["state"] == "ready_for_review", final["reason"]
        worker = final["sessions"]["worker-0"]
        assert "context_checkpointed" in [event.kind.value for event in service.store.events(worker)]
        last = str(platform.requests[-1].messages)
        # The continued request carries the runner's summary, not the earlier tool output.
        assert "compacted by the project runner" in last and "saved part one saved part one" not in last
        assert "workspaceFiles" in last and "README.md" in last  # the layout, so no re-listing
        assert len(platform.requests) == 3  # compaction itself never sampled the model
        assert final["tokensUsed"] == 15
    finally:
        runtime.close()


@pytest.mark.parametrize("error,transient", [
    ("AITransportError: AI request failed via provider 'p': APIConnectionError: Connection error.", True),
    ("AITransportError: AI request failed via provider 'p': APITimeoutError: Request timed out.", True),
    ("AITransportError: provider returned status 503: overloaded", True),
    ("AITransportError: Error 402 Insufficient Balance", False),
    ("AITransportError: provider returned status 401: invalid api key", False),
    ("RuntimeError: model repeatedly returned malformed responses: timed out parsing", False),
    ("Worker must report action and nonempty summary", False),
])
def test_transient_failure_classification(error, transient):
    from app.project_runner.runner import transient_failure
    assert transient_failure(error) is transient


def _failing_once(platform, error):
    original, calls = platform.execute_chat, []
    def execute(profile, request):
        calls.append(request)
        if len(calls) == 2:  # the worker's first request; the planner's went through
            platform.requests.append(request)
            raise error
        return original(profile, request)
    platform.execute_chat = execute


def test_provider_outage_ends_the_episode_not_the_project(tmp_path):
    from app.ai.errors import AITransportError
    from app.agent_runtime.contracts import AgentLimits
    service, runtime, platform, _, source = build(tmp_path, [reply(PLAN), reply({"action": "candidate", "summary": "Done after the outage"})])
    runtime.limits = AgentLimits(transport_retries=0)
    _failing_once(platform, AITransportError("AI request failed via provider 'p': APIConnectionError: Connection error."))
    try:
        run = control(service, create(service, source, tokenBudget=200000), "start")
        for _ in range(3):  # plan, failed worker episode, its settlement
            cycle(service)
        waiting = service.project_store.get(run["id"])
        # Real ledger run: one connection error stopped a whole two-worker project.
        assert waiting["state"] == "running" and waiting["control"] == "active", waiting["reason"]
        assert waiting["transientFailureStreak"] == 1 and waiting["retryAfter"] > __import__("time").time()
        assert waiting["plan"][0]["state"] == "pending" and "network failure" in waiting["handoff"]
        count = len(platform.requests)
        cycle(service)
        assert len(platform.requests) == count  # nothing is dispatched while backing off
        with service.project_store.transaction() as db:
            current = service.project_store._read(db, run["id"])
            current["retryAfter"] = 0
            service.project_store.save(db, current, "test_backoff_elapsed")
        cycle(service)
        cycle(service)
        final = service.project_store.get(run["id"])
        assert final["state"] == "ready_for_review" and final["transientFailureStreak"] == 0, final["reason"]
        assert final["sessions"]["worker-0"] and final["episodesUsed"] == 3
    finally:
        runtime.close()


def test_permanent_provider_refusal_still_stops_the_project(tmp_path):
    from app.ai.errors import AITransportError
    from app.agent_runtime.contracts import AgentLimits
    service, runtime, platform, _, source = build(tmp_path, [reply(PLAN)])
    runtime.limits = AgentLimits(transport_retries=0)
    _failing_once(platform, AITransportError("Error 402 Insufficient Balance", retryable=False, status_code=402))
    try:
        run = control(service, create(service, source), "start")
        for _ in range(3):
            cycle(service)
        final = service.project_store.get(run["id"])
        assert final["state"] == "needs_review" and "402" in final["reason"]
        assert not final.get("retryAfter")
    finally:
        runtime.close()


@pytest.mark.parametrize("text", [
    '{"action": "candidate", "summary": "done"}',
    '```json\n{"action": "candidate", "summary": "done"}\n```',
    # A real five-task run stopped here: the runtime appends its verification note.
    '{"action": "candidate", "summary": "done"}\n\nRuntime verification: earlier changes still lack current acceptance evidence and remain unverified.',
    'Result:\n{"action": "candidate", "summary": "done"}',
])
def test_reply_is_the_first_json_object_in_the_final_text(text):
    from app.project_runner.runner import object_reply
    assert object_reply(text) == {"action": "candidate", "summary": "done"}


@pytest.mark.parametrize("text", ["no json here", "[1, 2]", '{"action": "candidate", "summary": '])
def test_reply_without_a_complete_json_object_is_rejected(text):
    from app.project_runner.runner import object_reply
    with pytest.raises(ValueError):
        object_reply(text)


def test_malformed_plan_or_worker_reply_goes_back_to_the_model(tmp_path):
    bad_plan = reply({"tasks": [{"instructions": "No acceptance given"}]})
    service, runtime, platform, _, source = build(tmp_path, [bad_plan, reply(PLAN),
        ModelResponse(text="I finished the work.", usage=ModelUsage(5, 0, 5)),
        reply({"action": "candidate", "summary": "Implemented and checked"})])
    try:
        run = control(service, create(service, source), "start")
        for _ in range(6):
            cycle(service)
        final = service.project_store.get(run["id"])
        assert final["state"] == "ready_for_review", final["reason"]
        assert final["replyRejections"] == 0 and not final["replyNote"]
        assert "acceptance criteria" in str(platform.requests[1].messages)  # the planner was told why
        assert "could not accept your previous final reply" in str(platform.requests[3].messages)
        kinds = [item["kind"] for item in service.project_store.events(run["id"], limit=200)]
        assert kinds.count("reply_rejected") == 2
    finally:
        runtime.close()


def test_repeated_malformed_replies_stop_for_review(tmp_path):
    prose = ModelResponse(text="Done, no JSON.", usage=ModelUsage(5, 0, 5))
    service, runtime, _, _, source = build(tmp_path, [reply(PLAN), prose, prose, prose])
    try:
        run = control(service, create(service, source), "start")
        for _ in range(6):
            cycle(service)
        final = service.project_store.get(run["id"])
        assert final["state"] == "needs_review" and "JSON" in final["reason"]
        assert final["episodesUsed"] == 4
    finally:
        runtime.close()


@pytest.mark.parametrize("state,result,refused,kind", [
    ("limit_reached", {"limit": "model_steps"}, False, "episode_bound"),
    ("limit_reached", {"limit": "tool_calls"}, False, "episode_bound"),
    ("limit_reached", {"limit": "no_progress", "error": "model step limit reached"}, False, "no_progress"),
    ("limit_reached", {"limit": "context"}, False, None),
    ("limit_reached", {"limit": "admission"}, True, "budget"),
    ("limit_reached", {"limit": None, "error": "tool call limit reached"}, False, "episode_bound"),  # pre-field event
    ("failed", {"providerFailure": True, "retryable": True, "providerStatus": None}, False, "provider_transient"),
    ("failed", {"providerFailure": True, "retryable": True, "providerStatus": 503}, False, "provider_transient"),
    ("failed", {"providerFailure": True, "retryable": False, "providerStatus": 402}, False, None),
    ("failed", {"providerFailure": True, "retryable": True, "providerStatus": 401}, False, None),
    # The field decides, not the text: a tool error that mentions a timeout is the work's own.
    ("failed", {"providerFailure": False, "error": "AITransportError: APITimeoutError: timed out"}, False, None),
    ("failed", {"providerFailure": None, "error": "AITransportError: APIConnectionError: Connection error."}, False, "provider_transient"),
    ("failed", {"reviewRejected": True}, False, "review_evidence"),
    ("failed", {"replyRejected": True}, False, "reply_format"),
    ("completed", {}, False, None),
])
def test_episode_outcome_comes_from_recorded_fields(state, result, refused, kind):
    from app.project_runner.runner import episode_outcome
    assert episode_outcome(state, result, refused) == kind


def test_runtime_records_limit_kind_and_provider_failure_on_terminal_events(tmp_path):
    from app.ai.errors import AITransportError
    from app.agent_runtime.contracts import AgentLimits, AgentEventKind
    tool = AgentTool(name="noop_step", description="Do nothing", input_schema={"type": "object", "properties": {}},
                     effect=ToolEffect.READ_ONLY, handler=lambda context, arguments: ToolResult(ok=True, content="nothing"))
    looping = ModelResponse(tool_calls=(ToolCall(call_id="c", name="noop_step", arguments={}),), usage=ModelUsage(5, 0, 5))
    service, runtime, platform, _, source = build(tmp_path, [reply(PLAN), looping], tools=(tool,))
    runtime.limits = AgentLimits(max_model_steps=1, transport_retries=0)
    _failing_once(platform, AITransportError("AI request failed: APIConnectionError: Connection error."))
    try:
        run = control(service, create(service, source, tokenBudget=200000), "start")
        for _ in range(3):  # plan, then the worker's provider failure settles into a backoff
            cycle(service)
        with service.project_store.transaction() as db:
            current = service.project_store._read(db, run["id"])
            current["retryAfter"] = 0
            service.project_store.save(db, current, "test_backoff_elapsed")
        for _ in range(2):  # the continued episode ends at its one-step bound
            cycle(service)
        worker = service.project_store.get(run["id"])["sessions"]["worker-0"]
        events = service.store.events(worker)
        failed = [event.data for event in events if event.kind is AgentEventKind.TURN_FAILED]
        limits = [event.data for event in events if event.kind is AgentEventKind.LIMIT_REACHED]
        # The provider outage is marked as one; the scripted platform running dry
        # afterwards is an ordinary failure of the turn, and is marked as not one.
        assert failed[0]["provider_failure"] is True and failed[0]["retryable"] is True
        assert all(data["provider_failure"] is False for data in failed[1:])
        assert limits and limits[0]["limit"] == "model_steps"
    finally:
        runtime.close()


def test_project_sessions_keep_one_scratch_directory_across_episodes(tmp_path):
    # A real reviewer lost nine episodes to files it had written to scratch
    # vanishing at every episode boundary (scratch was per turn).
    from app.agent_runtime.contracts import AgentLimits
    seen = []
    def note(context, arguments):
        scratch = context.service("run_scratch_dir")
        seen.append(str(scratch))
        marker = scratch / "notes.txt"
        found = marker.exists()
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text("kept")
        return ToolResult(ok=True, content=f"earlier notes present: {found}")
    tool = AgentTool(name="scratch_note", description="Keep a note in scratch", input_schema={"type": "object", "properties": {}},
                     effect=ToolEffect.MUTATING, handler=note)
    step = ModelResponse(tool_calls=(ToolCall(call_id="n", name="scratch_note", arguments={}),), usage=ModelUsage(5, 0, 5))
    service, runtime, platform, _, source = build(tmp_path, [reply(PLAN), step, step,
        reply({"action": "candidate", "summary": "Done"})], tools=(tool,))
    runtime.limits = AgentLimits(max_model_steps=1)
    try:
        run = control(service, create(service, source), "start")
        for _ in range(5):
            cycle(service)
        assert service.project_store.get(run["id"])["state"] == "ready_for_review"
        assert len(seen) == 2 and seen[0] == seen[1] and seen[0].endswith("session")
        assert "earlier notes present: True" in str(platform.requests[-1].messages)
        # An ordinary conversation keeps one scratch folder per turn.
        other = runtime.create_session("p", system_prompt="x")
        assert runtime._run_scratch_dir(other.session_id, "turn-a") != runtime._run_scratch_dir(other.session_id, "turn-b")
    finally:
        runtime.close()
