"""Child conversation branches remain durable, independent and reviewable."""
from __future__ import annotations

import time
from pathlib import Path

import pytest

from app.agent_runtime import DurableAgentRuntime, FileAgentSessionStore, PermissionMode, ToolRegistry
from app.app_server import JsonRpcError, PROTOCOL_VERSION
from app.app_server_thread_management import ManagedStreamingLoomAppServerService, ManagedStreamingLoomRpcController


class ReplyPlatform:
    def execute_chat(self, _profile_id, _request):
        from app.ai import ModelResponse
        return ModelResponse(text="Child task done: implemented and tested.")


@pytest.fixture()
def branch_service(tmp_path: Path):
    workspace = tmp_path / "project"
    workspace.mkdir()
    store = FileAgentSessionStore(tmp_path / "home")
    runtime = DurableAgentRuntime(
        platform=ReplyPlatform(),
        store=store, tools=ToolRegistry(()),
        default_permission_mode=PermissionMode.WORKSPACE,
        auto_drain_queue=False,
    )
    service = ManagedStreamingLoomAppServerService(
        runtime=runtime, store=store, model="test-model",
        default_workspace=workspace, default_permission_mode=PermissionMode.WORKSPACE,
    )
    yield service, runtime, store, workspace
    runtime.close()


def wait_for_idle(service, thread_id):
    limit = time.monotonic() + 8
    while service._is_active(thread_id) and time.monotonic() < limit:
        time.sleep(.015)
    assert not service._is_active(thread_id)


def test_child_creates_durable_parent_link_without_copying_history(branch_service):
    service, _, store, root = branch_service
    project = service.project_create({"root": str(root)})["project"]
    parent = service.thread_start({"projectId": project["id"]})["thread"]
    project_file = root / "README.md"
    project_file.write_text("unchanged", encoding="utf-8")
    created = service.thread_branch_create({
        "parentThreadId": parent["id"], "title": "Fix Windows CRLF test",
        "prompt": "Make CRLF assertions platform-agnostic.",
        "sourceMessageId": "assistant-message-1",
        "startNow": False,
    })
    child = created["thread"]
    assert created["started"] is False
    assert child["id"] != parent["id"]
    assert child["parentThreadId"] == parent["id"]
    assert child["workspace"] == parent["workspace"]
    assert child["projectId"] == parent["projectId"] == project["id"]
    assert child["permissionMode"] == parent["permissionMode"]
    assert child["branchSourceMessageId"] == "assistant-message-1"
    assert child["title"] == "Fix Windows CRLF test"
    assert service.thread_read({"threadId": child["id"]})["thread"]["parentThreadId"] == parent["id"]
    assert any(item["id"] == child["id"] and item["parentThreadId"] == parent["id"]
               for item in service.thread_list({"view": "active"})["threads"])
    assert "parentThreadId" not in (store.session_dir(child["id"]) / "session.json").read_text()
    assert project_file.read_text(encoding="utf-8") == "unchanged"


def test_launch_branch_and_report_to_idle_parent(branch_service):
    service, _, _, root = branch_service
    parent = service.thread_start({"workspace": str(root)})["thread"]
    created = service.thread_branch_create({
        "parentThreadId": parent["id"], "title": "Independent review", "prompt": "Review this.",
    })
    child_id = created["thread"]["id"]
    assert created["started"]
    wait_for_idle(service, child_id)
    result = service.thread_branch_report({"threadId": child_id})
    assert result["parentThreadId"] == parent["id"]
    assert service.thread_library.read(child_id)["branchReportedAt"]
    wait_for_idle(service, parent["id"])
    with pytest.raises(JsonRpcError):
        service.thread_branch_report({"threadId": child_id})
    with pytest.raises(JsonRpcError):
        service.thread_branch_report({"threadId": parent["id"]})


def test_invalid_task_parent_and_archived_parent_rejected(branch_service):
    service, _, _, root = branch_service
    parent = service.thread_start({"workspace": str(root)})["thread"]
    with pytest.raises(JsonRpcError):
        service.thread_branch_create({"parentThreadId": "invalid", "title": "hello", "prompt": "run"})
    with pytest.raises(JsonRpcError):
        service.thread_branch_create({"parentThreadId": parent["id"], "title": "hello", "prompt": ""})
    service.thread_archive({"threadId": parent["id"]})
    with pytest.raises(JsonRpcError):
        service.thread_branch_create({"parentThreadId": parent["id"], "title": "hello", "prompt": "run"})


def test_rpc_branch_endpoint_and_capability(branch_service):
    service, _, _, root = branch_service
    parent = service.thread_start({"workspace": str(root)})["thread"]
    controller = ManagedStreamingLoomRpcController(service)
    init = controller.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": PROTOCOL_VERSION, "clientInfo": {"name": "pytest", "version": "1"}}})
    assert init["result"]["capabilities"]["threadManagement"]["branches"] is True
    response = controller.handle({
        "jsonrpc": "2.0", "id": 2, "method": "thread/branch_create",
        "params": {"parentThreadId": parent["id"], "title": "UI case", "prompt": "Test", "startNow": False},
    })
    assert response["result"]["thread"]["parentThreadId"] == parent["id"]
