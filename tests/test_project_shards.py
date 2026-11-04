"""Whole-project review split across parallel reviewers (shards.py), on real Loom sessions."""
from copy import deepcopy
import sys
import threading
import uuid

import pytest

from app.ai import ModelResponse, ToolCall
from app.agent_runtime import AgentTool, PermissionMode, ToolEffect, ToolResult
from app.agent_runtime.process_tools import exec_tool
from app.project_runner import shards
from app.project_runner.review import criteria
from test_project_runner import build, create, control, cycle, reply


PLAN = {"tasks": [
    {"instructions": "Write alpha.txt containing A", "acceptance": ["alpha contains A"], "dependsOn": [],
     "fileScopes": ["alpha.txt"], "sharedResources": []},
    {"instructions": "Write beta.txt containing B", "acceptance": ["beta contains B"], "dependsOn": [],
     "fileScopes": ["beta.txt"], "sharedResources": []},
]}
FILES = {"task-1": ("alpha.txt", "A"), "task-2": ("beta.txt", "B")}


@pytest.fixture
def small_shards(monkeypatch):
    # The production thresholds start shards from 8 criteria; two one-criterion tasks stand in here.
    monkeypatch.setattr(shards, "MIN_CRITERIA", 2)
    monkeypatch.setattr(shards, "CRITERIA_PER_SHARD", 1)


def setup(tmp_path, *, workers=2, plan=PLAN, reject=None, automatic=False, budget=40, rendezvous=True):
    """``reject``: task ID whose shard asks for changes in the first review round."""
    lock = threading.Lock()
    meeting = threading.Barrier(2) if rendezvous else None
    stats = {"reviewers": 0, "peak": 0, "aggregates": 0, "rejected": 0, "writes": []}
    def write(context, args):
        context.resolve_workspace_path(args["path"]).write_text(args["value"])
        with lock:
            stats["writes"].append(args["path"])
        return ToolResult(ok=True, content="Assigned implementation saved")
    tool = AgentTool(name="shard_test_write", description="Write task output", input_schema={"type": "object",
        "properties": {"path": {"type": "string"}, "value": {"type": "string"}}, "required": ["path", "value"]},
        effect=ToolEffect.MUTATING, handler=write)
    service, runtime, platform, root, source = build(tmp_path, [], tools=(tool, exec_tool()))
    runtime.set_permission_mode(source, PermissionMode.FULL_ACCESS)
    service.project_permission_mode = PermissionMode.FULL_ACCESS
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
            path, value = FILES[task_id]
            return (ModelResponse(tool_calls=(ToolCall(call_id=f"write-{task_id}-{index}", name="shard_test_write",
                                                       arguments={"path": path, "value": value}),))
                    if index % 2 == 0 else reply({"action": "candidate", "summary": "Assigned implementation ready"}))
        if shards.aggregating(run):
            with lock:
                stats["aggregates"] += 1
            return reply({"verdict": "passed", "summary": "Tasks work together; objective covered"})
        required = criteria(run)
        shard = run.get("reviewShard")
        if index == 0:
            if shard:
                with lock:
                    stats["reviewers"] += 1
                    stats["peak"] = max(stats["peak"], stats["reviewers"])
                if meeting:
                    meeting.wait(12)  # both shard reviewers are in flight at once
                with lock:
                    stats["reviewers"] -= 1
            calls = []
            for item in required:
                path, value = FILES[item["taskId"]]
                calls.append(ToolCall(call_id="check-" + item["id"], name="exec", arguments={
                    "argv": [sys.executable, "-B", "-c", f"from pathlib import Path; assert Path('{path}').read_text() == '{value}'"],
                    "wait": True, "verification": {"case_id": item["id"], "expectation": item["expectation"],
                                                   "subject_paths": [path], "check_kind": "functional"}}))
            return ModelResponse(tool_calls=tuple(calls))
        if shard and reject in shard["taskIds"] and not stats["rejected"]:
            with lock:
                stats["rejected"] += 1
            return reply({"verdict": "needs_changes", "summary": "beta needs a newline",
                          "issues": [{"taskId": reject, "description": "beta.txt must be rewritten"}]})
        return reply({"verdict": "passed", "summary": "Actual file behavior checked",
                      "checks": [{"criterionId": item["id"]} for item in required]})
    platform.execute_chat = execute
    run = create(service, source, maxWorkers=workers, reviewPolicy="independent", maxEpisodes=budget,
                 completionPolicy="local_integrate" if automatic else "manual")
    control(service, run, "start")
    return service, runtime, platform, root, run["id"], stats


def until(service, run_id, predicate, limit=80):
    for _ in range(limit):
        cycle(service)
        run = service.project_store.get(run_id)
        if predicate(run):
            return run
    raise AssertionError(service.project_store.get(run_id))


def test_plan_splits_free_tasks_and_keeps_exclusive_ones_together():
    tasks = [{"id": f"task-{n}", "acceptance": ["x"] * size, "sharedResources": resources}
             for n, (size, resources) in enumerate([(4, []), (3, []), (3, []), (2, ["browser"]), (2, None), (5, [])], 1)]
    run = {"plan": tasks, "reviewPolicy": "independent"}
    groups = shards.plan(run)
    assert groups[-1] == {"taskIds": ["task-4", "task-5"], "exclusive": True}
    free = [group["taskIds"] for group in groups[:-1]]
    assert len(free) == 3 and sorted(sum(free, [])) == ["task-1", "task-2", "task-3", "task-6"]
    assert all(group == sorted(group, key=lambda item: int(item.split("-")[1])) for group in free)  # plan order
    # Too small, a team task, manual review, or nothing to split: one reviewer.
    assert shards.plan({**run, "plan": tasks[:1]}) == []
    assert shards.plan({**run, "teamOwnerId": "x"}) == []
    assert shards.plan({**run, "reviewPolicy": "manual"}) == []
    assert shards.plan({**run, "plan": [{**task, "sharedResources": None} for task in tasks]}) == []
    assert shards.plan({**run, "plan": [{**task, "acceptance": ["x"]} for task in tasks[:3]]}) == []


def test_shards_review_in_parallel_and_the_aggregate_carries_their_evidence(tmp_path, small_shards):
    service, runtime, _, root, run_id, stats = setup(tmp_path, automatic=True)
    try:
        final = until(service, run_id, lambda r: r.get("workflowStatus") == "completed")
        assert stats["peak"] == 2 and stats["aggregates"] == 1
        state = final["reviewShards"]
        assert state["status"] == "aggregating" and len(state["children"]) == 2
        for item in state["children"]:
            child = service.project_store.get(item["runId"])
            assert child["state"] == "reviewed" and child["shardVerdict"] == "passed"
            [assessment] = service.project_store.assessments(child["id"])
            assert [check["criterionId"] for check in assessment["checks"]] == [f"{item['taskIds'][0]}:1"]
        [aggregate] = service.project_store.assessments(run_id)
        assert aggregate["scope"] == "project" and aggregate["verdict"] == "passed"
        assert sorted(check["criterionId"] for check in aggregate["checks"]) == ["task-1:1", "task-2:1"]
        assert (root / "alpha.txt").read_text() == "A" and (root / "beta.txt").read_text() == "B"
        # Shard episodes are charged to the project; their checks are kept in its registry.
        children = [r for r in service.project_store.list() if r.get("teamOwnerId") == run_id]
        assert final["tokensUsed"] == sum(r["tokensUsed"] for r in children) + 5 + 5  # + planner + whole-objective review
        overview = service.project_overview({"threadId": final["sourceThreadId"]})
        phases = overview["metrics"]["phases"]
        assert phases["shardReview"]["episodes"] == 2 and phases["projectReview"]["episodes"] == 1
        assert overview["run"]["knowledge"]["acceptedChecks"] == 2
    finally:
        runtime.close()


def test_a_shard_asking_for_changes_sends_the_run_back_to_work_once(tmp_path, small_shards):
    service, runtime, _, _, run_id, stats = setup(tmp_path, reject="task-2", rendezvous=False)
    try:
        final = until(service, run_id, lambda r: r["state"] == "ready_for_review")
        assert stats["rejected"] == 1 and stats["aggregates"] == 1
        assert final["reviewFailures"] == {"project": 1}
        kinds = [row[0] for row in service.project_store.db.execute(
            "SELECT kind FROM project_events WHERE run_id=? ORDER BY sequence", (run_id,))]
        assert kinds.count("review_shards_started") == 2 and "review_shards_need_changes" in kinds
        # The first round's shards reported and were forgotten; the second round reviewed the reworked candidate.
        shard_runs = [r for r in service.project_store.list() if (r.get("reviewShard") or {}).get("sourceRunId") == run_id]
        assert len(shard_runs) == 4 and all(r["state"] == "reviewed" for r in shard_runs)
        first = {r["reviewShard"]["candidateId"] for r in shard_runs}
        assert len(first) == 2 and final["reviewCandidate"]["id"] in first
        assert stats["writes"].count("beta.txt") == 2  # only the rejected task was redone
    finally:
        runtime.close()


def test_cancel_stops_running_shards(tmp_path, small_shards):
    service, runtime, _, _, run_id, _ = setup(tmp_path, rendezvous=False)
    try:
        parent = until(service, run_id, lambda r: (r.get("reviewShards") or {}).get("status") == "running")
        control(service, parent, "cancel")
        for child_id in shards.children(parent):
            assert service.project_store.get(child_id)["state"] in {"cancelled", "reviewed"}
        for _ in range(3):
            cycle(service)
        assert not any(r["activeEpisode"] for r in service.project_store.list())
    finally:
        runtime.close()


def test_serial_plans_without_declared_resources_keep_one_reviewer(tmp_path, small_shards):
    serial = {"tasks": [{"instructions": t["instructions"], "acceptance": t["acceptance"]} for t in PLAN["tasks"]]}
    service, runtime, _, _, run_id, stats = setup(tmp_path, workers=1, plan=serial, rendezvous=False)
    try:
        final = until(service, run_id, lambda r: r["state"] == "ready_for_review")
        assert not final.get("reviewShards") and stats["aggregates"] == 0
        assert not any(r.get("reviewShard") for r in service.project_store.list())
    finally:
        runtime.close()


def test_aggregate_pass_requires_every_criterion_from_the_shards():
    run = {"reviewShards": {"status": "aggregating", "candidateId": "c", "evidence": [{"criterionId": "task-1:1"}]},
           "reviewCandidate": {"id": "c"}, "phase": "verifying", "reviewScope": "project"}
    with pytest.raises(ValueError, match="cover every"):
        shards.aggregate_evidence(run, {"id": "c"}, {"task-1:1": {}, "task-2:1": {}})
    with pytest.raises(ValueError, match="every parallel reviewer"):
        shards.aggregate_evidence({**run, "reviewCandidate": {"id": "other"}}, {"id": "c"}, {"task-1:1": {}})
    assert shards.aggregate_evidence(run, {"id": "c"}, {"task-1:1": {}}) == [{"criterionId": "task-1:1"}]


def test_a_shard_at_the_budget_holds_the_project_and_resuming_with_more_continues(tmp_path, small_shards):
    # planner 1 + two team tasks (work, review) 4 + first shard 1 = 6: the second shard meets the limit.
    service, runtime, _, _, run_id, stats = setup(tmp_path, budget=6, rendezvous=False)
    try:
        held = until(service, run_id, lambda r: r["control"] == "budget_limited")
        assert held["reviewShards"]["status"] == "running" and len(held["shardHolds"]) == 1
        assert "parallel reviewer stopped" in held["reason"]
        for _ in range(3):  # held: nothing more is dispatched
            cycle(service)
        assert service.project_store.get(run_id)["episodesUsed"] == 6
        service.project_control({"operationId": str(uuid.uuid4()), "runId": run_id,
                                 "expectedRevision": service.project_store.get(run_id)["revision"], "maxEpisodes": 40}, "resume")
        final = until(service, run_id, lambda r: r["state"] == "ready_for_review")
        assert final["episodesUsed"] == 8 and stats["aggregates"] == 1 and final["shardHolds"] == []
    finally:
        runtime.close()
