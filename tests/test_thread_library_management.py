from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.agent_runtime import (
    DurableAgentRuntime,
    FileAgentSessionStore,
    PermissionMode,
    ToolRegistry,
)
from app.app_server import JsonRpcError, PROTOCOL_VERSION
from app.app_server_thread_management import (
    ManagedStreamingLoomAppServerService,
    ManagedStreamingLoomRpcController,
)


class RecordingPlatform:
    def execute_chat(self, _profile_id, _request):
        raise AssertionError("model execution is not expected in thread-library tests")


def _build_service(tmp_path: Path):
    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = FileAgentSessionStore(home)
    runtime = DurableAgentRuntime(
        platform=RecordingPlatform(),
        store=store,
        tools=ToolRegistry(()),
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
    return service, runtime, store, workspace


def test_thread_library_rename_archive_restore_and_read_only_boundary(tmp_path: Path) -> None:
    service, runtime, store, workspace = _build_service(tmp_path)
    try:
        first = service.thread_start({"workspace": str(workspace)})["thread"]
        second = service.thread_start({"workspace": str(workspace)})["thread"]
        first_id = first["id"]
        second_id = second["id"]

        renamed = service.thread_rename(
            {"threadId": first_id, "title": "  Memory   investigation  "}
        )["thread"]
        assert renamed["title"] == "Memory investigation"
        assert renamed["customTitle"] is True
        assert renamed["archived"] is False

        # Client-library metadata is deliberately sidecar state: Runtime's
        # canonical session snapshot is not rewritten with presentation fields.
        session_payload = json.loads(
            (store.session_dir(first_id) / "session.json").read_text(encoding="utf-8")
        )
        assert "title" not in session_payload
        assert "archivedAt" not in session_payload
        assert (store.session_dir(first_id) / "thread-library.json").is_file()

        archived = service.thread_archive({"threadId": first_id, "archived": True})["thread"]
        assert archived["archived"] is True
        assert archived["archivedAt"]

        active = service.thread_list({"view": "active", "limit": 200})
        archived_list = service.thread_list({"view": "archived", "limit": 200})
        assert [thread["id"] for thread in active["threads"]] == [second_id]
        assert [thread["id"] for thread in archived_list["threads"]] == [first_id]
        assert archived_list["counts"] == {"active": 1, "archived": 1, "all": 2}

        read = service.thread_read({"threadId": first_id})
        assert read["thread"]["title"] == "Memory investigation"
        assert read["thread"]["archived"] is True
        with pytest.raises(JsonRpcError) as exc_info:
            service.turn_start({"threadId": first_id, "input": "continue"})
        assert exc_info.value.code == -32023

        restored = service.thread_archive({"threadId": first_id, "archived": False})["thread"]
        assert restored["archived"] is False
        assert {thread["id"] for thread in service.thread_list({})["threads"]} == {
            first_id,
            second_id,
        }
    finally:
        runtime.close()


def test_thread_library_search_delete_and_protocol_capability(tmp_path: Path) -> None:
    service, runtime, store, workspace = _build_service(tmp_path)
    try:
        created = service.thread_start({"workspace": str(workspace)})["thread"]
        thread_id = created["id"]
        service.thread_rename({"threadId": thread_id, "title": "Screenshot diagnostics"})

        found = service.thread_list({"view": "active", "query": "screenSHOT", "limit": 200})
        assert [thread["id"] for thread in found["threads"]] == [thread_id]
        assert service.thread_list({"query": "does-not-exist"})["threads"] == []

        controller = ManagedStreamingLoomRpcController(service)
        initialized = controller.handle(
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
        assert initialized["result"]["capabilities"]["threadManagement"] == {
            "rename": True,
            "archive": True,
            "delete": True,
            "search": True,
            "permissionMode": True,
            "autoTitle": True,
        }

        deleted = controller.handle(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "thread/delete",
                "params": {"threadId": thread_id},
            }
        )
        assert deleted["result"] == {"deleted": True, "threadId": thread_id}
        assert not store.session_dir(thread_id).exists()
        assert workspace.exists()
    finally:
        runtime.close()


def test_thread_permission_profile_updates_and_persists(tmp_path: Path) -> None:
    service, runtime, store, workspace = _build_service(tmp_path)
    notifications: list[tuple[str, dict]] = []
    service.subscribe_notifications(lambda method, params: notifications.append((method, params)))
    try:
        created = service.thread_start(
            {"workspace": str(workspace), "permissionMode": PermissionMode.APPROVAL.value}
        )["thread"]
        thread_id = created["id"]
        assert created["permissionMode"] == PermissionMode.APPROVAL.value

        controller = ManagedStreamingLoomRpcController(service)
        initialized = controller.handle(
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
        assert initialized["result"]["capabilities"]["threadManagement"]["permissionMode"] is True

        changed = controller.handle(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "thread/set_permission_mode",
                "params": {
                    "threadId": thread_id,
                    "permissionMode": PermissionMode.FULL_ACCESS.value,
                },
            }
        )
        assert changed["result"]["thread"]["permissionMode"] == PermissionMode.FULL_ACCESS.value
        assert store.load(thread_id).permission_mode is PermissionMode.FULL_ACCESS
        assert service.thread_read({"threadId": thread_id})["thread"]["permissionMode"] == PermissionMode.FULL_ACCESS.value
        assert any(
            method == "thread/updated"
            and params.get("reason") == "permission_changed"
            and params.get("thread", {}).get("permissionMode") == PermissionMode.FULL_ACCESS.value
            for method, params in notifications
        )

        invalid = controller.handle(
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "thread/set_permission_mode",
                "params": {"threadId": thread_id, "permissionMode": "not-a-profile"},
            }
        )
        assert invalid["error"]["code"] == -32602
        assert PermissionMode.APPROVAL.value in invalid["error"]["data"]["supported"]
        assert store.load(thread_id).permission_mode is PermissionMode.FULL_ACCESS
    finally:
        runtime.close()


def test_thread_delete_refuses_nonempty_internal_workspace(tmp_path: Path) -> None:
    service, runtime, store, workspace = _build_service(tmp_path)
    try:
        created = service.thread_start({"workspace": str(workspace)})["thread"]
        thread_id = created["id"]
        session = store.load(thread_id)
        internal = store.workspace_dir(thread_id)
        (internal / "keep.txt").write_text("keep", encoding="utf-8")
        session.workspace_dir = str(internal)
        store.save(session)

        with pytest.raises(JsonRpcError) as exc_info:
            service.thread_delete({"threadId": thread_id})
        assert exc_info.value.code == -32024
        assert (internal / "keep.txt").read_text(encoding="utf-8") == "keep"
    finally:
        runtime.close()
