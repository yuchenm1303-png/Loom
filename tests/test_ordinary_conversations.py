from __future__ import annotations

from pathlib import Path

from app.ai import ModelResponse
from app.agent_runtime import DurableAgentRuntime, FileAgentSessionStore, PermissionMode, ToolRegistry
from app.app_server_thread_management import ManagedStreamingLoomAppServerService


class RecordingPlatform:
    def __init__(self) -> None:
        self.requests = []

    def execute_chat(self, _profile_id, request):
        self.requests.append(request)
        return ModelResponse(text="ok")


def _build_service(tmp_path: Path):
    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = FileAgentSessionStore(home)
    runtime = DurableAgentRuntime(
        platform=RecordingPlatform(),
        store=store,
        tools=ToolRegistry(),
        default_permission_mode=PermissionMode.WORKSPACE,
        auto_drain_queue=False,
    )
    service = ManagedStreamingLoomAppServerService(
        runtime=runtime,
        store=store,
        model="test-model",
        default_workspace=workspace,
        default_permission_mode=PermissionMode.WORKSPACE,
    )
    return service, runtime, workspace


def test_top_level_new_thread_stays_in_recent_even_when_workspace_is_project(tmp_path: Path) -> None:
    service, runtime, workspace = _build_service(tmp_path)
    try:
        project = service.project_create({"root": str(workspace), "name": "Workspace Project"})["project"]

        ordinary = service.thread_start({})["thread"]
        assert ordinary["projectId"] == ""
        assert ordinary["conversationKind"] == "ordinary"
        assert service.thread_read({"threadId": ordinary["id"]})["thread"]["projectId"] == ""

        listed = service.thread_list({"view": "active"})["threads"]
        assert next(thread for thread in listed if thread["id"] == ordinary["id"])["projectId"] == ""

        projects = service.project_list({})["projects"]
        assert next(row for row in projects if row["id"] == project["id"])["threadCount"] == 0
    finally:
        runtime.close()


def test_project_new_thread_remains_in_that_project(tmp_path: Path) -> None:
    service, runtime, workspace = _build_service(tmp_path)
    try:
        project = service.project_create({"root": str(workspace), "name": "Workspace Project"})["project"]
        project_thread = service.thread_start({"projectId": project["id"]})["thread"]

        assert project_thread["projectId"] == project["id"]
        assert project_thread["conversationKind"] == "project"
        assert service.thread_read({"threadId": project_thread["id"]})["thread"]["projectId"] == project["id"]

        projects = service.project_list({})["projects"]
        assert next(row for row in projects if row["id"] == project["id"])["threadCount"] == 1
    finally:
        runtime.close()
