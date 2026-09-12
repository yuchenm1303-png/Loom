"""Project AGENTS/LOOM file reader powers the project details panel."""

from __future__ import annotations

from pathlib import Path

import pytest

import app.project_agent_files as project_agent_files
from app.agent_runtime import (
    DurableAgentRuntime,
    FileAgentSessionStore,
    PermissionMode,
    ToolRegistry,
)
from app.app_server import PROTOCOL_VERSION
from app.app_server_project_move import ProjectMovableLoomAppServerService, ProjectMovableLoomRpcController


project_agent_files.patch(__import__("app.app_server_project_move", fromlist=["ProjectMovableLoomAppServerService"]))


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


def test_project_agent_files_reads_known_instruction_files(service, tmp_path: Path) -> None:
    root = tmp_path / "loom"
    root.mkdir()
    (root / "AGENTS.md").write_text("# Agent Rules\n\nUse main directly.\n", encoding="utf-8")
    (root / "AGENTS.override.md").write_text("# Override\n\nNo branches.\n", encoding="utf-8")

    project = service.project_create({"root": str(root)})["project"]

    result = service.project_agent_files({"projectId": project["id"]})

    assert result["projectId"] == project["id"]
    assert result["availableCount"] == 2
    by_name = {item["name"]: item for item in result["files"]}
    assert by_name["AGENTS.md"]["exists"] is True
    assert by_name["AGENTS.md"]["readable"] is True
    assert "Use main directly" in by_name["AGENTS.md"]["content"]
    assert by_name["LOOM.md"]["exists"] is False


def test_project_agent_files_is_dispatchable_and_advertised(service, tmp_path: Path) -> None:
    root = tmp_path / "loom"
    root.mkdir()
    (root / "LOOM.md").write_text("Loom project context\n", encoding="utf-8")
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
    assert init["result"]["capabilities"]["projects"]["agentFiles"] is True

    response = controller.handle(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "project/agent_files",
            "params": {"projectId": project["id"]},
        }
    )
    files = response["result"]["files"]
    assert any(item["name"] == "LOOM.md" and item["readable"] for item in files)
