"""Merged results need new executed acceptance before touching the source branch."""
import pytest

from app.project_runner import integration
from app.project_runner.review import git
from app.project_runner.store import ProjectStore
from app.project_git_workflow import repository_operation_lock
from test_project_delivery import accepted, request
from test_project_review import checking, drive, PASSED, CHANGES, writing, CANDIDATE
from test_project_runner import Controller, reply


def prepare(accepted):
    service, run, root, platform = accepted
    delivered = service.project_deliver(request(run))
    (root / "upstream.txt").write_text("preserve new work")
    git(root, "add", "upstream.txt")
    git(root, "commit", "-m", "Source advanced")
    params = {**request(delivered), "maxEpisodes": 16}
    child = Controller(service)._dispatch("projectRun/prepareIntegration", params)["run"]
    assert integration.start(service, params)["id"] == child["id"]
    return service, child, root, platform


def accept_child(service, child, platform):
    platform.responses.extend([checking(code="from pathlib import Path; assert Path('app.txt').read_text() == 'fixed'; assert Path('upstream.txt').read_text() == 'preserve new work'"), reply(PASSED)])
    child = drive(service, child["id"])
    assert child["state"] == "ready_for_review", child
    return service.project_deliver(request(child))


def test_merge_is_new_independent_review_then_fast_forward_with_crash_recovery(accepted, monkeypatch):
    service, child, root, platform = prepare(accepted)
    head = git(root, "rev-parse", "HEAD")
    assert child["verificationStatus"] == "pending"
    assert child["sessions"] == {}
    assert child["tokensUsed"] == accepted[1]["tokensUsed"]
    assert child["episodesUsed"] == accepted[1]["episodesUsed"]
    with pytest.raises(ValueError, match="reviewed and delivered"):
        integration.apply(service, request(child))
    delivered = accept_child(service, child, platform)
    assert git(root, "rev-parse", "HEAD") == head
    assert not (root / "app.txt").exists()
    original = integration.fast_forward
    def crash(run, receipt):
        original(run, receipt)
        raise OSError("interrupted after fast-forward")
    monkeypatch.setattr(integration, "fast_forward", crash)
    params = request(delivered)
    source_id = delivered["sourceThreadId"]
    service._active_sessions.add(source_id)
    try:
        with pytest.raises(ValueError, match="active session"):
            integration.apply(service, params)
    finally:
        service._active_sessions.remove(source_id)
    with pytest.raises(OSError, match="interrupted"):
        integration.apply(service, params)
    assert service.project_store.get(child["id"])["integrationStatus"] == "prepared"
    home = service.project_store.home
    service.project_store.close()
    service.project_store = ProjectStore(home)
    service.project_runner.store = service.project_store
    monkeypatch.setattr(integration, "fast_forward", original)
    result = Controller(service)._dispatch("projectRun/applyIntegration", params)["run"]
    assert result["state"] == "integrated"
    assert (root / "app.txt").read_text() == "fixed"
    assert (root / "upstream.txt").read_text() == "preserve new work"
    assert git(root, "rev-parse", "HEAD") == delivered["deliveryReceipt"]["commit"]
    assert integration.apply(service, params) == result


def test_source_advancement_and_dirty_files_stop_apply_without_overwrite(accepted):
    service, child, root, platform = prepare(accepted)
    delivered = accept_child(service, child, platform)
    params = request(delivered)
    branch = git(root, "symbolic-ref", "--short", "HEAD")
    git(root, "switch", "-c", "another-branch")
    with pytest.raises(ValueError, match="Source branch changed"):
        integration.apply(service, params)
    git(root, "switch", branch)
    (root / "README.md").write_text("uncommitted user edits")
    with pytest.raises(ValueError, match="uncommitted"):
        integration.apply(service, params)
    assert (root / "README.md").read_text() == "uncommitted user edits"
    git(root, "add", "README.md")
    git(root, "commit", "-m", "Concurrent source work")
    head = git(root, "rev-parse", "HEAD")
    with pytest.raises(ValueError, match="Source advanced"):
        integration.apply(service, params)
    assert git(root, "rev-parse", "HEAD") == head
    assert not (root / "app.txt").exists()


def test_conflict_keeps_source_and_has_no_new_review_or_model_call(accepted):
    service, run, root, platform = accepted
    delivered = service.project_deliver(request(run))
    (root / "app.txt").write_text("conflicting source implementation")
    git(root, "add", "app.txt")
    git(root, "commit", "-m", "Conflicting feature")
    head = git(root, "rev-parse", "HEAD")
    count, requests = len(service.project_store.list()), len(platform.requests)
    lock = repository_operation_lock(root)
    assert lock.acquire(blocking=False)
    try:
        with pytest.raises(ValueError, match="another Git operation"):
            integration.start(service, request(delivered))
    finally:
        lock.release()
    with pytest.raises(ValueError, match="CONFLICT"):
        integration.start(service, request(delivered))
    assert git(root, "rev-parse", "HEAD") == head
    assert not git(root, "status", "--porcelain")
    assert len(service.project_store.list()) == count
    assert len(platform.requests) == requests


def test_merged_review_rejection_can_rework_in_isolation_before_integration(accepted):
    service, child, root, platform = prepare(accepted)
    platform.responses.extend([checking(code="raise AssertionError('merged behavior needs rework')"),
                               reply(CHANGES), writing("fixed"), reply(CANDIDATE), checking(), reply(PASSED),
                               checking(), reply(PASSED)])
    result = drive(service, child["id"])
    assert result["state"] == "ready_for_review", result
    assert result["reviewFailures"] == {"project": 1}
    assert not (root / "app.txt").exists()
    delivered = service.project_deliver(request(result))
    assert integration.apply(service, request(delivered))["state"] == "integrated"
