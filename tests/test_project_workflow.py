"""Opt-in completion with actual runtime, Git and durable stage commands."""
import pytest
import uuid

from app.project_runner import workflow, integration
from app.project_runner.review import git
from app.project_runner.store import ProjectStore
from test_project_runner import create, control, cycle, reply, PLAN
from test_project_review import setup_review, writing, checking, CANDIDATE, PASSED


@pytest.fixture
def automatic(tmp_path):
    service, runtime, platform, root, source = setup_review(tmp_path, [reply(PLAN), writing("fixed"),
        reply(CANDIDATE), checking(), reply(PASSED), checking(), reply(PASSED), checking(), reply(PASSED)])
    try:
        run = create(service, source, completionPolicy="local_integrate", reviewPolicy="independent", maxEpisodes=12)
        control(service, run, "start")
        yield service, run["id"], root, platform
    finally:
        runtime.close()


def until(service, run_id, predicate):
    for _ in range(30):
        cycle(service)
        run = service.project_store.get(run_id)
        if predicate(run):
            return run
    raise AssertionError(service.project_store.get(run_id))


def test_one_start_runs_to_verified_local_integration_without_extra_model_calls(automatic):
    service, run_id, root, platform = automatic
    final = until(service, run_id, lambda r: r.get("workflowStatus") == "completed")
    assert final["state"] == "integrated"
    assert (root / "app.txt").read_text() == "fixed"
    child = service.project_store.get(final["integrationRunId"])
    assert child["integrationStatus"] == "integrated"
    assert final["tokensUsed"] == child["tokensUsed"] == 25
    assert final["episodesUsed"] == child["episodesUsed"] == 5
    assert len(platform.requests) == 9
    assert child["deliveryReceipt"]["publication"] == "pending"
    assert git(root, "symbolic-ref", "HEAD") == final["completionRef"]
    for _ in range(3):
        cycle(service)
    assert len(platform.requests) == 9


def test_pause_and_cancel_propagate_to_child_and_do_not_touch_source(automatic):
    service, run_id, root, platform = automatic
    run = until(service, run_id, lambda r: bool(r.get("integrationRunId")))
    paused = control(service, run, "pause")
    child = service.project_store.get(run["integrationRunId"])
    assert child["control"] == "paused"
    count = len(platform.requests)
    for _ in range(3):
        cycle(service)
    assert len(platform.requests) == count
    cancelled = control(service, paused, "cancel")
    assert cancelled["state"] == "cancelled"
    assert service.project_store.get(child["id"])["state"] == "cancelled"
    assert not (root / "app.txt").exists()

def test_saved_prepare_command_recovers_child_after_reopen_without_duplicate(automatic, monkeypatch):
    service, run_id, root, platform = automatic
    original = integration.start
    lost = []
    def crash(service, params):
        result = original(service, params)
        if not lost:
            lost.append(result["id"])
            raise OSError("lost after child creation")
        return result
    monkeypatch.setattr(integration, "start", crash)
    blocked = until(service, run_id, lambda r: r.get("workflowStatus") == "blocked")
    assert "lost after child" in blocked["workflowError"]
    count = len(platform.requests)
    for _ in range(3):
        cycle(service)
    assert len(platform.requests) == count
    home = service.project_store.home
    service.project_store.close()
    service.project_store = ProjectStore(home)
    service.project_runner.store = service.project_store
    resumed = control(service, service.project_store.get(run_id), "resume")
    final = until(service, run_id, lambda r: r.get("workflowStatus") == "completed")
    assert final["integrationRunId"] == lost[0]
    assert len(service.project_store.list()) == 2


def test_dirty_source_blocks_once_and_explicit_resume_retries_after_fix(automatic):
    service, run_id, root, platform = automatic
    run = until(service, run_id, lambda r: r.get("workflowStep", {}).get("action") == "prepare" if r.get("workflowStep") else False)
    (root / "user.txt").write_text("user work")
    blocked = until(service, run_id, lambda r: r.get("workflowStatus") == "blocked")
    assert not (root / "app.txt").exists()
    revision, count = blocked["revision"], len(platform.requests)
    for _ in range(3):
        cycle(service)
    assert service.project_store.get(run_id)["revision"] == revision
    assert len(platform.requests) == count
    git(root, "add", "user.txt")
    git(root, "commit", "-m", "Preserve user work")
    control(service, blocked, "resume")
    final = until(service, run_id, lambda r: r.get("workflowStatus") == "completed")
    assert (root / "user.txt").read_text() == "user work"


def test_automatic_integration_does_not_reset_or_expand_exhausted_budget(automatic):
    service, run_id, root, platform = automatic
    with service.project_store.transaction() as db:
        run = service.project_store._read(db, run_id)
        run["maxEpisodes"] = 4
        service.project_store.save(db, run, "test_explicit_budget")
    blocked = until(service, run_id, lambda r: r.get("workflowStatus") == "blocked")
    assert blocked["episodesUsed"] == blocked["maxEpisodes"] == 4
    assert "budgets" in blocked["workflowError"]
    assert len(platform.requests) == 7
    assert len(service.project_store.list()) == 1
    assert not (root / "app.txt").exists()
    service.project_control({"operationId": str(uuid.uuid4()), "runId": run_id,
        "expectedRevision": blocked["revision"], "maxEpisodes": 8}, "resume")
    final = until(service, run_id, lambda r: r.get("workflowStatus") == "completed")
    assert final["episodesUsed"] == 5
    assert final["maxEpisodes"] == 8
