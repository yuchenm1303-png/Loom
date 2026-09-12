from __future__ import annotations

"""Read-only project AGENTS/LOOM instruction files for the project panel."""

import importlib.abc
import importlib.machinery
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

from app.projects import ProjectStoreError


_TARGET_MODULE = "app.app_server_project_move"
_INSTALLED = False
_AGENT_FILE_NAMES = ("AGENTS.md", "AGENTS.override.md", "LOOM.md")
_AGENT_FILE_MAX_BYTES = 96_000


def _safe_agent_file(root: Path, name: str) -> Path | None:
    if name not in _AGENT_FILE_NAMES:
        return None
    target = (root / name).resolve()
    try:
        target.relative_to(root.resolve())
    except ValueError:
        return None
    return target


def _read_agent_file(root: Path, name: str) -> dict[str, Any]:
    target = _safe_agent_file(root, name)
    if target is None:
        return {
            "name": name,
            "path": name,
            "exists": False,
            "readable": False,
            "size": 0,
            "content": "",
            "truncated": False,
            "error": "Unsupported project instruction file",
        }

    payload: dict[str, Any] = {
        "name": name,
        "path": name,
        "exists": target.exists(),
        "readable": False,
        "size": 0,
        "content": "",
        "truncated": False,
        "error": "",
    }
    if not target.exists():
        return payload
    if not target.is_file():
        payload["error"] = "Instruction path is not a file"
        return payload
    try:
        size = target.stat().st_size
        payload["size"] = size
        data = target.read_bytes()[:_AGENT_FILE_MAX_BYTES]
    except OSError as exc:
        payload["error"] = str(exc)
        return payload

    payload["truncated"] = size > _AGENT_FILE_MAX_BYTES
    if b"\x00" in data:
        payload["error"] = "Binary file omitted"
        return payload
    try:
        payload["content"] = data.decode("utf-8")
    except UnicodeDecodeError:
        payload["content"] = data.decode("utf-8", errors="replace")
    payload["readable"] = True
    return payload


def _root_or_rpc_error(service: Any, project_id: str) -> tuple[Any, Path]:
    if hasattr(service, "_project_root_or_error"):
        return service._project_root_or_error(project_id)

    JsonRpcError = getattr(sys.modules[_TARGET_MODULE], "JsonRpcError")
    try:
        project = service.projects.get(project_id)
    except KeyError as exc:
        raise JsonRpcError(-32004, "project not found") from exc
    except ProjectStoreError as exc:
        raise JsonRpcError(-32028, f"could not read project registry: {exc}") from exc

    root = Path(project.root).expanduser()
    try:
        resolved = root.resolve()
    except OSError:
        resolved = root.absolute()
    return project, resolved


def patch(module: Any) -> None:
    service_cls = module.ProjectMovableLoomAppServerService
    controller_cls = module.ProjectMovableLoomRpcController
    if getattr(service_cls, "_loom_project_agent_files_installed", False):
        return

    JsonRpcError = module.JsonRpcError
    original_initialize = controller_cls._initialize
    original_dispatch = controller_cls._dispatch

    def project_agent_files(self: Any, params: dict[str, Any]) -> dict[str, Any]:
        project_id = self._required_text(params, "projectId")
        project, root = _root_or_rpc_error(self, project_id)
        if not root.exists():
            raise JsonRpcError(-32032, "project folder does not exist")
        if not root.is_dir():
            raise JsonRpcError(-32033, "project root is not a directory")
        files = [_read_agent_file(root, name) for name in _AGENT_FILE_NAMES]
        return {
            "projectId": project.project_id,
            "projectName": project.name,
            "root": str(root),
            "files": files,
            "availableCount": sum(1 for item in files if item.get("exists")),
        }

    def initialize(self: Any, params: dict[str, Any]) -> dict[str, Any]:
        result = original_initialize(self, params)
        capabilities = dict(result.get("capabilities") or {})
        projects = dict(capabilities.get("projects") or {})
        projects["agentFiles"] = True
        capabilities["projects"] = projects
        result["capabilities"] = capabilities
        return result

    def dispatch(self: Any, method: str, params: dict[str, Any]) -> Any:
        if method == "project/agent_files":
            return self.service.project_agent_files(params)
        return original_dispatch(self, method, params)

    service_cls.project_agent_files = project_agent_files
    service_cls._loom_project_agent_files_installed = True
    controller_cls._initialize = initialize
    controller_cls._dispatch = dispatch


def _patch_loaded_target() -> None:
    target = sys.modules.get(_TARGET_MODULE)
    if target is not None:
        patch(target)


class _ProjectAgentFilesLoader(importlib.abc.Loader):
    def __init__(self, loader: importlib.abc.Loader) -> None:
        self.loader = loader

    def create_module(self, spec: importlib.machinery.ModuleSpec) -> ModuleType | None:
        create_module = getattr(self.loader, "create_module", None)
        if callable(create_module):
            return create_module(spec)
        return None

    def exec_module(self, module: ModuleType) -> None:
        exec_module = getattr(self.loader, "exec_module", None)
        if not callable(exec_module):
            raise ImportError(f"loader for {_TARGET_MODULE} cannot execute modules")
        exec_module(module)
        patch(module)


class _ProjectAgentFilesFinder(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname: str, path: Any, target: ModuleType | None = None):
        if fullname != _TARGET_MODULE:
            return None
        try:
            sys.meta_path.remove(self)
            spec = importlib.machinery.PathFinder.find_spec(fullname, path)
        finally:
            sys.meta_path.insert(0, self)
        if spec is None or spec.loader is None or isinstance(spec.loader, _ProjectAgentFilesLoader):
            return spec
        spec.loader = _ProjectAgentFilesLoader(spec.loader)  # type: ignore[arg-type]
        return spec


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    _patch_loaded_target()
    if _TARGET_MODULE not in sys.modules:
        sys.meta_path.insert(0, _ProjectAgentFilesFinder())
    _INSTALLED = True


__all__ = ["install", "patch"]
