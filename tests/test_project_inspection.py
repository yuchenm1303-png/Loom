"""Observation panels cite real candidates, Git changes and executed review events."""
import time

from app.project_runner.inspection import read, evidence
from app.project_runner.metrics import note_wait
from test_project_review import setup_review, writing, checking, drive, PLAN, CANDIDATE, PASSED, CHANGES
from test_project_runner import build, create, control, reply, cycle, Controller


def test_empty_inspection_has_no_invented_success_or_delivery(tmp_path):
    service, runtime, _, _, source = build(tmp_path, [])
    try:
        view = Controller(service)._dispatch("projectRun/inspection", {"threadId": source})
        assert view["source"] == "conversation"
        assert view["criteria"] == view["deliveries"] == view["changes"] == []
        assert view["observedAt"] <= time.time()
    finally:
        runtime.close()


def test_waits_show_current_reason_not_an_old_closed_wait(tmp_path):
    service, runtime, _, _, source = build(tmp_path, [])
    try:
        run = control(service, create(service, source), "start")
        now = time.time()
        note_wait(service.project_store, run["id"], "team", "workerCap", now=now - 30)
        note_wait(service.project_store, run["id"], "team", "fileScope", now=now - 10)
        view = read(service, {"threadId": source})
        assert [(item["code"], item["since"]) for item in view["waits"]] == [("fileScope", now - 10)]
        assert view["metrics"]["waits"]["workerCap"] == 20
        assert view["changes"][0]["error"] == "checkout_unavailable"
    finally:
        runtime.close()


def test_executed_review_evidence_and_real_file_changes_remain_version_bound(tmp_path, monkeypatch):
    service, runtime, _, _, source = setup_review(tmp_path, [reply(PLAN), writing("fixed"), reply(CANDIDATE), checking(), reply(PASSED)])
    try:
        run = create(service, source, reviewPolicy="independent")
        control(service, run, "start")
        final = drive(service, run["id"])
        assert final["verificationStatus"] == "passed"
        view = read(service, {"threadId": source})
        [criterion] = view["criteria"]
        assert criterion["status"] == "passed"
        assert criterion["evidence"]["exitCode"] == 0
        assert criterion["evidence"]["eventId"] == view["reviews"][0]["checks"][0]["eventId"]
        assert criterion["evidence"]["recordedAt"]
        assert "assert" in criterion["evidence"]["command"]
        assert "exit=0" in criterion["evidence"]["log"]
        # Aggregated reviews cite the shard's session, not the aggregating reviewer's.
        cited = criterion["evidence"]
        shard = evidence(service, {"sessionId": source, "candidateId": cited["candidateId"]},
                         {"eventId": cited["eventId"], "sessionId": cited["sessionId"], "turnId": cited["turnId"]}, {})
        assert shard["verdict"] == "passed" and shard["command"] == cited["command"]
        assert "app.txt" in [item["path"] for item in view["changes"][0]["files"]]
        assert view["deliveries"][0]["receipt"] is None  # passing a review does not deliver it
        checkout = service.project_store.home / "workspaces" / run["id"]
        (checkout / "app.txt").write_text("changed after review")
        (checkout / "笔记 file.txt").write_text("new", encoding="utf-8")
        stale = read(service, {"threadId": source})
        assert stale["freshness"] == "stale"
        assert stale["criteria"][0]["status"] == "recorded_pass"
        assert "笔记 file.txt" in [item["path"] for item in stale["changes"][0]["files"]]
        with monkeypatch.context() as missing:
            missing.setattr(service.store, "turn_events", lambda *_: [])
            assert read(service, {"threadId": source})["criteria"][0]["status"] == "unavailable"
    finally:
        runtime.close()


def test_failed_check_is_shown_instead_of_not_run(tmp_path):
    service, runtime, _, _, source = setup_review(tmp_path, [reply(PLAN), writing("broken"), reply(CANDIDATE), checking(), reply(CHANGES)])
    try:
        run = control(service, create(service, source, reviewPolicy="independent"), "start")
        for _ in range(8):
            cycle(service)
            if service.project_store.assessments(run["id"]):
                break
        view = read(service, {"threadId": source})
        assert view["reviews"][0]["verdict"] == "needs_changes"
        assert view["reviews"][0]["issues"]
        assert view["criteria"][0]["status"] == "failed"
        assert view["criteria"][0]["evidence"]["exitCode"] != 0
    finally:
        runtime.close()
