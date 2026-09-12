from __future__ import annotations

"""Project-level Git staging and commit RPCs.

This module is installed from ``app.__init__`` and patches the project-aware app
server after it is imported. Keeping these operations here prevents
``app_server_project_move.py`` from turning into a large Git controller while
still exposing the project workspace controls expected by the desktop UI.
"""

import importlib.abc
import importlib.machinery
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

from app.agent_runtime import AgentStatus
from app.projects import ProjectStoreError


_TARGET_MODULE = "app.app_server_project_move"
_INSTALLED = False


def _safe_git_path(value: Any) -> str:
    text = str(value or "").replace("\\", "/").strip()
    if not text:
        return ""
    text = text.lstrip("/")
    parts = [part for part in text.split("/") if part and part != "."]
    if any(part == ".." for part in parts):
        raise ValueError("project git path must stay inside the project")
    return "/".join(parts)


def _run_git(root: Path, *args: str, timeout: float = 8) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(
            ["git", "-C", str(root), *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        return None


def _is_git_repo(root: Path) -> bool:
    result = _run_git(root, "rev-parse", "--is-inside-work-tree", timeout=3)
    return bool(result and result.returncode == 0 and result.stdout.strip().casefold() == "true")


def _head_sha(root: Path) -> str:
    result = _run_git(root, "rev-parse", "--short", "HEAD", timeout=3)
    if result and result.returncode == 0:
        return result.stdout.strip()
    return ""


def _has_staged_changes(root: Path) -> bool:
    result = _run_git(root, "diff", "--cached", "--quiet", timeout=5)
    if result is None:
        return False
    return result.returncode == 1


def _git_error(result: subprocess.CompletedProcess[str] | None, fallback: str) -> str:
    if result is None:
        return fallback
    message = (result.stderr or result.stdout or "").strip()
    return message or fallback


def _root_or_rpc_error(service: Any, project_id: str) -> tuple[Any, Path]:
    if hasattr(service, "_project_root_or_error"):
        return service._project_root_or_error(project_id)

    try:
        project = service.projects.get(project_id)
    except KeyError as exc:
        raise service.JsonRpcError(-32004, "project not found") from exc  # type: ignore[attr-defined]
    except ProjectStoreError as exc:
        raise service.JsonRpcError(-32028, f"could not read project registry: {exc}") from exc  # type: ignore[attr-defined]

    root = Path(project.root).expanduser()
    try:
        resolved = root.resolve()
    except OSError:
        resolved = root.absolute()
    return project, resolved


def _project_busy(service: Any, project_id: str) -> bool:
    for session in service._list_session_objects():
        try:
            if str(service._resolved_project_id(session) or "") != project_id:
                continue
        except Exception:
            continue
        if service._is_active(session.session_id) or session.status in {
            AgentStatus.RUNNING,
            AgentStatus.WAITING_APPROVAL,
        }:
            return True
    return False


def _git_status(service: Any, root: Path) -> dict[str, Any]:
    parser = getattr(sys.modules.get(_TARGET_MODULE), "_parse_git_status", None)
    if callable(parser):
        return parser(root)
    return {"available": True, "isRepo": _is_git_repo(root), "changedCount": 0, "changedFiles": []}


def _validate_project_repo(service: Any, params: dict[str, Any]) -> tuple[Any, Path, str]:
    JsonRpcError = getattr(sys.modules[_TARGET_MODULE], "JsonRpcError")
    project_id = service._required_text(params, "projectId")
    project, root = _root_or_rpc_error(service, project_id)
    if not root.exists():
        raise JsonRpcError(-32032, "project folder does not exist")
    if not root.is_dir():
        raise JsonRpcError(-32033, "project root is not a directory")
    if not _is_git_repo(root):
        raise JsonRpcError(-32035, "这个项目不是 Git 仓库，不能执行 Git 操作。")
    if _project_busy(service, project_id):
        raise JsonRpcError(-32036, "项目内有任务正在运行，结束后再执行 Git 操作。")
    return project, root, project_id


def patch(module: Any) -> None:
    service_cls = module.ProjectMovableLoomAppServerService
    controller_cls = module.ProjectMovableLoomRpcController
    if getattr(service_cls, "_loom_project_git_commit_installed", False):
        return

    JsonRpcError = module.JsonRpcError
    original_initialize = controller_cls._initialize
    original_dispatch = controller_cls._dispatch

    def project_git_stage(self: Any, params: dict[str, Any]) -> dict[str, Any]:
        project, root, project_id = _validate_project_repo(self, params)
        path = _safe_git_path(params.get("path"))
        args = ("add", "--", path) if path else ("add", "--all")
        result = _run_git(root, *args, timeout=12)
        if result is None or result.returncode != 0:
            raise JsonRpcError(-32037, _git_error(result, "git add failed"))
        return {
            "projectId": project_id,
            "projectName": project.name,
            "root": str(root),
            "path": path,
            "git": _git_status(self, root),
        }

    def project_git_unstage(self: Any, params: dict[str, Any]) -> dict[str, Any]:
        project, root, project_id = _validate_project_repo(self, params)
        path = _safe_git_path(params.get("path"))
        args = ("reset", "--", path) if path else ("reset", "--")
        result = _run_git(root, *args, timeout=12)
        if result is None or result.returncode != 0:
            raise JsonRpcError(-32038, _git_error(result, "git reset failed"))
        return {
            "projectId": project_id,
            "projectName": project.name,
            "root": str(root),
            "path": path,
            "git": _git_status(self, root),
        }

    def project_git_commit(self: Any, params: dict[str, Any]) -> dict[str, Any]:
        project, root, project_id = _validate_project_repo(self, params)
        message = str(params.get("message") or "").strip()
        if not message:
            raise JsonRpcError(-32602, "提交信息不能为空。")
        if len(message) > 500:
            raise JsonRpcError(-32602, "提交信息不能超过 500 个字符。")
        if not _has_staged_changes(root):
            raise JsonRpcError(-32039, "没有已暂存的变更。请先暂存文件再提交。")

        before = _head_sha(root)
        result = _run_git(root, "commit", "-m", message, timeout=20)
        if result is None or result.returncode != 0:
            raise JsonRpcError(-32040, _git_error(result, "git commit failed"))
        after = _head_sha(root)
        payload = {
            "projectId": project_id,
            "projectName": project.name,
            "root": str(root),
            "message": message,
            "before": before,
            "commitSha": after,
            "summary": (result.stdout or "").strip(),
            "git": _git_status(self, root),
        }
        self._notify("project/updated", {"projectId": project_id, "reason": "git_commit", "commitSha": after})
        return payload

    def initialize(self: Any, params: dict[str, Any]) -> dict[str, Any]:
        result = original_initialize(self, params)
        capabilities = dict(result.get("capabilities") or {})
        projects = dict(capabilities.get("projects") or {})
        projects.update({"gitStage": True, "gitUnstage": True, "gitCommit": True})
        capabilities["projects"] = projects
        result["capabilities"] = capabilities
        return result

    def dispatch(self: Any, method: str, params: dict[str, Any]) -> Any:
        if method == "project/git_stage":
            return self.service.project_git_stage(params)
        if method == "project/git_unstage":
            return self.service.project_git_unstage(params)
        if method == "project/git_commit":
            return self.service.project_git_commit(params)
        return original_dispatch(self, method, params)

    service_cls.project_git_stage = project_git_stage
    service_cls.project_git_unstage = project_git_unstage
    service_cls.project_git_commit = project_git_commit
    service_cls._loom_project_git_commit_installed = True
    controller_cls._initialize = initialize
    controller_cls._dispatch = dispatch


def _patch_loaded_target() -> None:
    target = sys.modules.get(_TARGET_MODULE)
    if target is not None:
        patch(target)


class _ProjectGitCommitLoader(importlib.abc.Loader):
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


class _ProjectGitCommitFinder(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname: str, path: Any, target: ModuleType | None = None):
        if fullname != _TARGET_MODULE:
            return None
        try:
            sys.meta_path.remove(self)
            spec = importlib.machinery.PathFinder.find_spec(fullname, path)
        finally:
            sys.meta_path.insert(0, self)
        if spec is None or spec.loader is None or isinstance(spec.loader, _ProjectGitCommitLoader):
            return spec
        spec.loader = _ProjectGitCommitLoader(spec.loader)  # type: ignore[arg-type]
        return spec


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    _patch_loaded_target()
    if _TARGET_MODULE not in sys.modules:
        sys.meta_path.insert(0, _ProjectGitCommitFinder())
    _INSTALLED = True


__all__ = ["install", "patch"]
