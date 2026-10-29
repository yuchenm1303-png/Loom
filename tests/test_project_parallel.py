"""Real concurrent Loom sessions, file-scope gates, pooled budgets and DAG merges."""
from copy import deepcopy
from pathlib import Path
import sys
import threading
import uuid

import pytest

from app.ai import ModelResponse, ToolCall
from app.agent_runtime import AgentTool, PermissionMode, ToolEffect, ToolResult
from app.agent_runtime.process_tools import exec_tool
from app.project_runner import parallel
from app.project_runner.review import criteria, git
from app.project_runner.runner import validate_plan
from app.project_runner.store import ProjectStore
from test_project_runner import build, create, control, cycle, reply


PLAN = {"tasks": [
    {"instructions": "Write alpha.txt containing A", "acceptance": ["alpha contains A"], "dependsOn": [], "fileScopes": ["alpha.txt"], "sharedResources": []},
    {"instructions": "Write beta.txt containing B", "acceptance": ["beta contains B"], "dependsOn": [], "fileScopes": ["beta.txt"], "sharedResources": []},
]}


def setup(tmp_path, *, dependencies=False, barrier=True, outside=False, budget=40, automatic=False, shared=None):
    plan = deepcopy(PLAN)
    if dependencies:
        plan["tasks"][1]["dependsOn"] = ["task-1"]
    if shared == "files":
        for task in plan["tasks"]:
            task["fileScopes"] = ["alpha.txt", "beta.txt"]
    elif shared == "resources":
        plan["tasks"][0]["sharedResources"] = ["Database"]
        plan["tasks"][1]["sharedResources"] = ["database"]
    rendezvous = threading.Barrier(2) if barrier else None
    lock = threading.Lock()
    stats = {"active": 0, "peak": 0, "workspaces": set()}
    def write(context, args):
        with lock:
            stats["active"] += 1
            stats["peak"] = max(stats["peak"], stats["active"])
            stats["workspaces"].add(str(context.resolve_workspace_path(".")))
        try:
            if rendezvous:
                rendezvous.wait(12)
            if dependencies and args["path"] == "beta.txt":
                assert context.resolve_workspace_path("alpha.txt").read_text() == "A"
            context.resolve_workspace_path(args["path"]).write_text(args["value"])
            if outside:
                context.resolve_workspace_path("unclaimed.txt").write_text("must not merge")
            return ToolResult(ok=True, content="Assigned implementation saved")
        finally:
            with lock:
                stats["active"] -= 1
    tool = AgentTool(name="parallel_test_write", description="Write task output", input_schema={"type": "object",
        "properties": {"path": {"type": "string"}, "value": {"type": "string"}}, "required": ["path", "value"]}, effect=ToolEffect.MUTATING, handler=write)
    service, runtime, platform, root, source = build(tmp_path, [], tools=(tool, exec_tool()))
    runtime.set_permission_mode(source, PermissionMode.FULL_ACCESS)
    seen = {}
    def execute(_profile, request):
        platform.requests.append(request)
        run = service.project_store.session_run(request.session_id)
        with lock:
            index = seen.get(request.session_id, 0)
            seen[request.session_id] = index + 1
        if run["phase"] == "planning":
            return reply(plan)
        if run["phase"] == "working":
            task_id = run["plan"][run["taskIndex"]]["id"]
            path, value = ("alpha.txt", "A") if task_id == "task-1" else ("beta.txt", "B")
            return (ModelResponse(tool_calls=(ToolCall(call_id="write-"+task_id, name="parallel_test_write", arguments={"path": path, "value": value}),))
                    if index == 0 else reply({"action": "candidate", "summary": "Assigned implementation ready"}))
        required = criteria(run)
        if index == 0:
            calls = []
            for item in required:
                path, value = ("alpha.txt", "A") if item["taskId"] == "task-1" else ("beta.txt", "B")
                calls.append(ToolCall(call_id="check-"+item["id"], name="exec", arguments={
                    "argv": [sys.executable, "-B", "-c", f"from pathlib import Path; assert Path('{path}').read_text() == '{value}'"], "wait": True,
                    "verification": {"case_id": item["id"], "expectation": item["expectation"], "subject_paths": [path], "check_kind": "functional"}}))
            return ModelResponse(tool_calls=tuple(calls))
        return reply({"verdict": "passed", "summary": "Actual file behavior checked", "checks": [{"criterionId": item["id"]} for item in required]})
    platform.execute_chat = execute
    run = create(service, source, maxWorkers=2, reviewPolicy="independent", maxEpisodes=budget,
                 completionPolicy="local_integrate" if automatic else "manual")
    control(service, run, "start")
    return service, runtime, platform, root, run["id"], stats


def until(service, run_id, predicate):
    for _ in range(60):
        cycle(service)
        run = service.project_store.get(run_id)
        if predicate(run):
            return run
    raise AssertionError(service.project_store.get(run_id))


def test_two_workers_really_overlap_and_aggregate_requires_fresh_project_review(tmp_path):
    service, runtime, platform, root, run_id, stats = setup(tmp_path)
    try:
        final = until(service, run_id, lambda r: r["state"] == "ready_for_review")
        assert stats["peak"] == 2 and len(stats["workspaces"]) == 2
        assert final["verificationStatus"] == "passed"
        assert all(item["state"] == "merged" for item in final["teamChildren"])
        assert final["episodesUsed"] == 8 and final["tokensUsed"] == 40
        assert not (root / "alpha.txt").exists() and not (root / "beta.txt").exists()
        aggregate = service.project_store.home / "workspaces" / run_id
        assert (aggregate / "alpha.txt").read_text() == "A" and (aggregate / "beta.txt").read_text() == "B"
        assessment = service.project_store.assessments(run_id)[0]
        assert assessment["scope"] == "project" and len(assessment["checks"]) == 2
    finally:
        runtime.close()


def test_dependencies_start_on_merged_predecessor_and_parallel_root_auto_integrates(tmp_path):
    service, runtime, _, root, run_id, stats = setup(tmp_path, dependencies=True, barrier=False, automatic=True)
    try:
        final = until(service, run_id, lambda r: r.get("workflowStatus") == "completed")
        assert stats["peak"] == 1
        assert (root / "alpha.txt").read_text() == "A" and (root / "beta.txt").read_text() == "B"
        first, second = final["teamChildren"]
        assert service.project_store.get(second["runId"])["baseCommit"] == first["commit"]
        # The source never advanced, so the accepted aggregate integrates without a third review.
        assert final["episodesUsed"] == 8 and final["tokensUsed"] == 40
        assert service.project_store.get(final["integrationRunId"])["integrationTarget"]["reviewReused"] is True
    finally:
        runtime.close()


@pytest.mark.parametrize("outside", [False, True])
def test_scope_and_shared_budget_fail_closed_without_reset(tmp_path, outside):
    service, runtime, _, root, run_id, _ = setup(tmp_path, outside=outside, budget=3)
    try:
        blocked = until(service, run_id, lambda r: r["control"] == "paused")
        assert blocked["episodesUsed"] == blocked["maxEpisodes"] == 3
        assert ("outside" if outside else "budget") in blocked["reason"].lower()
        assert not (root / "unclaimed.txt").exists()
        assert not any(item["state"] == "merged" for item in blocked["teamChildren"])
        if not outside:
            service.project_control({"operationId": str(uuid.uuid4()), "runId": run_id,
                "expectedRevision": blocked["revision"], "maxEpisodes": 40}, "resume")
            final = until(service, run_id, lambda r: r["state"] == "ready_for_review")
            assert final["episodesUsed"] == 8
    finally:
        runtime.close()


def test_pause_and_cancel_prevent_child_dispatch_even_after_sqlite_reopen(tmp_path):
    service, runtime, platform, root, run_id, _ = setup(tmp_path, barrier=False)
    try:
        parent = until(service, run_id, lambda r: len(r.get("teamChildren", [])) == 2)
        paused = control(service, parent, "pause")
        count = len(platform.requests)
        home = service.project_store.home
        service.project_store.close()
        service.project_store = ProjectStore(home)
        service.project_runner.store = service.project_store
        for _ in range(3):
            cycle(service)
        assert len(platform.requests) == count == 1
        control(service, paused, "cancel")
        assert all(service.project_store.get(item["runId"])["state"] == "cancelled" for item in parent["teamChildren"])
        assert not (root / "alpha.txt").exists()
    finally:
        runtime.close()


def test_plan_defaults_serialize_and_scope_claims_are_conservative():
    raw = {"tasks": [{"instructions": "one", "acceptance": ["works"]}, {"instructions": "two", "acceptance": ["works"]}]}
    plan = parallel.configure(validate_plan(raw), raw["tasks"])
    assert plan[1]["dependsOn"] == ["task-1"] and parallel.overlap(plan[0]["fileScopes"], ["src/a.py"])
    assert parallel.overlap(["src"], ["SRC/a.py"])
    assert not parallel.overlap(["src/a.py"], ["src/b.py"])
    for bad in ["../escape", "/absolute", "C:/drive", ".GIT/config", "*"]:
        value = deepcopy(PLAN)
        value["tasks"][0]["fileScopes"] = [bad]
        with pytest.raises(ValueError):
            parallel.configure(validate_plan(value), value["tasks"])
    value = deepcopy(PLAN)
    value["tasks"][0]["dependsOn"] = ["task-2"]
    with pytest.raises(ValueError):
        parallel.configure(validate_plan(value), value["tasks"])


def test_aggregate_checkout_loss_reconciles_intent_without_replaying_model_work(tmp_path, monkeypatch):
    service, runtime, _, root, run_id, _ = setup(tmp_path)
    original = parallel.git
    lost = []
    def crash(path, *args, **kwargs):
        result = original(path, *args, **kwargs)
        if "checkout" in args and not lost:
            lost.append(args[-1])
            raise OSError("lost after aggregate checkout")
        return result
    monkeypatch.setattr(parallel, "git", crash)
    try:
        blocked = until(service, run_id, lambda r: r["control"] == "paused")
        assert blocked["parallelMerge"]["commit"] == lost[0]
        home = service.project_store.home
        service.project_store.close()
        service.project_store = ProjectStore(home)
        service.project_runner.store = service.project_store
        control(service, service.project_store.get(run_id), "resume")
        final = until(service, run_id, lambda r: r["state"] == "ready_for_review")
        assert final["episodesUsed"] == 8 and len(final["teamChildren"]) == 2
        assert final["parallelMerge"] is None
        assert not (root / "alpha.txt").exists()
    finally:
        runtime.close()


def test_aggregate_conflict_preserves_user_commit_and_original_source(tmp_path, monkeypatch):
    service, runtime, _, root, run_id, _ = setup(tmp_path)
    original = parallel.aggregate
    touched = []
    def conflict(service, parent, child, workspace):
        if not touched:
            name = child["plan"][0]["fileScopes"][0]
            (workspace / name).write_text("user version")
            git(workspace, "add", name)
            git(workspace, "commit", "-m", "User work in aggregate")
            touched.append((name, git(workspace, "rev-parse", "HEAD")))
        return original(service, parent, child, workspace)
    monkeypatch.setattr(parallel, "aggregate", conflict)
    try:
        blocked = until(service, run_id, lambda r: r["control"] == "paused")
        assert "CONFLICT" in blocked["reason"]
        workspace = service.project_store.home / "workspaces" / run_id
        assert git(workspace, "rev-parse", "HEAD") == touched[0][1]
        assert (workspace / touched[0][0]).read_text() == "user version"
        assert not (root / touched[0][0]).exists()
        control(service, blocked, "cancel")
    finally:
        runtime.close()


@pytest.mark.parametrize("shared", ["files", "resources"])
def test_claim_conflicts_serialize_without_an_explicit_dependency(tmp_path, shared):
    service, runtime, _, _, run_id, stats = setup(tmp_path, barrier=False, shared=shared)
    try:
        final = until(service, run_id, lambda r: r["state"] == "ready_for_review")
        first, second = final["teamChildren"]
        assert stats["peak"] == 1
        assert service.project_store.get(second["runId"])["baseCommit"] == first["commit"]
    finally:
        runtime.close()


def test_project_sessions_do_not_spawn_unbudgeted_idle_models(tmp_path, monkeypatch):
    from app.agent_runtime.idle_compaction import IdleCompaction
    scheduled = []
    monkeypatch.setattr(IdleCompaction, "schedule", lambda self, session, observed: scheduled.append(session.session_id))
    service, runtime, _, _, run_id, _ = setup(tmp_path, barrier=False)
    try:
        source_id = service.project_store.get(run_id)["sourceThreadId"]
        runtime.idle_compaction.schedule(runtime.get_session(source_id), 100000)
        until(service, run_id, lambda r: r["state"] == "ready_for_review")
        assert scheduled == [source_id]
    finally:
        runtime.close()
