"""Where a project's time goes: scheduler waits and episode time by phase (P0 of the 2026-11-04 study)."""
import time

from app.project_runner import metrics
from app.project_runner.runner import ProjectRunner
from app.project_runner.store import ProjectStore
from test_project_parallel import setup, until


def test_waits_accumulate_switch_close_and_survive_a_reopen(tmp_path):
    store = ProjectStore(tmp_path / "state")
    metrics.note_wait(store, "run-a", "team", "workerCap", now=100.0)
    metrics.note_wait(store, "run-a", "team", "workerCap", now=105.0)  # unchanged: no write, no reset
    metrics.note_wait(store, "run-a", "team", "fileScope", now=110.0)
    metrics.note_wait(store, "run-a", "admission", "admission", now=110.0)  # an independent channel
    metrics.note_wait(store, "run-a", "team", None, now=125.0)
    metrics.note_wait(store, "run-b", "team", "workerCap", now=0.0)
    assert metrics.waits(store, ["run-a"], now=130.0) == {"workerCap": 10.0, "fileScope": 15.0, "admission": 20.0}
    store.close()
    reopened = ProjectStore(tmp_path / "state")  # an open wait is durable: a Host restart keeps counting it
    metrics.note_wait(reopened, "run-a", "admission", None, now=140.0)
    assert metrics.waits(reopened, ["run-a"], now=500.0) == {"workerCap": 10.0, "fileScope": 15.0, "admission": 30.0}
    assert metrics.waits(reopened, [], now=500.0) == {}
    reopened.close()


def test_summarize_phases_and_concurrency():
    spans = [
        {"runId": "root", "phase": "planning", "start": 0, "end": 10, "tokens": 5},
        {"runId": "t1", "phase": "working", "start": 10, "end": 40, "tokens": 30},
        {"runId": "t2", "phase": "working", "start": 20, "end": 30, "tokens": 10},
        {"runId": "t1", "phase": "verifying", "start": 40, "end": 50, "tokens": 7},
        {"runId": "root", "phase": "verifying", "start": 60, "end": None, "tokens": 0},
    ]
    kinds = {"root": "root", "t1": "team", "t2": "team"}
    summary = metrics.summarize(spans, kinds, now=70)
    assert summary["span"] == 70 and summary["peak"] == 2
    assert summary["phases"]["working"] == {"seconds": 40.0, "tokens": 40, "episodes": 2}
    assert summary["phases"]["taskReview"]["seconds"] == 10.0
    assert summary["phases"]["projectReview"] == {"seconds": 10.0, "tokens": 0, "episodes": 1}  # still running
    assert summary["concurrency"] == {"0": 10.0, "1": 50.0, "2": 10.0}


def test_admission_wait_is_only_another_projects_hold():
    now = time.time()
    run = {"id": "a", "state": "running", "control": "active", "phase": "working"}
    other = {"id": "b"}
    own_child = {"id": "c", "teamOwnerId": "a"}
    assert ProjectRunner._held_by_others(run, [other])
    assert not ProjectRunner._held_by_others(run, [other, own_child])
    assert not ProjectRunner._held_by_others(run, [])
    assert not ProjectRunner._held_by_others({**run, "control": "paused"}, [other])
    assert not ProjectRunner._held_by_others({**run, "retryAfter": now + 60}, [other])
    assert not ProjectRunner._held_by_others({**run, "phase": "parallel"}, [other])


def test_a_real_team_records_scope_waits_and_the_overview_reports_them(tmp_path):
    # Both tasks claim both files: the second is ready but must wait for the first to merge.
    service, runtime, _, _, run_id, _ = setup(tmp_path, barrier=False, shared="files")
    try:
        until(service, run_id, lambda r: r["state"] == "ready_for_review")
        source = service.project_store.get(run_id)["sourceThreadId"]
        overview = service.project_overview({"threadId": source})
        measured = overview["metrics"]
        assert measured["waits"].get("fileScope", 0) > 0 and "workerCap" not in measured["waits"]
        assert measured["peak"] == 1
        phases = measured["phases"]
        assert phases["working"]["episodes"] == 2 and phases["taskReview"]["episodes"] == 2
        assert phases["projectReview"]["episodes"] == 1 and phases["planning"]["episodes"] == 1
        assert sum(item["tokens"] for item in phases.values()) == service.project_store.get(run_id)["tokensUsed"]
        # Nothing is left open once the team is merged.
        assert not any(key.startswith(run_id) for key in service.project_store._open_waits)
    finally:
        runtime.close()
