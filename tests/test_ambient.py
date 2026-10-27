import time
from pathlib import Path

import pytest

from app.ambient import AmbientScheduler


class Executor:
    def __init__(self):
        self.calls = []
        self.busy = False
        self.outcome = None
        self.cancelled = []

    def validate(self, task):
        pass

    def ready(self, thread):
        return not self.busy

    def start(self, task, run):
        self.calls.append((task.copy(), run.copy()))
        return {"turnId": run["id"], "usageStart": 0}

    def poll(self, run):
        return self.outcome

    def cancel(self, run):
        self.cancelled.append(run["id"])


def task(scheduler, **values):
    return scheduler.put({"name": "Watch", "threadId": "thread", "prompt": "Inspect changes", "kind": "event", **values})


def test_idle_due_busy_merge_and_restart_receipts(tmp_path):
    executor = Executor()
    now = [100.0]
    scheduler = AmbientScheduler(tmp_path, executor, clock=lambda: now[0])
    try:
        for _ in range(10):
            scheduler.tick()
        assert not executor.calls
        once = task(scheduler, kind="once", nextAt=105)
        scheduler.tick()
        assert not executor.calls
        executor.busy = True
        now[0] = 106
        scheduler.tick()
        assert scheduler.snapshot()["runs"][0]["status"] == "queued"
        scheduler.tick()
        assert len(scheduler.snapshot()["runs"]) == 1
        executor.busy = False
        scheduler.tick()
        assert len(executor.calls) == 1
        executor.outcome = {"status": "completed", "tokens": 12, "result": "Changed"}
        scheduler.tick()
        assert scheduler.snapshot()["tasks"][0]["tokensUsed"] == 12
        event = task(scheduler)
        executor.busy = True
        assert scheduler.event(event["id"], "a")
        assert not scheduler.event(event["id"], "a")
        assert scheduler.event(event["id"], "b")
        assert scheduler.snapshot()["runs"][-1]["merged"] == 2
    finally:
        scheduler.close()
    scheduler = AmbientScheduler(tmp_path, executor, clock=lambda: now[0])
    try:
        assert not scheduler.event(event["id"], "a")
        executor.busy = False
        scheduler.tick()
        assert len(executor.calls) == 2
        assert not scheduler.snapshot()["tasks"][0]["enabled"]
    finally:
        scheduler.close()


def test_unknown_restart_no_replay_or_old_approval_resume(tmp_path):
    executor = Executor()
    scheduler = AmbientScheduler(tmp_path, executor)
    item = task(scheduler)
    scheduler.event(item["id"], "first")
    scheduler.tick()
    executor.outcome = {"status": "waiting_approval"}
    scheduler.tick()
    scheduler.close()
    scheduler = AmbientScheduler(tmp_path, executor)
    try:
        scheduler.tick()
        snapshot = scheduler.snapshot()
        assert len(executor.calls) == 1
        assert snapshot["runs"][0]["status"] == "needs_review"
        assert not snapshot["tasks"][0]["enabled"]
        assert snapshot["notifications"][-1]["delivery"] == "local_only"
    finally:
        scheduler.close()


def test_resource_leases_do_not_expire_into_duplicate_side_effects(tmp_path):
    scheduler = AmbientScheduler(tmp_path, Executor())
    assert scheduler.acquire_resources("a", ["browser:work", "repo:main"])
    assert not scheduler.acquire_resources("b", ["browser:work", "port:3000"])
    assert scheduler.acquire_resources("c", ["port:3000"])  # failed acquisition was atomic
    scheduler.close()
    scheduler = AmbientScheduler(tmp_path, Executor())
    try:
        assert not scheduler.acquire_resources("a", ["browser:work"])
        scheduler.release_resources("a")  # caller must verify old operation stopped
        assert scheduler.acquire_resources("b", ["browser:work"])
    finally:
        scheduler.close()


def test_interval_misfires_budget_and_cancel_are_sticky(tmp_path):
    executor = Executor()
    now = [1.0]
    scheduler = AmbientScheduler(tmp_path, executor, clock=lambda: now[0])
    try:
        item = task(scheduler, kind="interval", nextAt=1, seconds=2, maxRuns=1)
        now[0] = 10000
        scheduler.tick()
        assert len(executor.calls) == 1
        assert scheduler.snapshot()["tasks"][0]["nextAt"] == 10002
        executor.outcome = {"status": "completed", "tokens": 5}
        scheduler.tick()
        now[0] = 10003
        scheduler.tick()
        assert len(executor.calls) == 1
        assert scheduler.snapshot()["runs"][-1]["status"] == "limited"
        other = task(scheduler, threadId="other")
        executor.outcome = None
        scheduler.event(other["id"], "start")
        scheduler.tick()
        scheduler.cancel(other["id"])
        assert executor.cancelled
        assert not scheduler.event(other["id"], "again")
        executor.outcome = {"status": "cancelled", "tokens": 7}
        scheduler.tick()
        assert scheduler.snapshot()["tasks"][-1]["tokensUsed"] == 7
        for _ in range(5):
            scheduler.tick()
        assert len(executor.calls) == 2
    finally:
        scheduler.close()


def test_file_source_pause_single_owner_and_short_live_run_without_ui(tmp_path):
    executor = Executor()
    path = tmp_path / "watched.txt"
    path.write_text("before")
    scheduler = AmbientScheduler(tmp_path, executor)
    try:
        with pytest.raises(RuntimeError, match="owner"):
            AmbientScheduler(tmp_path, Executor())
        task(scheduler, kind="file", path=str(path))
        scheduler.pause(True)
        path.write_text("after content")
        scheduler.start()
        time.sleep(1.2)
        assert not executor.calls
        scheduler.pause(False)
        deadline = time.monotonic() + 4
        while not executor.calls and time.monotonic() < deadline:
            time.sleep(.05)
        assert len(executor.calls) == 1
        assert executor.calls[0][1]["source"] == "file"
        assert scheduler.snapshot()["online"]
    finally:
        scheduler.close()


def test_failure_backoff_and_notification_suppression(tmp_path):
    executor = Executor()
    now = [0.0]
    scheduler = AmbientScheduler(tmp_path, executor, clock=lambda: now[0])
    try:
        item = task(scheduler)
        scheduler.event(item["id"], "1")
        scheduler.tick()
        executor.outcome = {"status": "failed", "error": "offline", "tokens": 3}
        scheduler.tick()
        scheduler.event(item["id"], "2")
        scheduler.tick()
        assert len(executor.calls) == 1
        now[0] = 3
        scheduler.tick()
        scheduler.tick()
        assert len(executor.calls) == 2
        assert len(scheduler.snapshot()["notifications"]) == 1
    finally:
        scheduler.close()


def test_edit_disable_delete_and_paginated_audit(tmp_path):
    executor = Executor()
    executor.busy = True
    scheduler = AmbientScheduler(tmp_path, executor)
    try:
        item = task(scheduler)
        scheduler.event(item["id"], "queued")
        with pytest.raises(ValueError, match="thread"):
            scheduler.put({"id": item["id"], "threadId": "other"})
        scheduler.put({"id": item["id"], "prompt": "updated", "enabled": False})
        executor.busy = False
        scheduler.tick()
        assert not executor.calls
        scheduler.put({"id": item["id"], "enabled": True})
        assert not scheduler.event(item["id"], "queued")
        scheduler.event(item["id"], "fresh")
        scheduler.tick()
        assert executor.calls[0][0]["prompt"] == "updated"
        executor.outcome = {"status": "completed", "tokens": 6}
        scheduler.tick()
        scheduler.cancel(item["id"], delete=True)
        assert not scheduler.snapshot()["tasks"]
        page = scheduler.history(limit=1)
        assert page["runs"][0]["status"] == "completed"
        assert scheduler.history(before=page["nextCursor"], limit=1)["runs"][0]["status"] == "cancelled"
    finally:
        scheduler.close()


def test_cancellation_error_cannot_undo_disable(tmp_path):
    executor = Executor()
    scheduler = AmbientScheduler(tmp_path, executor)
    try:
        item = task(scheduler)
        scheduler.event(item["id"], "1")
        scheduler.tick()
        def fail_cancel(run):
            raise RuntimeError("thread has changed")
        executor.cancel = fail_cancel
        scheduler.cancel(item["id"])
        assert not scheduler.snapshot()["tasks"][0]["enabled"]
        assert scheduler.snapshot()["runs"][0]["status"] == "needs_review"
        assert not scheduler.event(item["id"], "2")
    finally:
        scheduler.close()


def test_cancel_without_a_known_outcome_still_requires_review(tmp_path):
    executor = Executor()
    scheduler = AmbientScheduler(tmp_path, executor)
    try:
        item = task(scheduler)
        scheduler.event(item["id"], "1")
        scheduler.tick()
        scheduler.cancel(item["id"])
        executor.outcome = {"status": "needs_review", "error": "Thread changed before reconciliation"}
        scheduler.tick()
        run = scheduler.snapshot()["runs"][0]
        assert run["status"] == "needs_review"
        assert run["error"] == executor.outcome["error"]
        assert not scheduler.snapshot()["tasks"][0]["enabled"]
    finally:
        scheduler.close()


@pytest.mark.parametrize("values", [{"seconds": 0, "kind": "interval"}, {"nextAt": float("nan"), "kind": "once"}, {"maxRuns": -1}])
def test_invalid_task_rejected(tmp_path, values):
    scheduler = AmbientScheduler(tmp_path, Executor())
    try:
        with pytest.raises(ValueError):
            task(scheduler, **values)
    finally:
        scheduler.close()
