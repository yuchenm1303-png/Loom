"""Projects over the App Server protocol."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.agent_runtime import (
    DurableAgentRuntime,
    FileAgentSessionStore,
    PermissionMode,
    ToolRegistry,
)
from app.app_server import LoomAppServerService, LoomRpcController, PROTOCOL_VERSION


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
    built = LoomAppServerService(
        runtime=runtime,
        store=store,
        model="test-model",
        default_workspace=workspace,
        default_permission_mode=PermissionMode.WORKSPACE,
    )
    yield built
    runtime.close()


def _folder(tmp_path, name: str) -> Path:
    path = tmp_path / name
    path.mkdir(parents=True, exist_ok=True)
    return path


def _names(payload) -> list[str]:
    return [project["name"] for project in payload["projects"]]


def test_a_project_can_be_created_named_and_removed(service, tmp_path):
    folder = _folder(tmp_path, "loom")

    created = service.project_create({"root": str(folder)})["project"]
    assert created["name"] == "loom"
    assert created["threadCount"] == 0

    renamed = service.project_rename({"projectId": created["id"], "name": "Loom Desktop"})
    assert renamed["project"]["name"] == "Loom Desktop"
    assert _names(service.project_list({})) == ["Loom Desktop"]

    removed = service.project_remove({"projectId": created["id"]})
    assert removed["removed"] is True
    assert service.project_list({"adopt": False})["projects"] == []


def test_an_empty_project_still_appears(service, tmp_path):
    """The whole point: a project you have not talked in yet is still a place."""
    service.project_create({"root": str(_folder(tmp_path, "fresh"))})

    listing = service.project_list({})

    assert _names(listing) == ["fresh"]
    assert listing["projects"][0]["threadCount"] == 0


def test_existing_conversations_become_projects_without_a_setup_step(service, tmp_path):
    first = _folder(tmp_path, "alpha")
    second = _folder(tmp_path, "beta")
    service.thread_start({"workspace": str(first)})
    service.thread_start({"workspace": str(first)})
    service.thread_start({"workspace": str(second)})

    listing = service.project_list({})

    assert _names(listing) == ["alpha", "beta"]
    counts = {project["name"]: project["threadCount"] for project in listing["projects"]}
    assert counts == {"alpha": 2, "beta": 1}
    assert listing["unfiledThreadCount"] == 0


def test_a_thread_reports_the_project_it_belongs_to(service, tmp_path):
    folder = _folder(tmp_path, "loom")
    project = service.project_create({"root": str(folder)})["project"]

    thread = service.thread_start({"workspace": str(folder)})["thread"]

    assert thread["projectId"] == project["id"]
    listed = service.thread_list({})["threads"][0]
    assert listed["projectId"] == project["id"]


def test_a_thread_outside_every_project_is_unfiled(service, tmp_path):
    thread = service.thread_start({"workspace": str(_folder(tmp_path, "loose"))})

    # Nothing adopted yet, so the thread has no project to name.
    assert thread["thread"]["projectId"] == ""
    assert service.project_list({"adopt": False})["unfiledThreadCount"] == 1


def test_a_thread_can_be_started_by_project_id(service, tmp_path):
    folder = _folder(tmp_path, "loom")
    project = service.project_create({"root": str(folder)})["project"]

    thread = service.thread_start({"projectId": project["id"]})["thread"]

    assert Path(thread["workspace"]) == folder.resolve()
    assert thread["projectId"] == project["id"]


def test_starting_a_thread_by_both_project_and_workspace_is_refused(service, tmp_path):
    folder = _folder(tmp_path, "loom")
    project = service.project_create({"root": str(folder)})["project"]

    with pytest.raises(ValueError, match="not both"):
        service.thread_start({"projectId": project["id"], "workspace": str(folder)})


def test_removing_a_project_keeps_its_conversations(service, tmp_path):
    folder = _folder(tmp_path, "loom")
    project = service.project_create({"root": str(folder)})["project"]
    service.thread_start({"workspace": str(folder)})

    removed = service.project_remove({"projectId": project["id"]})

    assert removed["deletedThreads"] is False
    assert removed["deletedFiles"] is False
    assert folder.is_dir()
    # The conversation is still there; it is simply unfiled again.
    threads = service.thread_list({})["threads"]
    assert len(threads) == 1
    assert service.project_list({"adopt": False})["unfiledThreadCount"] == 1


def test_the_handshake_advertises_projects(service):
    controller = LoomRpcController(service)
    response = controller.handle(
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

    assert response["result"]["capabilities"]["projects"]["list"] is True


def test_project_methods_are_dispatchable(service, tmp_path):
    controller = LoomRpcController(service)
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

    created = controller.handle(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "project/create",
            "params": {"root": str(_folder(tmp_path, "loom"))},
        }
    )
    assert created["result"]["project"]["name"] == "loom"

    unknown = controller.handle(
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "project/rename",
            "params": {"projectId": "p000000000000", "name": "x"},
        }
    )
    assert unknown["error"]["code"] == -32004


def test_the_managed_service_also_reports_the_project(tmp_path: Path) -> None:
    """The desktop clients talk to the managed service, not the base one.

    Building records through the bare module helper there dropped ``projectId``
    from every listed thread, so every project rendered as empty while its
    conversations piled up under "No project".
    """
    from app.agent_runtime import FileAgentSessionStore
    from app.app_server_thread_management import ManagedStreamingLoomAppServerService

    workspace = _folder(tmp_path, "loom")
    store = FileAgentSessionStore(tmp_path / "home")
    runtime = DurableAgentRuntime(
        platform=SilentPlatform(),
        store=store,
        tools=ToolRegistry(()),
        default_permission_mode=PermissionMode.WORKSPACE,
        auto_drain_queue=False,
    )
    managed = ManagedStreamingLoomAppServerService(
        runtime=runtime,
        store=store,
        model="test-model",
        default_workspace=workspace,
        default_permission_mode=PermissionMode.WORKSPACE,
    )
    try:
        project = managed.project_create({"root": str(workspace)})["project"]
        managed.thread_start({"workspace": str(workspace)})

        listed = managed.thread_list({})["threads"]

        assert [thread["projectId"] for thread in listed] == [project["id"]]
        assert managed.project_list({})["projects"][0]["threadCount"] == 1
    finally:
        runtime.close()
