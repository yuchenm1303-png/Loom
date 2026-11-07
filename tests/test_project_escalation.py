"""One independent review always; more only where the Host can see a reason."""
import pytest

from app.project_runner import escalation
from app.project_runner.delivery import validate
from test_project_parallel import setup, until
from test_project_runner import build, control, create, cycle, reply


def team(tmp_path, level="auto", **kwargs):
    return setup(tmp_path, barrier=False, review_level=level, **kwargs)


def test_low_risk_team_tasks_reach_the_aggregate_and_only_the_aggregate_is_reviewed(tmp_path):
    service, runtime, _, root, run_id, _ = team(tmp_path)
    try:
        final = until(service, run_id, lambda run: run["state"] == "ready_for_review")
        store = service.project_store
        for item in final["teamChildren"]:
            child = store.get(item["runId"])
            assert store.assessments(item["runId"]) == [], "no review episode of its own"
            assert child["verificationStatus"] == "waived" and child["lastAssessment"]["verdict"] == "waived"
            assert child["reviewLevel"] == "auto" and child["reviewsWaived"] == 1
        assert final["episodesUsed"] < 6
        review = store.assessments(run_id)
        assert [(item["scope"], item["verdict"]) for item in review] == [("project", "passed")], "the floor stays"
        assert len(review[0]["checks"]) == 2, "it covers every task's criteria"
        assert final["verificationStatus"] == "passed"
    finally:
        runtime.close()


def test_thorough_keeps_a_review_for_every_task(tmp_path):
    service, runtime, _, _, run_id, _ = team(tmp_path, level="thorough")
    try:
        final = until(service, run_id, lambda run: run["state"] == "ready_for_review")
        for item in final["teamChildren"]:
            assert [a["verdict"] for a in service.project_store.assessments(item["runId"])] == ["passed"]
    finally:
        runtime.close()


def test_a_declared_shared_resource_keeps_the_tasks_own_review(tmp_path):
    service, runtime, _, _, run_id, _ = team(tmp_path, shared="resources")
    try:
        final = until(service, run_id, lambda run: run["state"] == "ready_for_review")
        for item in final["teamChildren"]:
            assert [a["verdict"] for a in service.project_store.assessments(item["runId"])] == ["passed"]
    finally:
        runtime.close()


def test_a_waived_candidate_cannot_be_delivered_outside_a_team(tmp_path):
    service, runtime, _, _, run_id, _ = team(tmp_path)
    try:
        final = until(service, run_id, lambda run: run["state"] == "ready_for_review")
        child = service.project_store.get(final["teamChildren"][0]["runId"])
        expected = service.project_store.candidate(child)
        source = service.project_store.home / "workspaces" / child["id"]
        root_like = {**child, "teamOwnerId": None, "state": "ready_for_review", "activeEpisode": None}
        with pytest.raises(ValueError, match="independently passed"):
            validate(root_like, expected, source)
    finally:
        runtime.close()


SERIAL = {"tasks": [{"instructions": f"Task {n}", "acceptance": [f"Criterion {n}"]} for n in (1, 2, 3)]}


def run_serial(tmp_path, level, write=None):
    """A scripted serial run: returns the phases the model was asked to work in, in order."""
    from app.ai import ModelResponse, ToolCall
    from app.agent_runtime import AgentTool, PermissionMode, ToolEffect, ToolResult
    from app.agent_runtime.process_tools import exec_tool
    from app.project_runner.review import criteria
    import sys

    def do_write(context, arguments):
        path = context.resolve_workspace_path(arguments["path"])
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(arguments["text"])
        return ToolResult(ok=True, content="written")
    tool = AgentTool(name="serial_write", description="Write", input_schema={"type": "object", "properties": {
        "path": {"type": "string"}, "text": {"type": "string"}}, "required": ["path", "text"]}, effect=ToolEffect.MUTATING, handler=do_write)
    service, runtime, platform, root, source = build(tmp_path, [], tools=(tool, exec_tool()))
    runtime.set_permission_mode(source, PermissionMode.FULL_ACCESS)
    service.project_permission_mode = PermissionMode.FULL_ACCESS
    seen, phases = {}, []

    def execute(_profile, request):
        run = service.project_store.session_run(request.session_id)
        index = seen.get(request.session_id, 0)
        seen[request.session_id] = index + 1
        if index == 0:
            phases.append((run["phase"], run.get("reviewScope"), run["taskIndex"]))
        if run["phase"] == "planning":
            return reply(SERIAL)
        if run["phase"] == "working":
            task = run["taskIndex"]
            if index == 0:
                path, text = (write or {}).get(task, (f"t{task}.txt", "x"))
                return ModelResponse(tool_calls=(ToolCall(call_id=f"w{task}", name="serial_write", arguments={"path": path, "text": text}),))
            return reply({"action": "candidate", "summary": "Ready"})
        required = criteria(run)
        if index == 0:
            return ModelResponse(tool_calls=tuple(ToolCall(call_id="check-" + item["id"], name="exec", arguments={
                "argv": [sys.executable, "-B", "-c", "pass"], "wait": True,
                "verification": {"case_id": item["id"], "expectation": item["expectation"], "subject_paths": ["t0.txt"], "check_kind": "functional"}}) for item in required))
        return reply({"verdict": "passed", "summary": "Checked", "checks": [{"criterionId": item["id"]} for item in required]})
    platform.execute_chat = execute
    run = create(service, source, reviewPolicy="independent", maxEpisodes=40, reviewLevel=level)
    control(service, run, "start")
    return service, runtime, run["id"], phases


def serial_until_done(service, run_id):
    for _ in range(40):
        cycle(service)
        run = service.project_store.get(run_id)
        if run["state"] in {"ready_for_review", "needs_review"}:
            return run
    raise AssertionError(service.project_store.get(run_id))


def reviews(phases):
    return [(scope, task) for phase, scope, task in phases if phase == "verifying"]


def test_a_serial_low_risk_run_is_reviewed_once_at_the_end(tmp_path):
    service, runtime, run_id, phases = run_serial(tmp_path, "auto")
    try:
        final = serial_until_done(service, run_id)
        assert final["state"] == "ready_for_review", final["reason"]
        assert len(reviews(phases)) == 1 and reviews(phases)[0][0] == "project"
        assert final["reviewsWaived"] == 2
    finally:
        runtime.close()


def test_thorough_or_unset_reviews_each_task(tmp_path):
    service, runtime, run_id, phases = run_serial(tmp_path, "thorough")
    try:
        serial_until_done(service, run_id)
        assert len(reviews(phases)) == 3
    finally:
        runtime.close()


def test_a_sensitive_change_gets_its_own_review_and_the_others_do_not(tmp_path):
    service, runtime, run_id, phases = run_serial(tmp_path, "auto", write={1: ("auth/login.py", "x")})
    try:
        final = serial_until_done(service, run_id)
        assert [scope for scope, _ in reviews(phases)] == ["task", "project"], reviews(phases)
        assert final["reviewsWaived"] == 1
    finally:
        runtime.close()


@pytest.fixture
def repo(tmp_path):
    from app.project_runner.review import git
    git(tmp_path, "init")
    git(tmp_path, "config", "user.name", "t")
    git(tmp_path, "config", "user.email", "t@example.invalid")
    (tmp_path / "base.txt").write_text("one\n")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-m", "base")
    return tmp_path


def auto(**extra):
    return {"reviewLevel": "auto", "taskIndex": 0, "plan": [{"id": "task-1"}, {"id": "task-2"}], **extra}


def test_a_small_plain_change_needs_no_review_of_its_own(repo):
    (repo / "notes.txt").write_text("hello\n")
    (repo / "base.txt").write_text("two\n")
    assert escalation.reason(auto(), repo) == ""


@pytest.mark.parametrize("path", [
    ".github/workflows/ci.yml", "deploy/run.txt", "db/migrations/1.txt", "app/auth_service.py", "src/login.ts",
    "tools/secret_store.py", "Dockerfile", "docker-compose.prod.yml", "scripts/run.sh", "schema.sql", "app/sandbox/x.py",
    "app/payment.py", "server.pem", "src\\credentials\\a.txt"])
def test_sensitive_paths_are_seen(repo, path):
    target = repo / path.replace("\\", "/")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("x\n")
    assert escalation.reason(auto(), repo).startswith("touches")


@pytest.mark.parametrize("path", ["src/notes/readme.txt", "docs/guide.md", "app/models.py", "tests/test_x.py"])
def test_ordinary_paths_are_not(repo, path):
    target = repo / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("x\n")
    assert escalation.reason(auto(), repo) == ""


def test_size_comes_from_git_not_from_the_worker(repo):
    (repo / "big.txt").write_text("x\n" * (escalation.MAX_LINES + 1))
    assert "lines changed" in escalation.reason(auto(), repo)
    (repo / "big.txt").unlink()
    (repo / "base.txt").write_text("y\n" * (escalation.MAX_LINES + 1))
    assert "lines changed" in escalation.reason(auto(), repo)
    (repo / "base.txt").write_text("one\n")
    for n in range(escalation.MAX_FILES + 1):
        (repo / f"f{n}.txt").write_text("x\n")
    assert "files changed" in escalation.reason(auto(), repo)


def test_exactly_at_the_limits_is_still_small(repo):
    (repo / "big.txt").write_text("x\n" * escalation.MAX_LINES)
    assert escalation.reason(auto(), repo) == ""
    (repo / "big.txt").unlink()
    for n in range(escalation.MAX_FILES):
        (repo / f"f{n}.txt").write_text("x\n")
    assert escalation.reason(auto(), repo) == ""


def test_a_rejection_or_a_shared_resource_or_the_persons_choice_escalates(repo):
    assert "rejected" in escalation.reason(auto(reviewFailures={"project": 1}), repo)
    assert "shared" in escalation.reason(auto(plan=[{"id": "task-1", "sharedResources": ["gpu"]}, {"id": "task-2"}]), repo)
    assert escalation.reason({**auto(), "reviewLevel": "thorough"}, repo) == "asked for"
    assert escalation.reason({**auto(), "reviewLevel": None}, repo) == "asked for", "a run from before the choice"


def test_the_floor_is_never_waivable():
    last = auto(taskIndex=1)
    assert escalation.waivable(auto()) is True
    assert escalation.waivable(last) is False, "the last task of a serial run goes to the whole-project review"
    assert escalation.waivable({**last, "teamOwnerId": "root"}) is True, "a team task has the aggregate after it"
    assert escalation.waivable({**auto(), "reviewLevel": "thorough"}) is False


def test_an_unknown_level_is_refused(tmp_path):
    service, runtime, _, _, source = build(tmp_path, [])
    try:
        with pytest.raises(ValueError, match="reviewLevel"):
            create(service, source, reviewLevel="none")
    finally:
        runtime.close()
