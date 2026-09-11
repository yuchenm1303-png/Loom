from __future__ import annotations

from pathlib import Path

import pytest

from app.agent_runtime import (
    DurableAgentRuntime,
    FileAgentSessionStore,
    PermissionMode,
    ToolRegistry,
)
from app.app_server_project_move import (
    ProjectMovableLoomAppServerService,
    ProjectMovableLoomRpcController,
)
from app.app_server import PROTOCOL_VERSION


class SilentPlatform:
    def execute_chat(self, _profile_id, _request):  # pragma: no cover - turns are never run
        raise AssertionError("these tests never run a turn")


@pytest.fixture()
def service(tmp_path: Path):
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


def _folder(tmp_path: Path, name: str) -> Path:
    folder = tmp_path / name
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def test_unfiled_thread_can_move_into_and_between_projects(service, tmp_path: Path) -> None:
    loose = _folder(tmp_path, "loose")
    alpha_root = _folder(tmp_path, "alpha")
    beta_root = _folder(tmp_path, "beta")
    alpha = service.project_create({"root": str(alpha_root)})["project"]
    beta = service.project_create({"root": str(beta_root)})["project"]
    thread = service.thread_start({"workspace": str(loose)})["thread"]

    moved_alpha = service.thread_move_project(
        {"threadId": thread["id"], "projectId": alpha["id"]}
    )["thread"]
    assert moved_alpha["projectId"] == alpha["id"]
    assert Path(moved_alpha["workspace"]) == alpha_root.resolve()

    moved_beta = service.thread_move_project(
        {"threadId": thread["id"], "projectId": beta["id"]}
    )["thread"]
    assert moved_beta["projectId"] == beta["id"]
    assert Path(moved_beta["workspace"]) == beta_root.resolve()

    listing = service.project_list({})
    counts = {entry["id"]: entry["threadCount"] for entry in listing["projects"]}
    assert counts[alpha["id"]] == 0
    assert counts[beta["id"]] == 1


def test_move_to_no_project_is_sticky_and_keeps_workspace(service, tmp_path: Path) -> None:
    project_root = _folder(tmp_path, "alpha")
    project = service.project_create({"root": str(project_root)})["project"]
    thread = service.thread_start({"projectId": project["id"]})["thread"]

    moved = service.thread_move_project({"threadId": thread["id"], "projectId": ""})["thread"]
    assert moved["projectId"] == ""
    assert Path(moved["workspace"]) == project_root.resolve()

    # Normal project listing performs legacy workspace adoption. The explicit
    # unfiled choice must still win rather than silently filing the thread back.
    listing = service.project_list({})
    assert listing["unfiledThreadCount"] == 1
    assert listing["projects"][0]["threadCount"] == 0
    assert service.thread_list({})["threads"][0]["projectId"] == ""


def test_removing_project_does_not_recreate_it_on_next_list(service, tmp_path: Path) -> None:
    project_root = _folder(tmp_path, "alpha")
    project = service.project_create({"root": str(project_root)})["project"]
    thread = service.thread_start({"projectId": project["id"]})["thread"]

    service.project_remove({"projectId": project["id"]})

    listing = service.project_list({})
    assert listing["projects"] == []
    assert listing["unfiledThreadCount"] == 1
    assert service.thread_read({"threadId": thread["id"]})["thread"]["projectId"] == ""


def test_move_project_rpc_is_dispatchable(service, tmp_path: Path) -> None:
    project = service.project_create({"root": str(_folder(tmp_path, "alpha"))})["project"]
    thread = service.thread_start({"workspace": str(_folder(tmp_path, "loose"))})["thread"]
    controller = ProjectMovableLoomRpcController(service)
    controller.handle(
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

    response = controller.handle(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "thread/move_project",
            "params": {"threadId": thread["id"], "projectId": project["id"]},
        }
    )

    assert response["result"]["thread"]["projectId"] == project["id"]
