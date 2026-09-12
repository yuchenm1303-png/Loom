"""Project workspace status powers the project details panel."""

from __future__ import annotations

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


def _git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


def _require_git(root: Path) -> None:
    try:
        result = subprocess.run(["git", "--version"], capture_output=True, text=True, check=False)
    except FileNotFoundError:
        pytest.skip("git is not available")
    if result.returncode != 0:
        pytest.skip("git is not available")
    assert _git(root, "init").returncode == 0
    assert _git(root, "config", "user.email", "loom@example.invalid").returncode == 0
    assert _git(root, "config", "user.name", "Loom Test").returncode == 0


def test_workspace_status_lists_a_bounded_project_tree(service, tmp_path: Path) -> None:
    root = tmp_path / "loom"
    (root / "src" / "app").mkdir(parents=True)
    (root / "src" / "app" / "main.tsx").write_text("console.log('loom')\n", encoding="utf-8")
    (root / "node_modules").mkdir()
    (root / "node_modules" / "hidden.js").write_text("ignored\n", encoding="utf-8")

    project = service.project_create({"root": str(root)})["project"]

    status = service.project_workspace_status({"projectId": project["id"]})

    assert status["projectId"] == project["id"]
    assert status["exists"] is True
    assert status["isDirectory"] is True
    paths = {entry["path"] for entry in status["tree"]["entries"]}
    assert "src" in paths
    assert "src/app" in paths
    assert "src/app/main.tsx" in paths
    assert "node_modules" not in paths
    assert status["git"]["changedCount"] >= 0


def test_workspace_status_is_dispatchable_and_advertised(service, tmp_path: Path) -> None:
    root = tmp_path / "loom"
    root.mkdir()
    project = service.project_create({"root": str(root)})["project"]
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
    assert init["result"]["capabilities"]["projects"]["workspaceStatus"] is True

    response = controller.handle(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "project/workspace_status",
            "params": {"projectId": project["id"]},
        }
    )
    assert response["result"]["root"] == str(root.resolve())


def test_project_git_diff_returns_tracked_and_untracked_changes(service, tmp_path: Path) -> None:
    root = tmp_path / "loom"
    root.mkdir()
    _require_git(root)
    tracked = root / "src" / "main.ts"
    tracked.parent.mkdir()
    tracked.write_text("console.log('old')\n", encoding="utf-8")
    assert _git(root, "add", "src/main.ts").returncode == 0
    assert _git(root, "commit", "-m", "baseline").returncode == 0
    tracked.write_text("console.log('new')\n", encoding="utf-8")
    (root / "src" / "new.ts").write_text("export const value = 1\n", encoding="utf-8")
    project = service.project_create({"root": str(root)})["project"]

    payload = service.project_git_diff({"projectId": project["id"]})

    assert payload["projectId"] == project["id"]
    assert "src/main.ts" in payload["paths"]
    assert "src/new.ts" in payload["paths"]
    assert "-console.log('old')" in payload["diff"]
    assert "+console.log('new')" in payload["diff"]
    assert "+export const value = 1" in payload["diff"]


def test_project_git_diff_can_be_scoped_to_one_file(service, tmp_path: Path) -> None:
    root = tmp_path / "loom"
    root.mkdir()
    _require_git(root)
    (root / "a.txt").write_text("a\n", encoding="utf-8")
    assert _git(root, "add", "a.txt").returncode == 0
    assert _git(root, "commit", "-m", "baseline").returncode == 0
    (root / "a.txt").write_text("aa\n", encoding="utf-8")
    (root / "b.txt").write_text("bb\n", encoding="utf-8")
    project = service.project_create({"root": str(root)})["project"]

    payload = service.project_git_diff({"projectId": project["id"], "path": "a.txt"})

    assert payload["paths"] == ["a.txt"]
    assert "+aa" in payload["diff"]
    assert "b.txt" not in payload["diff"]


def test_project_git_diff_is_dispatchable_and_advertised(service, tmp_path: Path) -> None:
    root = tmp_path / "loom"
    root.mkdir()
    project = service.project_create({"root": str(root)})["project"]
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
    assert init["result"]["capabilities"]["projects"]["gitDiff"] is True

    response = controller.handle(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "project/git_diff",
            "params": {"projectId": project["id"]},
        }
    )
    assert response["result"]["projectId"] == project["id"]
