"""Independent checks, version binding and bounded rework through real runtime tools."""
from copy import deepcopy
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from app.ai import ModelResponse, ToolCall
from app.agent_runtime import AgentTool, PermissionMode, ToolEffect, ToolResult
from app.agent_runtime import DurableAgentRuntime, FileAgentSessionStore, ToolRegistry
from app.agent_runtime.contracts import AgentEventKind as E
from app.agent_runtime.process_tools import exec_tool
from app.agent_runtime.tools import ToolContext
from app.agent_runtime.verification import subject_snapshot
from app.project_runner.review import assess, candidate, review_workspace, snapshot
from test_project_runner import build, create, control, cycle, reply, PLAN
from test_project_runner import Service
from test_app_server import RecordingPlatform


def write_tool():
    def write(context, args):
        context.resolve_workspace_path("app.txt").write_text(args["content"])
        return ToolResult(ok=True, content="Implementation saved")
    return AgentTool(name="review_test_write", description="Write the implementation",
        input_schema={"type": "object", "properties": {"content": {"type": "string"}}, "required": ["content"]},
        effect=ToolEffect.MUTATING, handler=write)


def writing(content):
    return ModelResponse(tool_calls=(ToolCall(call_id="write-"+content, name="review_test_write", arguments={"content": content}),))


def checking(case_id="task-1:1", expectation=PLAN["tasks"][0]["acceptance"][0], code=None):
    return ModelResponse(tool_calls=(ToolCall(call_id="check-"+case_id, name="exec", arguments={
        "argv": [sys.executable, "-B", "-c", code or "from pathlib import Path; assert Path('app.txt').read_text() == 'fixed'"],
        "wait": True, "verification": {"case_id": case_id, "expectation": expectation,
        "subject_paths": ["app.txt"], "check_kind": "functional"}}),))


PASSED = {"verdict": "passed", "summary": "Behavior exercised and passed", "checks": [{"criterionId": "task-1:1"}]}
CHANGES = {"verdict": "needs_changes", "summary": "The implementation returns the wrong result", "issues": [
    {"taskId": "task-1", "description": "Reproduce by reading app.txt; expected fixed, observed broken"}]}
CANDIDATE = {"action": "candidate", "summary": "Candidate implemented"}
EXPECTATION2 = "App file is present"
TWO_TASKS = {"tasks": [PLAN["tasks"][0], {"instructions": "Add a secondary behavior", "acceptance": [EXPECTATION2]}]}
PASSED_ALL = {**PASSED, "checks": [{"criterionId": "task-1:1"}, {"criterionId": "task-2:1"}]}


def check_both():
    second = checking("task-2:1", EXPECTATION2, "from pathlib import Path; assert Path('app.txt').is_file()")
    return ModelResponse(tool_calls=checking().tool_calls + second.tool_calls)


def setup_review(tmp_path, responses, *, service_class=None):
    options = {"tools": (write_tool(), exec_tool())}
    if service_class:
        options["service_class"] = service_class
    service, runtime, platform, workspace, source = build(tmp_path, responses, **options)
    runtime.set_permission_mode(source, PermissionMode.FULL_ACCESS)
    return service, runtime, platform, workspace, source


def drive(service, run_id, limit=16):
    for _ in range(limit):
        cycle(service)
        run = service.project_store.get(run_id)
        if run["state"] in {"ready_for_review", "needs_review", "cancelled"} and not run["activeEpisode"]:
            return run
    return service.project_store.get(run_id)


def test_real_command_failure_reworks_then_independently_verifies_whole_candidate(tmp_path):
    # A single (last) task goes straight to whole-project review.
    responses = [reply(PLAN), writing("broken"), reply(CANDIDATE), checking(), reply(CHANGES),
        writing("fixed"), reply(CANDIDATE), checking(), reply(PASSED)]
    service, runtime, platform, workspace, source = setup_review(tmp_path, responses)
    try:
        run = create(service, source, reviewPolicy="independent", maxEpisodes=12)
        control(service, run, "start")
        final = drive(service, run["id"])
        assert final["state"] == "ready_for_review", final["reason"]
        assert final["verificationStatus"] == "passed"
        assert final["reviewFailures"] == {"project": 1}
        assert final["episodesUsed"] == 5 and final["tokensUsed"] == 25
        assert final["plan"][0]["state"] == "verified"
        assert not (workspace / "app.txt").exists()
        assessments = service.project_store.assessments(run["id"])
        assert [a["verdict"] for a in assessments] == ["passed", "needs_changes"]
        assert {a["scope"] for a in assessments} == {"project"}
        assert assessments[0]["candidateDigest"] != assessments[-1]["candidateDigest"]
        worker = runtime.get_session(final["sessions"]["worker-0"])
        assert "expected fixed, observed broken" in str(worker.messages)
        for result in assessments:
            reviewer = runtime.get_session(result["sessionId"])
            assert reviewer.session_id != worker.session_id and reviewer.workspace_dir != worker.workspace_dir
            assert snapshot(reviewer.workspace_dir)["digest"] == result["candidateDigest"]
        assert len(platform.requests) == 9
    finally:
        runtime.close()


def test_claimed_pass_without_executed_evidence_cannot_pass(tmp_path):
    # Each bare pass is returned to the reviewer with the reason; it never becomes a pass.
    service, runtime, platform, _, source = setup_review(tmp_path, [reply(PLAN), writing("fixed"), reply(CANDIDATE),
                                                                    reply(PASSED), reply(PASSED), reply(PASSED)])
    try:
        run = create(service, source, reviewPolicy="independent")
        control(service, run, "start")
        final = drive(service, run["id"])
        assert final["state"] == "needs_review"
        assert "evidence" in final["reason"].lower()
        assert service.project_store.assessments(run["id"]) == []
        assert len(platform.requests) == 6
    finally:
        runtime.close()


def test_failed_review_limit_is_durable_and_does_not_rewrite_criteria(tmp_path):
    service, runtime, _, _, source = setup_review(tmp_path, [reply(PLAN), writing("broken"), reply(CANDIDATE),
        reply(CHANGES), writing("still broken"), reply(CANDIDATE), reply(CHANGES)])
    try:
        run = create(service, source, reviewPolicy="independent", maxReviewCycles=2)
        control(service, run, "start")
        final = drive(service, run["id"])
        assert final["state"] == "needs_review" and "cycle limit" in final["reason"]
        assert final["reviewFailures"]["project"] == 2
        assert final["plan"][0]["acceptance"] == PLAN["tasks"][0]["acceptance"]
        assert len(service.project_store.assessments(run["id"])) == 2
        for _ in range(2):
            cycle(service)
        assert service.project_store.get(run["id"])["episodesUsed"] == final["episodesUsed"]
    finally:
        runtime.close()


@pytest.fixture
def review_facts(tmp_path):
    service, runtime, _, workspace, source = build(tmp_path, [])
    run = create(service, source, reviewPolicy="independent")
    run.update(plan=[{"id": "task-1", **PLAN["tasks"][0]}], taskIndex=0, reviewScope="task")
    frozen = candidate(run, workspace)
    target = tmp_path / "review"
    review_workspace(run, frozen, workspace, target)
    episode = {"sessionId": "review-session", "turnId": "review-turn"}
    context = ToolContext(episode["sessionId"], episode["turnId"], target)
    # Synthetic records test gate validation; the integration above uses actual exec.
    event = SimpleNamespace(event_id="executed-check", session_id=episode["sessionId"], turn_id=episode["turnId"],
        kind=E.CHECK_RECORDED, data={"case_id": "task-1:1", "expectation": PLAN["tasks"][0]["acceptance"][0],
        "verdict": "passed", "assessment_source": "runtime_exit_code", "acceptance_evidence": True,
        "check_kind": "functional",
        "subject_snapshot": subject_snapshot(context, ["README.md"])})
    try:
        yield run, episode, frozen, workspace, target, event
    finally:
        runtime.close()


@pytest.mark.parametrize("fault", ["foreign_session", "self_assessment", "static_only", "failed", "wrong_expectation", "definition_changed", "missing", "older_pass"])
def test_gate_rejects_foreign_unexecuted_failed_or_superseded_evidence(review_facts, fault):
    run, episode, frozen, source, workspace, event = review_facts
    events = [event]
    if fault == "foreign_session":
        event.session_id = "worker-session"
    elif fault == "self_assessment":
        event.data["assessment_source"] = "assistant_assessment"
    elif fault == "static_only":
        event.data["check_kind"] = "static"
    elif fault == "failed":
        event.data["verdict"] = "failed"
    elif fault == "wrong_expectation":
        event.data["expectation"] = "weakened requirement"
    elif fault == "definition_changed":
        event.data["definition_changed"] = True
    elif fault == "missing":
        events = []
    else:
        failed = deepcopy(event)
        failed.event_id = "later-failure"
        failed.data["verdict"] = "failed"
        events.append(failed)
    passed = {**PASSED, "checks": [{"criterionId": "task-1:1", "eventId": "executed-check"}]}
    with pytest.raises(ValueError):
        assess(run, episode, passed, frozen, source, workspace, events)


@pytest.mark.parametrize("changed", ["candidate", "reviewer", "definition"])
def test_changed_source_or_definition_invalidates_review(review_facts, changed):
    run, episode, frozen, source, workspace, event = review_facts
    if changed == "definition":
        run["plan"][0]["acceptance"] = ["a different requirement"]
    else:
        root = source if changed == "candidate" else workspace
        (root / "README.md").write_text("changed after candidate snapshot")
    result = assess(run, episode, PASSED, frozen, source, workspace, [event])
    assert result["verdict"] == "stale"


def test_review_checks_may_span_episodes_but_not_candidate_sessions(tmp_path):
    service, runtime, _, _, source = setup_review(tmp_path, [reply(TWO_TASKS), writing("fixed"), reply(CANDIDATE),
        checking(), reply({"verdict": "continue", "summary": "Check saved, inspect result next episode"}),
        reply(PASSED), reply(CANDIDATE), check_both(), reply(PASSED_ALL)])
    try:
        run = create(service, source, reviewPolicy="independent", maxEpisodes=12)
        control(service, run, "start")
        final = drive(service, run["id"])
        assert final.get("verificationStatus") == "passed", final["reason"]
        assessments = service.project_store.assessments(run["id"])
        assert [a["verdict"] for a in assessments] == ["passed", "passed", "continue"]
        assert assessments[1]["sessionId"] == assessments[2]["sessionId"]
        assert assessments[0]["sessionId"] != assessments[1]["sessionId"]
    finally:
        runtime.close()


def test_restart_imports_review_once_then_runs_only_the_final_review(tmp_path):
    service, runtime, _, _, source = setup_review(tmp_path, [reply(TWO_TASKS), writing("fixed"), reply(CANDIDATE),
        checking(), reply(PASSED)])
    run = create(service, source, reviewPolicy="independent")
    control(service, run, "start")
    for _ in range(3):
        cycle(service)
    current = service.project_store.get(run["id"])
    assert current["activeEpisode"]["phase"] == "verifying"
    control(service, current, "pause")
    home, workspace = service.store.root.parent.parent, service.default_workspace
    runtime.close()
    store = FileAgentSessionStore(home)
    platform = RecordingPlatform([reply(CANDIDATE), check_both(), reply(PASSED_ALL)])
    runtime = DurableAgentRuntime(platform=platform, store=store, tools=ToolRegistry((exec_tool(),)), auto_drain_queue=False)
    service = Service(runtime=runtime, store=store, model="test-model", default_workspace=workspace)
    service.project_runner.stop.set()
    service.project_runner.thread.join()
    service.project_runner.stop.clear()
    try:
        cycle(service)
        current = service.project_store.get(run["id"])
        assert current["phase"] == "working" and current["taskIndex"] == 1 and current["control"] == "paused"
        assert len(service.project_store.assessments(run["id"])) == 1 and not platform.requests
        control(service, current, "resume")
        final = drive(service, run["id"])
        assert final.get("verificationStatus") == "passed", final["reason"]
        assert len(service.project_store.assessments(run["id"])) == 2 and len(platform.requests) == 3
    finally:
        runtime.close()


def test_budget_before_final_review_does_not_promote_task_pass_to_project_pass(tmp_path):
    service, runtime, platform, _, source = setup_review(tmp_path, [reply(PLAN), writing("fixed"), reply(CANDIDATE),
        checking(), reply(PASSED)])
    try:
        run = create(service, source, reviewPolicy="independent", maxEpisodes=2)
        control(service, run, "start")
        for _ in range(4):
            cycle(service)
        final = service.project_store.get(run["id"])
        assert final["control"] == "budget_limited" and final["reviewScope"] == "project"
        assert final["verificationStatus"] == "pending" and "verifiedCandidate" not in final
        assert final["plan"][0]["state"] == "candidate" and len(platform.requests) == 3
        service.project_review({"operationId": "fund-final-review", "runId": run["id"],
            "expectedRevision": final["revision"], "maxEpisodes": 4})
        assert drive(service, run["id"])["verificationStatus"] == "passed"
    finally:
        runtime.close()


def test_final_review_detects_later_task_regression_and_reopens_downstream_tasks(tmp_path):
    responses = [reply(TWO_TASKS), writing("fixed"), reply(CANDIDATE), checking(), reply(PASSED),
        writing("broken"), reply(CANDIDATE), check_both(), reply(CHANGES),
        writing("fixed"), reply(CANDIDATE), checking(), reply(PASSED), reply(CANDIDATE), check_both(), reply(PASSED_ALL)]
    service, runtime, _, _, source = setup_review(tmp_path, responses)
    try:
        run = create(service, source, reviewPolicy="independent", maxEpisodes=20)
        control(service, run, "start")
        final = drive(service, run["id"], limit=24)
        assert final.get("verificationStatus") == "passed", final["reason"]
        assert final["reviewFailures"] == {"project": 1}
        assert all(task["state"] == "verified" for task in final["plan"])
        assessments = service.project_store.assessments(run["id"])
        assert len(assessments) == 4  # task-1, project (regression), task-1, project
        assert [a["verdict"] for a in assessments if a["scope"] == "project"] == ["passed", "needs_changes"]
        assert len(assessments[0]["checks"]) == 2
    finally:
        runtime.close()


def test_existing_manual_candidate_can_enter_explicit_review_and_reports_staleness(tmp_path):
    service, runtime, platform, _, source = setup_review(tmp_path, [reply(PLAN), writing("fixed"), reply(CANDIDATE),
        checking(), reply(PASSED)])
    try:
        run = create(service, source, maxEpisodes=2)  # v1/manual candidate workflow
        control(service, run, "start")
        settled = drive(service, run["id"])
        params = {"operationId": "explicit-review", "runId": run["id"], "expectedRevision": settled["revision"]}
        with pytest.raises(ValueError, match="budget exhausted"):
            service.project_review(params)
        params["maxEpisodes"] = 4
        reviewed = service.project_review(params)
        assert service.project_review(params)["reviewCandidate"]["id"] == reviewed["reviewCandidate"]["id"]
        final = drive(service, run["id"])
        assert final["verificationStatus"] == "passed" and len(platform.requests) == 5
        assert service.project_read(run["id"])["verificationFreshness"] == "current"
        worker = runtime.get_session(final["sessions"]["worker-0"])
        (Path(worker.workspace_dir) / "app.txt").write_text("changed after verification")
        read = service.project_read(run["id"])
        assert read["verificationFreshness"] == "stale"
        assert read["verifiedCandidate"]["digest"] == final["verifiedCandidate"]["digest"]
    finally:
        runtime.close()


def test_production_default_runs_independent_reviews_without_title_model_calls(tmp_path):
    from app.app_server_browser_policy import BrowserPolicyLoomAppServerService, BrowserPolicyLoomRpcController
    service, runtime, platform, _, source = setup_review(tmp_path, [reply(PLAN), writing("fixed"), reply(CANDIDATE),
        checking(), reply(PASSED)], service_class=BrowserPolicyLoomAppServerService)
    try:
        rpc = BrowserPolicyLoomRpcController(service)
        run = rpc._dispatch("projectRun/create", {"operationId": "default-independent", "threadId": source,
            "objective": "Implement a working project", "tokenBudget": 1000, "maxEpisodes": 8})["run"]
        assert run["reviewPolicy"] == "independent"
        control(service, run, "start")
        final = drive(service, run["id"])
        assert final.get("verificationStatus") == "passed", final["reason"]
        assert rpc._dispatch("projectRun/get", {"runId": run["id"]})["run"]["verificationFreshness"] == "current"
        assert len(rpc._dispatch("projectRun/assessments", {"runId": run["id"]})["assessments"]) == 1
        for key, session in final["sessions"].items():
            if key.startswith("review-"):
                assert service.thread_library.read(session)["autoTitleDisabled"]
        assert len(platform.requests) == 5
    finally:
        runtime.close()


def test_candidate_change_before_dispatch_invalidates_review_without_sampling(tmp_path):
    service, runtime, platform, _, source = setup_review(tmp_path, [reply(PLAN), writing("fixed"), reply(CANDIDATE)])
    try:
        run = create(service, source, reviewPolicy="independent")
        control(service, run, "start")
        cycle(service)
        cycle(service)
        current = service.project_store.get(run["id"])
        control(service, current, "pause")
        cycle(service)
        frozen = service.project_store.get(run["id"])
        assert frozen["phase"] == "verifying" and frozen["reviewCandidate"]
        worker = runtime.get_session(frozen["sessions"]["worker-0"])
        (Path(worker.workspace_dir) / "app.txt").write_text("an external edit")
        control(service, frozen, "resume")
        cycle(service)
        invalidated = service.project_store.get(run["id"])
        assert invalidated["phase"] == "working" and invalidated["reviewCandidate"] is None
        assert invalidated["reviewFailures"] == {"project": 1}
        assert invalidated["episodesUsed"] == 2 and len(platform.requests) == 3
        assert (Path(worker.workspace_dir) / "app.txt").read_text() == "an external edit"
    finally:
        runtime.close()


@pytest.mark.parametrize("invalid", [{"verdict": {}, "summary": "invalid"},
    {"verdict": "needs_changes", "summary": "invalid", "issues": [{"taskId": {}, "description": "bad ID"}]}])
def test_malformed_review_settles_episode_instead_of_wedging_scheduler(tmp_path, invalid):
    service, runtime, _, _, source = setup_review(tmp_path, [reply(PLAN), writing("fixed"), reply(CANDIDATE), reply(invalid)])
    try:
        run = create(service, source, reviewPolicy="independent")
        control(service, run, "start")
        final = drive(service, run["id"])
        assert final["state"] == "needs_review" and final["activeEpisode"] is None
        assert not service.project_runner.error
    finally:
        runtime.close()


def test_under_evidenced_pass_goes_back_to_the_reviewer_with_the_reason(tmp_path):
    # Real run 8: the whole-project reviewer verified every criterion but filed one
    # check as check_kind=static. The pass was rightly refused, but the run died
    # instead of letting the reviewer record proper evidence.
    static = checking()
    static.tool_calls[0].arguments["verification"]["check_kind"] = "static"
    responses = [reply(PLAN), writing("fixed"), reply(CANDIDATE), static, reply(PASSED), checking(), reply(PASSED)]
    service, runtime, platform, workspace, source = setup_review(tmp_path, responses)
    try:
        run = create(service, source, reviewPolicy="independent", maxEpisodes=12)
        control(service, run, "start")
        final = drive(service, run["id"])
        assert final["state"] == "ready_for_review" and final["verificationStatus"] == "passed", final["reason"]
        assert final["reviewEvidenceRejections"] == 0 and not final["reviewNote"]
        retried = str(platform.requests[5].messages)
        assert "rejected your previous verdict" in retried and "check_kind=static" in retried
        kinds = [item["kind"] for item in service.project_store.events(run["id"], limit=200)]
        assert kinds.count("review_evidence_rejected") == 1
    finally:
        runtime.close()


def test_reviewer_artifacts_in_its_checkout_do_not_invalidate_the_candidate(tmp_path):
    # Real ledger run: test outputs the reviewer left in its own checkout were read as
    # a changed candidate; a three-episode review was discarded and unchanged work reworked.
    leaves_artifact = checking(code="from pathlib import Path; Path('reviewer-output.log').write_text('ran'); "
                                    "assert Path('app.txt').read_text() == 'fixed'")
    responses = [reply(TWO_TASKS), writing("fixed"), reply(CANDIDATE), leaves_artifact,
                 reply({"verdict": "continue", "summary": "More checks next episode"}), reply(PASSED),
                 reply(CANDIDATE), check_both(), reply(PASSED_ALL)]
    service, runtime, platform, workspace, source = setup_review(tmp_path, responses)
    try:
        run = create(service, source, reviewPolicy="independent", maxEpisodes=12)
        control(service, run, "start")
        final = drive(service, run["id"])
        assert final.get("verificationStatus") == "passed", final["reason"]
        assert final["reviewFailures"] == {}
        assert [a["verdict"] for a in service.project_store.assessments(run["id"])] == ["passed", "passed", "continue"]
    finally:
        runtime.close()
