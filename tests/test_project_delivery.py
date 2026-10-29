"""Real Git delivery, failure reconciliation and preservation of user work."""
import uuid

import pytest

from app.project_runner import delivery
from app.project_runner.review import git, candidate
from app.project_runner.store import ProjectStore
from test_project_runner import create, control, reply, PLAN
from test_project_review import setup_review, writing, checking, CANDIDATE, PASSED, drive


@pytest.fixture
def accepted(tmp_path):
    service, runtime, platform, workspace, source = setup_review(tmp_path, [reply(PLAN), writing("fixed"),
        reply(CANDIDATE), checking(), reply(PASSED), checking(), reply(PASSED)])
    try:
        run = create(service, source, reviewPolicy="independent")
        control(service, run, "start")
        run = drive(service, run["id"])
        assert run["verificationStatus"] == "passed", run
        yield service, run, workspace, platform
    finally:
        runtime.close()


def request(run):
    return {"operationId": str(uuid.uuid4()), "runId": run["id"], "expectedRevision": run["revision"]}


def test_delivery_exports_exact_verified_bytes_preserving_dirty_source_branch(accepted):
    service, run, root, platform = accepted
    (root / "upstream.txt").write_text("source branch advanced")
    git(root, "add", "upstream.txt")
    git(root, "commit", "-m", "Unrelated source progress")
    head = git(root, "rev-parse", "HEAD")
    (root / "README.md").write_text("user changes")
    git(root, "add", "README.md")
    before = git(root, "status", "--porcelain")
    count = len(platform.requests)
    params = request(run)
    result = service.project_deliver(params)
    receipt = result["deliveryReceipt"]
    assert result["state"] == "delivered"
    assert git(root, "rev-parse", receipt["ref"]) == receipt["commit"]
    assert git(root, "show", receipt["commit"] + ":app.txt") == "fixed"
    assert git(root, "rev-parse", receipt["commit"] + "^") == run["baseCommit"]
    assert git(root, "rev-parse", "HEAD") == head
    assert git(root, "status", "--porcelain") == before
    assert (root / "README.md").read_text() == "user changes"
    assert receipt["integration"] == receipt["publication"] == "pending"
    assert service.project_deliver(params) == result
    assert len(platform.requests) == count


def test_crash_after_ref_creation_reconciles_from_persisted_intent(accepted, monkeypatch):
    service, run, root, _ = accepted
    original = delivery.publish_local
    def crash(run, receipt):
        original(run, receipt)
        raise OSError("simulated process loss after Git side effect")
    monkeypatch.setattr(delivery, "publish_local", crash)
    params = request(run)
    with pytest.raises(OSError, match="simulated"):
        service.project_deliver(params)
    current = service.project_store.get(run["id"])
    assert current["deliveryReceipt"]["status"] == "prepared"
    commit = current["deliveryReceipt"]["commit"]
    home = service.project_store.home
    service.project_store.close()
    service.project_store = ProjectStore(home)
    service.project_runner.store = service.project_store
    monkeypatch.setattr(delivery, "publish_local", original)
    result = service.project_deliver(params)
    assert result["deliveryReceipt"]["commit"] == commit
    assert result["state"] == "delivered"


def test_changed_candidate_and_stale_revision_cannot_deliver(accepted):
    service, run, root, _ = accepted
    params = request(run)
    with pytest.raises(ValueError, match="revision"):
        service.project_deliver({**params, "expectedRevision": run["revision"] - 1})
    (service.project_store.home / "workspaces" / run["id"] / "app.txt").write_text("regression")
    with pytest.raises(ValueError, match="changed"):
        service.project_deliver(params)
    assert not git(root, "for-each-ref", "--format=%(refname)", "refs/heads/codex/project-")


def test_existing_delivery_branch_is_never_overwritten(accepted):
    service, run, root, _ = accepted
    ref = f"refs/heads/codex/project-{run['id']}"
    git(root, "update-ref", ref, run["baseCommit"])
    params = request(run)
    with pytest.raises(ValueError, match="refusing to overwrite"):
        service.project_deliver(params)
    assert git(root, "rev-parse", ref) == run["baseCommit"]
    current = service.project_store.get(run["id"])
    with pytest.raises(ValueError, match="Reconcile"):
        service.project_review(request(current))


def test_manual_candidate_cannot_bypass_independent_acceptance(tmp_path):
    service, runtime, _, _, source = setup_review(tmp_path, [reply(PLAN), reply(CANDIDATE)])
    try:
        run = create(service, source)
        control(service, run, "start")
        run = drive(service, run["id"])
        with pytest.raises((ValueError, TypeError)):
            service.project_deliver(request(run))
    finally:
        runtime.close()


def export_fixture(tmp_path):
    """Export-unit fixture: verification ledger is covered by the runtime tests above."""
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init")
    git(root, "config", "user.name", "Test")
    git(root, "config", "user.email", "test@localhost")
    (root / "old.txt").write_text("delete me")
    git(root, "add", ".")
    git(root, "commit", "-m", "base")
    base = git(root, "rev-parse", "HEAD")
    (root / "old.txt").unlink()
    (root / "二进制.bin").write_bytes(bytes(range(256)))
    run = {"id": str(uuid.uuid4()), "repository": str(root), "baseCommit": base,
           "gitCommonDir": git(root, "rev-parse", "--path-format=absolute", "--git-common-dir"),
           "objective": "Export", "plan": [], "reviewScope": "project", "taskIndex": 0,
           "state": "ready_for_review", "activeEpisode": None, "verificationStatus": "passed"}
    expected = candidate(run, root)
    run.update(verifiedCandidate=expected, lastAssessment={"verdict": "passed", "scope": "project", "candidateId": expected["id"]})
    return root, run, expected


def test_export_preserves_binary_unicode_paths_and_tracked_deletions(tmp_path):
    root, run, expected = export_fixture(tmp_path)
    receipt = delivery.prepare(run, expected, root, tmp_path / "home")
    from pathlib import Path
    exported = Path(receipt["workspace"])
    assert not (exported / "old.txt").exists()
    assert (exported / "二进制.bin").read_bytes() == bytes(range(256))


def test_export_rejects_git_tree_that_differs_from_reviewed_bytes(tmp_path, monkeypatch):
    root, run, expected = export_fixture(tmp_path)
    original = delivery.git
    def changed_tree(workspace, *args, **kwargs):
        if args == ("write-tree",):
            (workspace / "二进制.bin").write_bytes(b"changed by conversion")
            original(workspace, "add", "--all")
        return original(workspace, *args, **kwargs)
    monkeypatch.setattr(delivery, "git", changed_tree)
    with pytest.raises(ValueError, match="differs from reviewed"):
        delivery.prepare(run, expected, root, tmp_path / "home")


@pytest.mark.parametrize("text", [b"print('lf')\n", b"print('crlf')\r\n"])
def test_export_keeps_reviewed_line_endings_under_autocrlf(tmp_path, text):
    # This machine's Git ships core.autocrlf=true; a reviewed LF file came back
    # CRLF from the export and a fully accepted real run could not be delivered.
    root, run, expected = export_fixture(tmp_path)
    git(root, "config", "core.autocrlf", "true")
    (root / "tally.py").write_bytes(text)
    expected = candidate(run, root)
    run.update(verifiedCandidate=expected, lastAssessment={"verdict": "passed", "scope": "project", "candidateId": expected["id"]})
    receipt = delivery.prepare(run, expected, root, tmp_path / "home")
    from pathlib import Path
    assert (Path(receipt["workspace"]) / "tally.py").read_bytes() == text
    # The commit holds exactly the reviewed bytes.
    blob = __import__("subprocess").run(["git", "-C", str(root), "cat-file", "blob", receipt["commit"] + ":tally.py"],
                                        capture_output=True).stdout
    assert blob == text


def test_fast_forward_accepts_the_users_autocrlf_checkout_of_the_delivered_tree(tmp_path):
    from app.project_runner import integration
    root, run, expected = export_fixture(tmp_path)
    git(root, "config", "core.autocrlf", "true")
    (root / "tally.py").write_bytes(b"print('lf')\n")
    expected = candidate(run, root)
    run.update(verifiedCandidate=expected, lastAssessment={"verdict": "passed", "scope": "project", "candidateId": expected["id"]})
    receipt = delivery.prepare(run, expected, root, tmp_path / "home")
    # The user's source: clean, on its branch, at the reviewed base.
    git(root, "checkout", "--", ".")
    git(root, "clean", "-fdq")
    ref = git(root, "symbolic-ref", "HEAD")
    run["integrationTarget"] = {"ref": ref, "head": git(root, "rev-parse", "HEAD")}
    integration.fast_forward(run, receipt)
    assert git(root, "rev-parse", "HEAD") == receipt["commit"]
    # The user's filters rewrote the bytes; the integrated tree is still the reviewed one.
    assert (root / "tally.py").read_bytes() == b"print('lf')\r\n"
