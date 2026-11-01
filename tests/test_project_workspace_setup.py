from pathlib import Path
import uuid

import pytest

from app.project_runner.workspace import describe
from app.project_runner.service import git
from test_project_runner import build, create, Controller


def relocate(service, source, folder):
    folder.mkdir()
    session = service._load(source)
    session.workspace_dir = str(folder)
    service.store.save(session)


def prepare(service, source, folder, **extra):
    return Controller(service)._dispatch("projectRun/prepareWorkspace", {
        "threadId": source, "expectedPath": str(folder.resolve()), "confirmed": True, "action": "initialize", **extra})["workspace"]


def test_empty_folder_initializes_and_can_start_a_program_without_global_git_identity(tmp_path):
    service, runtime, platform, _, source = build(tmp_path, [])
    folder = tmp_path / "new-app"
    try:
        relocate(service, source, folder)
        assert describe(folder)["status"] == "not_git"
        result = prepare(service, source, folder)
        assert result["status"] == "ready" and result["branch"] == "main"
        assert git(folder, "ls-tree", "--name-only", "HEAD") == ""
        from app.project_runner import program
        created = program.create(service, {"threadId": source, "operationId": str(uuid.uuid4()),
            "objective": "Build this new app", "tokenBudget": 1000, "maxEpisodes": 8})
        assert service.project_store.get(created["currentRunId"])["state"] == "running"
        assert not platform.requests
    finally:
        runtime.close()


def test_initial_baseline_excludes_existing_and_staged_files_and_retry_is_idempotent(tmp_path):
    service, runtime, _, _, source = build(tmp_path, [])
    folder = tmp_path / "existing"
    try:
        relocate(service, source, folder)
        git(folder, "init", "-b", "dev")
        (folder / "notes.txt").write_text("Keep private until explicitly committed")
        git(folder, "add", "notes.txt")
        assert describe(folder)["status"] == "unborn"
        prepare(service, source, folder)
        first = git(folder, "rev-parse", "HEAD")
        assert git(folder, "ls-tree", "--name-only", "HEAD") == ""
        assert git(folder, "diff", "--cached", "--name-only") == "notes.txt"
        prepare(service, source, folder)
        assert git(folder, "rev-parse", "HEAD") == first
        assert git(folder, "branch", "--show-current") == "dev"
    finally:
        runtime.close()


def test_preparation_refuses_unconfirmed_or_changed_folder(tmp_path):
    service, runtime, _, _, source = build(tmp_path, [])
    folder = tmp_path / "blank"
    try:
        relocate(service, source, folder)
        for extra in ({"confirmed": False}, {"expectedPath": str(tmp_path)}):
            with pytest.raises(ValueError, match="Confirm"):
                prepare(service, source, folder, **extra)
        assert not (folder / ".git").exists()
    finally:
        runtime.close()


def test_nested_folder_is_not_initialized_as_another_repository(tmp_path):
    service, runtime, _, workspace, source = build(tmp_path, [])
    nested = workspace / "src"
    try:
        relocate(service, source, nested)
        assert describe(nested)["status"] == "nested"
        with pytest.raises(ValueError, match="repository root"):
            prepare(service, source, nested)
        assert not (nested / ".git").exists()
    finally:
        runtime.close()


def test_detached_checkout_can_get_an_explicit_branch_without_changing_files(tmp_path):
    service, runtime, _, folder, source = build(tmp_path, [])
    try:
        git(folder, "checkout", "--detach", "HEAD")
        head = git(folder, "rev-parse", "HEAD")
        (folder / "README.md").write_text("unsaved changes")
        result = prepare(service, source, folder, action="branch", branch="codex/build-test")
        assert result["status"] == "ready" and result["branch"] == "codex/build-test"
        assert git(folder, "rev-parse", "HEAD") == head
        assert (folder / "README.md").read_text() == "unsaved changes"
    finally:
        runtime.close()


def test_persistent_project_locks_source_folder_even_between_episodes(tmp_path):
    service, runtime, _, folder, source = build(tmp_path, [])
    try:
        create(service, source)
        with pytest.raises(ValueError, match="source folder"):
            prepare(service, source, folder)
        with pytest.raises(ValueError, match="source folder"):
            service.thread_move_project({"threadId": source, "projectId": "other"})
    finally:
        runtime.close()


def test_existing_sidebar_project_can_be_selected_without_duplication(tmp_path):
    from app.app_server_browser_policy import BrowserPolicyLoomAppServerService
    service, runtime, _, _, source = build(tmp_path, [], service_class=BrowserPolicyLoomAppServerService)
    folder = tmp_path / "other"
    folder.mkdir()
    try:
        project = service.project_create({"root": str(folder)})["project"]
        before = len(service.projects.list())
        again = service.project_create({"root": str(folder)})["project"]
        moved = service.thread_move_project({"threadId": source, "projectId": project["id"]})["thread"]
        assert again["id"] == project["id"] and len(service.projects.list()) == before
        assert moved["id"] == source and Path(moved["workspace"]).resolve() == folder.resolve()
    finally:
        runtime.close()


def test_new_empty_folder_reaches_independent_acceptance_and_integration(tmp_path):
    from test_project_review import setup_review, PLAN, writing, CANDIDATE, checking, PASSED
    from test_project_runner import reply
    from test_project_program import completion, run_program
    responses = [reply(PLAN), writing("fixed"), reply(CANDIDATE), checking(), reply(PASSED), *completion()]
    service, runtime, platform, _, source = setup_review(tmp_path, responses)
    folder = tmp_path / "brand-new"
    try:
        relocate(service, source, folder)
        prepare(service, source, folder)
        result = run_program(service, source)
        assert result["state"] == "completed", result["reason"]
        assert (folder / "app.txt").read_text() == "fixed"
        assert git(folder, "status", "--porcelain") == ""
        assert len(platform.requests) == len(responses)
    finally:
        runtime.close()
