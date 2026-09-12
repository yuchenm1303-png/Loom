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
