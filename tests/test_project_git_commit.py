"""Project Git staging and commit RPCs."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from app.agent_runtime import (
    DurableAgentRuntime,
    FileAgentSessionStore,
    PermissionMode,
    ToolRegistry,
)
from app.app_server import PROTOCOL_VERSION
from app.app_server_project_move import ProjectMovableLoomAppServerService, ProjectMovableLoomRpcController


class SilentPlatform:
    def execute_chat(self, _profile_id, _request):  # pragma: no cover - never reached
        raise AssertionError("these tests never run a turn")


def workflow(service, operation, params):
    from app.project_git_workflow import execute
    from app.app_server_project_move import JsonRpcError
    return execute(service, operation, params, JsonRpcError)


def test_repository_binding_branches_and_worktrees(service, git_repo):
    project = service.project_create({"root": str(git_repo)})["project"]
    params = {"projectId": project["id"]}
    bound = workflow(service, "bind", {**params, "repository": "example/loom"})
    assert bound["repository"] == "example/loom"
    switched = workflow(service, "switch_branch", {**params, "branch": "codex/feature", "create": True})
    assert switched["branch"] == "codex/feature"
    created = workflow(service, "create_worktree", {**params, "branch": "codex/isolated"})
    assert Path(created["project"]["root"]).is_dir()
    assert any(tree["branch"] == "codex/isolated" for tree in created["worktrees"])
    assert (git_repo / "README.md").read_text() == "hello\n"
    isolated = workflow(service, "repository", {"projectId": created["project"]["id"]})
    assert isolated["isWorktree"] is True
    assert created["isWorktree"] is False
    (git_repo / "README.md").write_text("replacement\nsecond\n", encoding="utf-8")
    diff = workflow(service, "repository", params)
    assert (diff["additions"], diff["deletions"]) == (2, 1)
    worktree = Path(created["project"]["root"])
    (worktree / "keep.txt").write_text("do not delete", encoding="utf-8")
    with pytest.raises(Exception):
        workflow(service, "remove_worktree", {**params, "path": str(worktree)})
    assert (worktree / "keep.txt").exists()
    (worktree / "keep.txt").unlink()
    workflow(service, "remove_worktree", {**params, "path": str(worktree)})
    assert not worktree.exists()
    workflow(service, "delete_branch", {**params, "branch": "codex/isolated"})


def test_workflow_rejects_bad_inputs(service, git_repo):
    project = service.project_create({"root": str(git_repo)})["project"]
    params = {"projectId": project["id"]}
    with pytest.raises(Exception):
        workflow(service, "bind", {**params, "repository": "https://evil.test/repo"})
    with pytest.raises(Exception):
        workflow(service, "switch_branch", {**params, "branch": "--detach"})
    with pytest.raises(Exception):
        workflow(service, "create_worktree", {**params, "branch": "../../escape"})
    with pytest.raises(Exception):
        workflow(service, "remove_worktree", {**params, "path": str(git_repo)})


def test_push_pull_local_remote(service, git_repo, tmp_path):
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(git_repo), "remote", "add", "origin", str(remote)], check=True)
    project = service.project_create({"root": str(git_repo)})["project"]
    params = {"projectId": project["id"]}
    pushed = workflow(service, "push", params)
    assert pushed["upstream"]
    assert pushed["ahead"] == 0
    workflow(service, "fetch", params)
    workflow(service, "pull", params)


def test_github_slug():
    from app.project_git_workflow import github_slug
    assert github_slug("git@github.com:owner/repo.git") == "owner/repo"
    assert github_slug("https://github.com/owner/repo.git") == "owner/repo"
    assert github_slug("https://github.com.evil.test/owner/repo") == ""


def test_init_non_repository(service, tmp_path):
    root = tmp_path / "plain"
    root.mkdir()
    project = service.project_create({"root": str(root)})["project"]
    params = {"projectId": project["id"]}
    assert not workflow(service, "repository", params)["isRepo"]
    assert workflow(service, "init", params)["branch"] == "main"


def test_pr_requires_published_head_and_reuses_existing(service, git_repo, monkeypatch):
    from types import SimpleNamespace
    import app.project_git_workflow as module
    project = service.project_create({"root": str(git_repo)})["project"]
    params = {"projectId": project["id"], "title": "Feature", "base": "other"}
    workflow(service, "bind", {**params, "repository": "example/loom"})
    calls = []
    open_pr = True
    class Client:
        def request(self, method, path, **kwargs):
            calls.append((method, path, kwargs))
            if method == "POST":
                return {"html_url": "https://github.com/example/loom/pull/2"}, {}
            return ([{"html_url": "https://github.com/example/loom/pull/1"}] if open_pr else []), {}
    service.connectors = SimpleNamespace(_github=object(), _client_for_bound=lambda _: Client(), github_status=lambda: {"connected": True})
    original = module._run_git
    published = False
    sha = subprocess.run(["git", "-C", str(git_repo), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    def run(root, *args, **kwargs):
        if args[0] == "ls-remote":
            return subprocess.CompletedProcess(args, 0, f"{sha}\trefs/heads/main\n" if published else "", "")
        return original(root, *args, **kwargs)
    monkeypatch.setattr(module, "_run_git", run)
    with pytest.raises(Exception, match="尚未推送"):
        workflow(service, "create_pr", params)
    assert not calls
    published = True
    result = workflow(service, "create_pr", params)
    assert result["pullRequestUrl"].endswith("/pull/1")
    assert [call[0] for call in calls] == ["GET"]
    open_pr = False
    created = workflow(service, "create_pr", params)
    assert created["pullRequestUrl"].endswith("/pull/2")
    assert calls[-1][2]["body"]["draft"] is True


@pytest.fixture()
def service(tmp_path):
    workspace = tmp_path / "default"
    workspace.mkdir()
    store = FileAgentSessionStore(tmp_path / "home")
    runtime = DurableAgentRuntime(
        platform=SilentPlatform(),
        store=store,
        tools=ToolRegistry(()),
        default_permission_mode=PermissionMode.WORKSPACE,
        auto_drain_queue=False,
    )
    built = ProjectMovableLoomAppServerService(
        runtime=runtime,
        store=store,
        model="test-model",
        default_workspace=workspace,
        default_permission_mode=PermissionMode.WORKSPACE,
    )
    yield built
    runtime.close()


@pytest.fixture()
def git_repo(tmp_path: Path) -> Path:
    if shutil.which("git") is None:
        pytest.skip("git is not available")
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "-C", str(root), "init"], check=True, capture_output=True, text=True)
    subprocess.run(["git", "-C", str(root), "config", "user.email", "loom@example.test"], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.name", "Loom Test"], check=True)
    (root / "README.md").write_text("hello\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "add", "README.md"], check=True)
    subprocess.run(["git", "-C", str(root), "commit", "-m", "test: initial"], check=True, capture_output=True, text=True)
    return root


def test_project_git_stage_unstage_and_commit(service, git_repo: Path) -> None:
    project = service.project_create({"root": str(git_repo)})["project"]
    (git_repo / "feature.txt").write_text("new feature\n", encoding="utf-8")

    staged = service.project_git_stage({"projectId": project["id"], "path": "feature.txt"})
    staged_file = next(file for file in staged["git"]["changedFiles"] if file["path"] == "feature.txt")
    assert staged_file["index"] == "A"

    unstaged = service.project_git_unstage({"projectId": project["id"], "path": "feature.txt"})
    unstaged_file = next(file for file in unstaged["git"]["changedFiles"] if file["path"] == "feature.txt")
    assert unstaged_file["status"] == "??"

    service.project_git_stage({"projectId": project["id"], "path": "feature.txt"})
    committed = service.project_git_commit({"projectId": project["id"], "message": "test: add feature"})

    assert committed["commitSha"]
    assert committed["message"] == "test: add feature"
    assert committed["git"]["changedCount"] == 0


def test_project_git_commit_is_dispatchable_and_advertised(service, git_repo: Path) -> None:
    project = service.project_create({"root": str(git_repo)})["project"]
    controller = ProjectMovableLoomRpcController(service)

    init = controller.handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": PROTOCOL_VERSION,
                "clientInfo": {"name": "pytest", "version": "1"},
            },
        }
    )

    projects = init["result"]["capabilities"]["projects"]
    assert projects["gitStage"] is True
    assert projects["gitUnstage"] is True
    assert projects["gitCommit"] is True
    assert projects["gitRepository"] is True
    assert projects["gitWorktrees"] is True
    repository_response = controller.handle({"jsonrpc": "2.0", "id": 5, "method": "project/git_repository", "params": {"projectId": project["id"]}})
    assert repository_response["result"]["isRepo"] is True

    (git_repo / "dispatch.txt").write_text("through rpc\n", encoding="utf-8")
    stage_response = controller.handle(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "project/git_stage",
            "params": {"projectId": project["id"], "path": "dispatch.txt"},
        }
    )
    assert stage_response["result"]["path"] == "dispatch.txt"


def test_project_git_rejects_unsafe_paths(service, git_repo: Path) -> None:
    project = service.project_create({"root": str(git_repo)})["project"]
    with pytest.raises(Exception):
        service.project_git_stage({"projectId": project["id"], "path": "../outside.txt"})


def test_project_git_commit_requires_staged_changes(service, git_repo: Path) -> None:
    project = service.project_create({"root": str(git_repo)})["project"]
    with pytest.raises(Exception):
        service.project_git_commit({"projectId": project["id"], "message": "test: empty"})
