"""A program pursues one objective through successive milestones, each a full project run."""
import json
from pathlib import Path
import uuid

from app.ai import ModelResponse
from app.agent_runtime import ContextAgentRuntime
from app.project_runner import program
from test_project_review import setup_review, writing, checking, PLAN, CANDIDATE, PASSED
from test_project_runner import cycle, reply

COMPLETE = {"complete": True, "summary": "app.txt holds the fixed implementation the objective asks for"}


def run_program(service, source, **kwargs):
    with service.project_runner.guard:
        created = program.create(service, {"operationId": str(uuid.uuid4()), "threadId": source,
            "objective": "Implement a working project", "tokenBudget": 10000, "maxEpisodes": 40, **kwargs})
    for _ in range(40):
        cycle(service)
        current = program.get(service.project_store, created["id"])
        if current["state"] != "running":
            return current
    return program.get(service.project_store, created["id"])


def test_next_milestone_plans_from_the_integrated_repository_then_reports_completion(tmp_path, monkeypatch):
    import test_app_server
    monkeypatch.setattr(test_app_server, "DurableAgentRuntime", ContextAgentRuntime)
    responses = [reply(PLAN), writing("fixed"), reply(CANDIDATE), checking(), reply(PASSED),  # milestone 1
                 reply(COMPLETE)]                                                                # milestone 2's planner
    service, runtime, platform, workspace, source = setup_review(tmp_path, responses)
    try:
        final = run_program(service, source)
        assert final["state"] == "completed", final["reason"]
        assert [item["complete"] for item in final["milestones"]] == [False, True]
        assert (workspace / "app.txt").read_text() == "fixed"  # milestone 1 was integrated into the source
        first, second = (service.project_store.get(item["runId"]) for item in final["milestones"])
        assert first["milestone"]["index"] == 1 and second["milestone"]["index"] == 2
        assert second["baseCommit"] != first["baseCommit"]  # the next milestone starts from the integrated commit
        planner_one, planner_two = str(platform.requests[0].messages), str(platform.requests[-1].messages)
        assert "This is milestone 1" in planner_one and '"complete":true' not in planner_one
        assert "This is milestone 2" in planner_two and "Milestone 1: 1 tasks integrated" in planner_two
        assert '"complete":true' in planner_two
        # One budget across milestones: the second run got what the first left.
        assert second["tokenBudget"] == 10000 - final["milestones"][0]["tokens"]
        assert len(platform.requests) == len(responses)
    finally:
        runtime.close()


def test_the_first_milestone_cannot_declare_the_objective_complete(tmp_path):
    # Nothing has been built yet; a "complete" first plan is a malformed reply, sent back.
    responses = [reply(COMPLETE), reply(PLAN), writing("fixed"), reply(CANDIDATE), checking(), reply(PASSED),
                 reply(COMPLETE)]
    service, runtime, platform, workspace, source = setup_review(tmp_path, responses)
    try:
        final = run_program(service, source)
        assert final["state"] == "completed", final["reason"]
        first = service.project_store.get(final["milestones"][0]["runId"])
        assert not first.get("objectiveComplete") and first["plan"]
        assert "planner must return 1..16 ordered tasks" in str(platform.requests[1].messages)
    finally:
        runtime.close()


def test_a_restarted_host_never_starts_a_milestone_twice(tmp_path):
    responses = [reply(PLAN), writing("fixed"), reply(CANDIDATE), checking(), reply(PASSED), reply(COMPLETE)]
    service, runtime, platform, workspace, source = setup_review(tmp_path, responses)
    try:
        with service.project_runner.guard:
            created = program.create(service, {"operationId": "once", "threadId": source,
                "objective": "Implement a working project", "tokenBudget": 10000, "maxEpisodes": 40})
            again = program.create(service, {"operationId": "once", "threadId": source,
                "objective": "Implement a working project", "tokenBudget": 10000, "maxEpisodes": 40})
        assert again["id"] == created["id"] and len(service.project_store.list()) == 1
        # The program record is lost between creating milestone 2 and saving it (a crash):
        # advancing again finds the same run by its operation ID.
        for _ in range(20):
            cycle(service)
            if len(program.get(service.project_store, created["id"])["milestones"]) == 1:
                break
        state = program.get(service.project_store, created["id"])
        stale = {**state, "milestones": [], "currentRunId": state["milestones"][0]["runId"]}
        program._save(service.project_store, stale)
        runs_before = {run["id"] for run in service.project_store.list()}
        program.advance(service)
        assert {run["id"] for run in service.project_store.list()} == runs_before
        assert program.get(service.project_store, created["id"])["currentRunId"] == state["currentRunId"]
    finally:
        runtime.close()
