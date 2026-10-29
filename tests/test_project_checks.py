"""Accepted checks are kept from passing independent reviews and handed to later sessions."""
import json
import shutil
from pathlib import Path
import sys
from types import SimpleNamespace
import uuid

from app.agent_runtime.contracts import AgentEventKind as E
from app.project_runner import checks
from app.project_runner.store import ProjectStore
from test_project_runner import create, control, cycle, reply
from test_project_review import setup_review, writing, checking, check_both, CANDIDATE, PASSED, PASSED_ALL, TWO_TASKS


def _event(kind, data):
    return SimpleNamespace(event_id=str(uuid.uuid4()), kind=kind, data=data)


def test_passing_review_keeps_its_cited_command_and_script_for_later_sessions(tmp_path):
    store = ProjectStore(tmp_path / "home")
    run = store.create("op", {"tokenBudget": 10, "maxEpisodes": 1})
    review_checkout, scratch = tmp_path / "review", tmp_path / "session" / "run-artifacts"
    (review_checkout / "pkg").mkdir(parents=True)
    script = scratch / "turn-1" / "check_dates.py"
    script.parent.mkdir(parents=True)
    script.write_text("print('calendar dates rejected')", encoding="utf-8")
    request = _event(E.TOOL_REQUESTED, {"call_id": "c1", "tool": "exec", "arguments": {
        "argv": [sys.executable, str(script), str(review_checkout / "pkg")], "cwd": str(review_checkout)}})
    passed = _event(E.CHECK_RECORDED, {"case_id": "task-1:2", "verdict": "passed", "expectation": "bad dates exit 2",
                                       "subject_paths": ["pkg/parse.py"], "evidence_refs": [{"call_id": "c1"}]})
    failed = _event(E.CHECK_RECORDED, {"case_id": "task-1:3", "verdict": "failed", "expectation": "x",
                                       "evidence_refs": [{"call_id": "c1"}]})
    assessment = {"verdict": "passed", "candidateId": "cand", "checks": [
        {"criterionId": "task-1:2", "eventId": passed.event_id}, {"criterionId": "task-1:3", "eventId": failed.event_id}]}
    assert checks.harvest(store, run, assessment, [request, passed, failed], review_checkout, scratch) == 1
    # A failing verdict keeps nothing.
    assert checks.harvest(store, run, {**assessment, "verdict": "needs_changes"}, [request, passed], review_checkout, scratch) == 0

    later_scratch, later_checkout = tmp_path / "later" / "scratch", tmp_path / "later" / "review"
    later_scratch.mkdir(parents=True)
    provided = checks.provide(store, run, later_scratch, later_checkout)
    assert [item["criterionId"] for item in provided] == ["task-1:2"]
    argv = provided[0]["argv"]
    copied = later_scratch / "accepted-checks" / "task-1_2" / "check_dates.py"
    assert copied.read_text(encoding="utf-8") == "print('calendar dates rejected')"
    assert argv == [sys.executable, copied.as_posix(), later_checkout.as_posix() + "/pkg"]
    assert provided[0]["cwd"] == later_checkout.as_posix()
    store.close()


def test_later_review_and_next_worker_receive_the_accepted_checks(tmp_path):
    responses = [reply(TWO_TASKS), writing("fixed"), reply(CANDIDATE), checking(), reply(PASSED),
                 reply(CANDIDATE), check_both(), reply(PASSED_ALL)]
    service, runtime, platform, workspace, source = setup_review(tmp_path, responses)
    try:
        run = create(service, source, reviewPolicy="independent", maxEpisodes=12)
        control(service, run, "start")
        for _ in range(12):
            cycle(service)
            if service.project_store.get(run["id"])["state"] != "running":
                break
        final = service.project_store.get(run["id"])
        assert final["verificationStatus"] == "passed", final["reason"]
        worker_two = str(platform.requests[5].messages)   # task-2 worker
        project_review = str(platform.requests[6].messages)
        assert "regressionChecks" in worker_two and "task-1:1" in worker_two
        assert "acceptedChecks" in project_review and "Re-run them" in project_review
        with service.project_store.lock:
            kept = [json.loads(row[0]) for row in service.project_store.db.execute("SELECT data FROM project_checks")]
        assert {item["criterionId"] for item in kept} >= {"task-1:1"}
    finally:
        runtime.close()


def test_notes_from_one_task_reach_later_episodes_once(tmp_path):
    from test_project_runner import build
    note = "cmd /c mangles the quoted non-ASCII interpreter path; call python directly"
    responses = [reply(TWO_TASKS),
                 reply({"action": "candidate", "summary": "task 1 done", "notes": [note, note, 7]}),
                 reply({"action": "candidate", "summary": "task 2 done", "notes": "not a list"})]
    service, runtime, platform, _, source = build(tmp_path, responses)
    try:
        run = control(service, create(service, source, reviewPolicy="manual"), "start")
        for _ in range(4):
            cycle(service)
        final = service.project_store.get(run["id"])
        assert final["state"] == "ready_for_review", final["reason"]
        assert note in str(platform.requests[2].messages)          # task-2's worker sees it
        assert note not in str(platform.requests[1].messages)      # nothing invented before it was written
        with service.project_store.lock:
            rows = service.project_store.db.execute("SELECT note FROM project_notes").fetchall()
        assert [row[0] for row in rows] == [note]
        knowledge = service.project_read(run["id"])["knowledge"]
        assert knowledge["notes"] == [note] and knowledge["acceptedChecks"] == 0 and knowledge["recoveries"] == {}
    finally:
        runtime.close()


def test_kept_script_carries_the_fixtures_it_names_by_absolute_path(tmp_path):
    # ledger run 10: a reviewer's probe named its fixture inside its own private scratch;
    # every later session was denied that path, rewrote the check, and was rejected.
    store = ProjectStore(tmp_path / "home")
    run = store.create("op", {"tokenBudget": 10, "maxEpisodes": 1})
    review_checkout, scratch = tmp_path / "review", tmp_path / "session" / "run-artifacts"
    review_checkout.mkdir()
    fixture = scratch / "session" / "invalid.csv"
    fixture.parent.mkdir(parents=True)
    fixture.write_text("2026-02-30,A,1\n", encoding="utf-8")
    script = scratch / "turn-1" / "check.py"
    script.parent.mkdir(parents=True)
    escaped = str(fixture).replace("\\", "\\\\")
    script.write_text(f'raw = r"{fixture}"\nescaped = "{escaped}"\nsource = r"{review_checkout}"\n'
                      'label = f"{workspace}"\n', encoding="utf-8")
    request = _event(E.TOOL_REQUESTED, {"call_id": "c1", "tool": "exec", "arguments": {
        "argv": [sys.executable, str(script)], "cwd": str(review_checkout)}})
    passed = _event(E.CHECK_RECORDED, {"case_id": "task-1:3", "verdict": "passed", "expectation": "bad rows exit 2",
                                       "evidence_refs": [{"call_id": "c1"}]})
    assessment = {"verdict": "passed", "checks": [{"criterionId": "task-1:3", "eventId": passed.event_id}]}
    assert checks.harvest(store, run, assessment, [request, passed], review_checkout, scratch) == 1
    shutil.rmtree(tmp_path / "session")  # the first reviewer's scratch is out of reach later

    later_scratch, later_checkout = tmp_path / "later" / "scratch", tmp_path / "later" / "review"
    later_scratch.mkdir(parents=True)
    checks.provide(store, run, later_scratch, later_checkout)
    kept = later_scratch / "accepted-checks" / "task-1_3"
    namespace = {"workspace": "w"}
    exec((kept / "check.py").read_text(encoding="utf-8"), namespace)
    assert Path(namespace["raw"]) == Path(namespace["escaped"]) == kept / "session" / "invalid.csv"
    assert Path(namespace["raw"]).read_text(encoding="utf-8") == "2026-02-30,A,1\n"
    assert Path(namespace["source"]) == later_checkout and namespace["label"] == "w"
    store.close()
