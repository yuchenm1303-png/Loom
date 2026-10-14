"""Regression tests for the local Files workbench RPC."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.agent_runtime import DurableAgentRuntime, FileAgentSessionStore, PermissionMode, ToolRegistry
from app.app_server import JsonRpcError, PROTOCOL_VERSION
from app.app_server_project_move import ProjectMovableLoomAppServerService, ProjectMovableLoomRpcController


class SilentPlatform:
    def execute_chat(self, *_args):
        raise AssertionError("tests must not invoke the agent")


@pytest.fixture()
def files_service(tmp_path: Path):
    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = FileAgentSessionStore(home)
    runtime = DurableAgentRuntime(
        platform=SilentPlatform(), store=store, tools=ToolRegistry(()),
        default_permission_mode=PermissionMode.WORKSPACE, auto_drain_queue=False,
    )
    service = ProjectMovableLoomAppServerService(
        runtime=runtime, store=store, model="test-model",
        default_workspace=workspace, default_permission_mode=PermissionMode.WORKSPACE,
    )
    root = tmp_path / "project"
    root.mkdir()
    project = service.project_create({"root": str(root)})["project"]
    yield service, project["id"], root
    runtime.close()


def _call(fixture, action: str, path: str = "", **more):
    service, project_id, _ = fixture
    return service.project_files({"projectId": project_id, "action": action, "path": path, **more})


def test_list_search_and_paging(files_service):
    _, _, root = files_service
    (root / "src").mkdir()
    (root / "src" / "nested").mkdir()
    (root / "src" / "nested" / "main.py").write_text("print('ok')\n", encoding="utf-8")
    (root / ".git").mkdir()
    (root / ".git" / "index").write_text("private", encoding="utf-8")
    (root / "notes.md").write_text("hello", encoding="utf-8")

    items = _call(files_service, "list", limit=1)
    assert len(items["entries"]) == 1
    assert items["nextOffset"] == 1
    rest = _call(files_service, "list", offset=items["nextOffset"])
    assert all(entry["name"] != ".git" for entry in rest["entries"])
    assert _call(files_service, "list", showIgnored=True)["entries"][0]["name"] == ".git"
    deep = _call(files_service, "list", "src/nested")
    assert [entry["path"] for entry in deep["entries"]] == ["src/nested/main.py"]
    hits = _call(files_service, "search", query="main.py")
    assert [entry["path"] for entry in hits["entries"]] == ["src/nested/main.py"]
    assert _call(files_service, "search", query="index")["entries"] == []


def test_edit_conflict_and_atomic_creation(files_service):
    _, _, root = files_service
    path = root / "notes.txt"
    path.write_text("before\n", encoding="utf-8")
    read = _call(files_service, "read", "notes.txt")
    assert read["text"] == "before\n"
    saved = _call(files_service, "write", "notes.txt", text="after\n", expectedSha=read["sha"])
    assert saved["sha"] == _call(files_service, "read", "notes.txt")["sha"]
    assert path.read_text(encoding="utf-8") == "after\n"

    with pytest.raises(JsonRpcError):
        _call(files_service, "write", "notes.txt", text="outdated", expectedSha=read["sha"])
    with pytest.raises(JsonRpcError):
        _call(files_service, "write", "notes.txt", text="missing revision")
    _call(files_service, "write", "new.txt", text="hi", expectedSha="")
    assert _call(files_service, "read", "new.txt")["text"] == "hi"
    with pytest.raises(JsonRpcError):
        _call(files_service, "write", "new.txt", text="overwrite", expectedSha="")


def test_rejects_outside_paths_symlinks_and_binary(files_service, tmp_path):
    _, _, root = files_service
    secret = tmp_path / "secret.txt"
    secret.write_text("secret", encoding="utf-8")
    (root / "binary.dat").write_bytes(b"\x00\xff")
    assert _call(files_service, "read", "binary.dat")["text"] is None

    for bad in ("../secret.txt", "/etc/passwd", r"C:\Windows\win.ini",
                r"..\secret.txt", "src//foo", "foo/../bar", "a\x00b"):
        with pytest.raises(JsonRpcError):
            _call(files_service, "read", bad)
    try:
        (root / "escape.txt").symlink_to(secret)
    except (OSError, NotImplementedError):
        pytest.skip("creating symlinks is not available")
    with pytest.raises(JsonRpcError):
        _call(files_service, "read", "escape.txt")


def test_folder_and_rename_delete_guards(files_service):
    _, _, root = files_service
    _call(files_service, "mkdir", "assets")
    _call(files_service, "write", "assets/icon.svg", text="<svg />", expectedSha="")
    with pytest.raises(JsonRpcError):
        _call(files_service, "delete", "assets")  # no recursive deletion
    renamed = _call(files_service, "rename", "assets/icon.svg", newPath="assets/logo.svg")
    assert renamed["path"] == "assets/logo.svg"
    assert (root / "assets" / "logo.svg").exists()
    with pytest.raises(JsonRpcError):
        _call(files_service, "rename", "assets", newPath="assets/inside")
    _call(files_service, "delete", "assets/logo.svg")
    _call(files_service, "delete", "assets")
    assert not (root / "assets").exists()


def test_rpc_dispatch_advertises_files(files_service):
    service, project_id, _ = files_service
    controller = ProjectMovableLoomRpcController(service)
    init = controller.handle({
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": PROTOCOL_VERSION,
                   "clientInfo": {"name": "pytest", "version": "1"}},
    })
    assert init["result"]["capabilities"]["projects"]["files"] is True
    response = controller.handle({
        "jsonrpc": "2.0", "id": 2, "method": "project/files",
        "params": {"projectId": project_id, "action": "list", "path": ""},
    })
    assert response["result"]["entries"] == []
