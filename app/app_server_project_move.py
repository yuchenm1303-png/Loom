from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any, Callable, TextIO

from app.ai import AIMessage, MessageRole
from app.agent_runtime import AgentEventKind, AgentStatus, PermissionMode
from app.agent_runtime.storage import utc_now
from app.projects import ProjectStoreError

from .app_server import JsonRpcError
from .app_server_reasoning import (
    ReasoningManagedJsonRpcStdioServer,
    ReasoningManagedLoomAppServerService,
    ReasoningManagedLoomRpcController,
)


_PROJECT_CONTEXT_MESSAGE_NAME = "loom_registered_project_instructions"
_TERMINAL_TURN_KINDS = {
    AgentEventKind.TURN_COMPLETED,
    AgentEventKind.TURN_FAILED,
    AgentEventKind.TURN_CANCELLED,
    AgentEventKind.TURN_INTERRUPTED,
    AgentEventKind.LIMIT_REACHED,
}
_WORKSPACE_TREE_LIMIT = 96
_WORKSPACE_TREE_DEPTH = 3
_PROJECT_DIFF_MAX_CHARS = 420_000
_PROJECT_DIFF_MAX_NEW_FILE_BYTES = 180_000
_WORKSPACE_TREE_SKIP_DIRS = {
    ".git",
    ".hg",
    ".svn",
    ".next",
    ".nuxt",
    ".turbo",
    ".cache",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "__pycache__",
    "node_modules",
    "dist",
    "build",
    "out",
    "target",
    ".venv",
    "venv",
    "env",
}


def _workspace_relative(root: Path, path: Path) -> str:
    try:
        value = path.resolve().relative_to(root.resolve())
    except (OSError, ValueError):
        value = path.relative_to(root)
    return value.as_posix()


def _workspace_tree(root: Path, *, limit: int = _WORKSPACE_TREE_LIMIT, max_depth: int = _WORKSPACE_TREE_DEPTH) -> dict[str, Any]:
    entries: list[dict[str, Any]] = []
    truncated = False

    def add_entry(path: Path, *, depth: int, entry_type: str) -> None:
        payload: dict[str, Any] = {
            "path": _workspace_relative(root, path),
            "name": path.name,
            "type": entry_type,
            "depth": depth,
        }
        if entry_type == "file":
            try:
                payload["size"] = path.stat().st_size
            except OSError:
                payload["size"] = 0
        entries.append(payload)

    def walk(directory: Path, depth: int) -> None:
        nonlocal truncated
        if len(entries) >= limit:
            truncated = True
            return
        try:
            children = sorted(
                directory.iterdir(),
                key=lambda item: (
                    1 if item.is_file() else 0,
                    item.name.casefold(),
                ),
            )
        except OSError:
            return

        for child in children:
            if len(entries) >= limit:
                truncated = True
                return
            name = child.name
            if name in _WORKSPACE_TREE_SKIP_DIRS:
                continue
            try:
                if child.is_symlink():
                    add_entry(child, depth=depth, entry_type="symlink")
                    continue
                is_dir = child.is_dir()
                is_file = child.is_file()
            except OSError:
                continue
            if not is_dir and not is_file:
                continue
            add_entry(child, depth=depth, entry_type="directory" if is_dir else "file")
            if is_dir and depth < max_depth:
                walk(child, depth + 1)

    walk(root, 0)
    return {
        "entries": entries,
        "truncated": truncated,
        "limit": limit,
        "maxDepth": max_depth,
    }


def _run_git(root: Path, *args: str, timeout: float = 3) -> subprocess.CompletedProcess[str] | None:
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


def _parse_git_status(root: Path) -> dict[str, Any]:
    inside = _run_git(root, "rev-parse", "--is-inside-work-tree")
    if inside is None:
        return {
            "available": False,
            "isRepo": False,
            "branch": "",
            "summary": "Git is not available",
            "changedCount": 0,
            "changedFiles": [],
            "truncated": False,
        }
    if inside.returncode != 0 or inside.stdout.strip().casefold() != "true":
        return {
            "available": True,
            "isRepo": False,
            "branch": "",
            "summary": "Not a Git repository",
            "changedCount": 0,
            "changedFiles": [],
            "truncated": False,
        }

    branch = ""
    branch_result = _run_git(root, "symbolic-ref", "--quiet", "--short", "HEAD")
    if branch_result is not None and branch_result.returncode == 0:
        branch = branch_result.stdout.strip()
    if not branch:
        head_result = _run_git(root, "rev-parse", "--short", "HEAD")
        if head_result is not None and head_result.returncode == 0:
            branch = head_result.stdout.strip()

    status_result = _run_git(root, "status", "--short", "--branch", "--untracked-files=normal")
    if status_result is None:
        return {
            "available": False,
            "isRepo": True,
            "branch": branch,
            "summary": branch,
            "changedCount": 0,
            "changedFiles": [],
            "truncated": False,
            "error": "Git status timed out or could not run",
        }

    lines = [line.rstrip("\n") for line in status_result.stdout.splitlines()]
    summary = branch
    changed: list[dict[str, Any]] = []
    for line in lines:
        if line.startswith("## "):
            summary = line[3:].strip()
            continue
        if not line.strip():
            continue
        status = line[:2] if len(line) >= 2 else line.strip()
        raw_path = line[3:].strip() if len(line) > 3 else ""
        path = raw_path.split(" -> ", 1)[-1].strip() if " -> " in raw_path else raw_path
        changed.append(
            {
                "path": path,
                "status": status.strip() or status,
                "index": status[:1].strip(),
                "workingTree": status[1:2].strip() if len(status) > 1 else "",
                "raw": line,
            }
        )

    max_changed = 80
    return {
        "available": True,
        "isRepo": True,
        "branch": branch,
        "summary": summary,
        "changedCount": len(changed),
        "changedFiles": changed[:max_changed],
        "truncated": len(changed) > max_changed,
        "error": "" if status_result.returncode == 0 else (status_result.stderr.strip() or "git status failed"),
    }


def _safe_git_path(value: Any) -> str:
    text = str(value or "").replace("\\", "/").strip()
    if not text:
        return ""
    text = text.lstrip("/")
    parts = [part for part in text.split("/") if part and part != "."]
    if any(part == ".." for part in parts):
        raise ValueError("project diff path must stay inside the project")
    return "/".join(parts)


def _new_file_unified_diff(root: Path, path: str) -> tuple[str, bool]:
    relative = _safe_git_path(path)
    if not relative:
        return "", False
    target = (root / relative).resolve()
    try:
        target.relative_to(root.resolve())
    except ValueError:
        return "", False
    if not target.is_file():
        return "", False
    try:
        data = target.read_bytes()
    except OSError:
        return "", False
    truncated = len(data) > _PROJECT_DIFF_MAX_NEW_FILE_BYTES
    sample = data[:_PROJECT_DIFF_MAX_NEW_FILE_BYTES]
    if b"\x00" in sample:
        return (
            "\n".join(
                [
                    f"diff --git a/{relative} b/{relative}",
                    "new file mode 100644",
                    "--- /dev/null",
                    f"+++ b/{relative}",
                    "@@ -0,0 +1 @@",
                    "+[Binary file omitted from inline review]",
                ]
            ),
            truncated,
        )
    try:
        text = sample.decode("utf-8")
    except UnicodeDecodeError:
        text = sample.decode("utf-8", errors="replace")
    lines = text.splitlines()
    hunk = f"@@ -0,0 +1,{len(lines)} @@"
    patch_lines = [
        f"diff --git a/{relative} b/{relative}",
        "new file mode 100644",
        "--- /dev/null",
        f"+++ b/{relative}",
        hunk,
        *[f"+{line}" for line in lines],
    ]
    if truncated:
        patch_lines.append("+[New file preview truncated]")
    return "\n".join(patch_lines), truncated


def _diff_for_changed_file(root: Path, path: str, status: str) -> tuple[str, bool]:
    relative = _safe_git_path(path)
    if not relative:
        return "", False
    parts: list[str] = []
    truncated = False
    for args in (
        ("diff", "--cached", "--no-ext-diff", "--unified=80", "--", relative),
        ("diff", "--no-ext-diff", "--unified=80", "--", relative),
    ):
        result = _run_git(root, *args, timeout=5)
        if result is not None and result.stdout.strip():
            parts.append(result.stdout.rstrip("\n"))
    if not parts and status.strip() == "??":
        preview, preview_truncated = _new_file_unified_diff(root, relative)
        if preview:
            parts.append(preview)
            truncated = truncated or preview_truncated
    return "\n".join(parts), truncated


def _project_git_diff(root: Path, *, path: str = "") -> dict[str, Any]:
    git = _parse_git_status(root)
    if not git.get("available") or not git.get("isRepo"):
        return {
            "root": str(root),
            "git": git,
            "path": path,
            "paths": [],
            "diff": "",
            "changedFiles": [],
            "truncated": False,
            "error": git.get("summary") or git.get("error") or "Git diff is not available for this project",
        }

    requested = _safe_git_path(path)
    changed_files = list(git.get("changedFiles") or [])
    if requested:
        changed_files = [
            file
            for file in changed_files
            if _safe_git_path(file.get("path")) == requested
        ]
        if not changed_files:
            changed_files = [{"path": requested, "status": "", "index": "", "workingTree": "", "raw": requested}]

    paths: list[str] = []
    chunks: list[str] = []
    truncated = False
    remaining = _PROJECT_DIFF_MAX_CHARS

    for file in changed_files:
        current_path = _safe_git_path(file.get("path"))
        if not current_path:
            continue
        diff, file_truncated = _diff_for_changed_file(root, current_path, str(file.get("status") or ""))
        if not diff.strip():
            continue
        paths.append(current_path)
        truncated = truncated or file_truncated
        if len(diff) > remaining:
            chunks.append(diff[:remaining].rstrip("\n"))
            truncated = True
            remaining = 0
            break
        chunks.append(diff.rstrip("\n"))
        remaining -= len(diff)
        if remaining <= 0:
            truncated = True
            break

    if truncated and chunks:
        chunks.append("\n[Project diff truncated]")

    return {
        "root": str(root),
        "git": git,
        "path": requested,
        "paths": paths,
        "diff": "\n".join(chunk for chunk in chunks if chunk),
        "changedFiles": changed_files,
        "truncated": truncated,
        "error": "",
    }


class ProjectMovableLoomAppServerService(ReasoningManagedLoomAppServerService):
    """Project placement plus product-facing Memory v2 management."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self._project_instruction_context_snapshots: dict[str, str] = {}
        super().__init__(*args, **kwargs)
        self._install_project_instruction_context()

    def _install_project_instruction_context(self) -> None:
        """Inject durable project instructions at model-request time.

        The Agent Runtime owns the durable conversation history, so project
        settings should not be written into ``session.system_prompt`` or old
        messages. Wrapping the request builder keeps project Instructions live:
        edits apply to the next turn, while the snapshot captured when a turn
        actually starts protects an already-running turn from mid-run settings
        changes.
        """

        base_prepare = getattr(self.runtime, "_loom_base_prepare_model_request", None)
        if not callable(base_prepare):
            base_prepare = getattr(self.runtime, "_prepare_model_request", None)
            if not callable(base_prepare):
                return
            setattr(self.runtime, "_loom_base_prepare_model_request", base_prepare)

        def prepare_with_project_context(session: Any, step: Any, token: Any) -> Any:
            prepared = base_prepare(session, step, token)
            if not isinstance(prepared, tuple) or len(prepared) != 2:
                return prepared
            messages, request_options = prepared
            return self._inject_project_instruction_message(session, messages), request_options

        setattr(self.runtime, "_prepare_model_request", prepare_with_project_context)

    def _project_instruction_context(self, session: Any) -> str:
        project_id = self._resolved_project_id(session)
        if not project_id:
            return ""
        try:
            project = self.projects.get(project_id)
        except (KeyError, ProjectStoreError):
            return ""
        instructions = str(getattr(project, "instructions", "") or "").strip()
        if not instructions:
            return ""
        return (
            "Loom Project Instructions\n"
            f"Project: {project.name}\n"
            f"Project root: {project.root}\n\n"
            "The following instructions come from this Loom project's settings. "
            "Apply them to tasks in this project. If the current user request conflicts with them, "
            "ask for clarification instead of silently ignoring the project instructions.\n\n"
            f"{instructions}"
        )

    def _project_instruction_context_for_request(self, session: Any) -> str:
        with self._guard:
            has_snapshot = session.session_id in self._project_instruction_context_snapshots
            snapshot = self._project_instruction_context_snapshots.get(session.session_id, "")
        if has_snapshot:
            return snapshot
        return self._project_instruction_context(session)

    def _snapshot_project_instruction_context(self, session: Any) -> None:
        context = self._project_instruction_context(session)
        with self._guard:
            self._project_instruction_context_snapshots[session.session_id] = context

    def _inject_project_instruction_message(self, session: Any, messages: Any) -> list[Any]:
        context = self._project_instruction_context_for_request(session)
        cleaned = [
            message
            for message in list(messages or [])
            if str(getattr(message, "name", "") or "") != _PROJECT_CONTEXT_MESSAGE_NAME
        ]
        if not context:
            return cleaned

        project_message = AIMessage(
            role=MessageRole.SYSTEM,
            name=_PROJECT_CONTEXT_MESSAGE_NAME,
            content=context,
        )
        for index, message in enumerate(cleaned):
            if getattr(message, "role", None) != MessageRole.SYSTEM:
                return [*cleaned[:index], project_message, *cleaned[index:]]
        return [*cleaned, project_message]

    def _on_runtime_event(self, event: Any) -> None:
        if event.kind is AgentEventKind.TURN_STARTED:
            try:
                self._snapshot_project_instruction_context(self.store.load(event.session_id))
            except Exception:
                pass
        try:
            super()._on_runtime_event(event)
        finally:
            if event.kind in _TERMINAL_TURN_KINDS:
                with self._guard:
                    self._project_instruction_context_snapshots.pop(event.session_id, None)

    def _sync_runtime_settings(self, snapshot: dict[str, Any] | None = None) -> dict[str, Any]:
        current = super()._sync_runtime_settings(snapshot)
        memory = dict(current.get("memory") or {})
        configure = getattr(self.runtime, "configure_memory", None)
        if callable(configure):
            configure(
                memory_enabled=bool(memory.get("enabled", True)),
                auto_extract=bool(memory.get("autoExtract", True)),
                semantic_auto=bool(memory.get("semanticAuto", True)),
                idle_seconds=int(memory.get("idleSeconds", 45) or 0),
            )
        return current

    def settings_set(self, params: dict[str, Any]) -> dict[str, Any]:
        # Protocol-v1 clients still send capability/enabled. Newer clients may
        # set a typed durable path directly without the legacy envelope hack.
        if "path" not in params:
            return super().settings_set(params)
        with self._guard:
            if self._active_sessions:
                raise RuntimeError("finish active turns before changing settings")
        path = self._required_text(params, "path")
        snapshot = self.settings.set_value(path, params.get("value"))
        self._sync_runtime_settings(snapshot)
        status = self.runtime_status()
        self._notify("runtime/updated", {"runtime": status})
        return {"settings": snapshot, "runtime": status}

    def _memory_call(self, name: str) -> Callable[..., Any]:
        method = getattr(self.runtime, name, None)
        if not callable(method):
            raise JsonRpcError(-32040, "memory is not available on this runtime")
        return method

    def memory_status(self, params: dict[str, Any]) -> dict[str, Any]:
        thread_id = self._required_text(params, "threadId")
        self._load(thread_id)
        result = self._memory_call("memory_status")(thread_id)
        return {"memory": dict(result) if isinstance(result, dict) else result}

    def memory_list(self, params: dict[str, Any]) -> dict[str, Any]:
        thread_id = self._required_text(params, "threadId")
        self._load(thread_id)
        limit = max(1, min(500, int(params.get("limit") or 100)))
        records = self._memory_call("list_memory")(thread_id, limit=limit)
        return {"memories": [record.to_dict() for record in records]}

    def memory_search(self, params: dict[str, Any]) -> dict[str, Any]:
        thread_id = self._required_text(params, "threadId")
        self._load(thread_id)
        query = self._required_text(params, "query")
        limit = max(1, min(32, int(params.get("limit") or 8)))
        records = self._memory_call("search_memory")(thread_id, query, limit=limit)
        return {
            "query": query,
            "memories": [record.to_dict() for record in records],
        }

    def memory_read(self, params: dict[str, Any]) -> dict[str, Any]:
        thread_id = self._required_text(params, "threadId")
        self._load(thread_id)
        memory_id = self._required_text(params, "memoryId")
        evidence_limit = max(1, min(100, int(params.get("evidenceLimit") or 20)))
        result = self._memory_call("read_memory")(
            thread_id,
            memory_id,
            evidence_limit=evidence_limit,
        )
        if result is None:
            raise JsonRpcError(-32044, "memory not found or not visible to this thread")
        record, evidence = result
        return {
            "memory": record.to_dict(),
            "evidence": [item.to_dict() for item in evidence],
        }

    def memory_forget(self, params: dict[str, Any]) -> dict[str, Any]:
        thread_id = self._required_text(params, "threadId")
        self._load(thread_id)
        memory_id = self._required_text(params, "memoryId")
        forgotten = bool(self._memory_call("forget_memory")(thread_id, memory_id))
        return {"memoryId": memory_id, "forgotten": forgotten}

    def _explicit_project_id(self, session: Any) -> str | None:
        metadata = self.thread_library.read(session.session_id)
        if "projectId" not in metadata:
            return None
        return str(metadata.get("projectId") or "").strip()

    def _resolved_project_id(self, session: Any) -> str:
        explicit = self._explicit_project_id(session)
        if explicit is not None:
            if not explicit:
                return ""
            try:
                self.projects.get(explicit)
            except (KeyError, ProjectStoreError):
                return ""
            return explicit

        try:
            project = self.projects.for_workspace(session.workspace_dir)
        except ProjectStoreError:
            return ""
        return project.project_id if project is not None else ""

    def _record(self, session: Any, *, active: bool = False) -> dict[str, Any]:
        record = super()._record(session, active=active)
        record["projectId"] = self._resolved_project_id(session)
        return record

    def _project_thread_count(self, project_id: str) -> int:
        count = 0
        for session in self._list_session_objects():
            if self._resolved_project_id(session) == project_id:
                count += 1
        return count

    def project_list(self, params: dict[str, Any]) -> dict[str, Any]:
        sessions = self._list_session_objects()

        # Preserve the legacy no-setup migration only for threads that have
        # never had an explicit project choice. Once the user moves a thread to
        # "No project", listing projects must not silently file it again.
        if bool(params.get("adopt", True)):
            implicit_workspaces = [
                session.workspace_dir
                for session in sessions
                if self._explicit_project_id(session) is None
            ]
            self.projects.adopt(dict.fromkeys(implicit_workspaces), now=utc_now())

        counts: dict[str, int] = {}
        unfiled = 0
        for session in sessions:
            project_id = self._resolved_project_id(session)
            if not project_id:
                unfiled += 1
                continue
            counts[project_id] = counts.get(project_id, 0) + 1

        return {
            "projects": [
                dict(project.as_dict(), threadCount=counts.get(project.project_id, 0))
                for project in self.projects.list()
            ],
            "unfiledThreadCount": unfiled,
        }

    def project_set_instructions(self, params: dict[str, Any]) -> dict[str, Any]:
        project_id = self._required_text(params, "projectId")
        instructions = str(params.get("instructions") or "")
        try:
            project = self.projects.set_instructions(project_id, instructions, now=utc_now())
        except KeyError as exc:
            raise JsonRpcError(-32004, "project not found") from exc
        except (ProjectStoreError, ValueError) as exc:
            raise JsonRpcError(-32031, f"could not save project instructions: {exc}") from exc
        payload = dict(project.as_dict(), threadCount=self._project_thread_count(project.project_id))
        self._notify("project/updated", {"project": payload, "reason": "instructions_changed"})
        return {"project": payload}

    def _project_root_or_error(self, project_id: str) -> tuple[Any, Path]:
        try:
            project = self.projects.get(project_id)
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

    def project_workspace_status(self, params: dict[str, Any]) -> dict[str, Any]:
        project_id = self._required_text(params, "projectId")
        project, resolved = self._project_root_or_error(project_id)

        exists = resolved.exists()
        is_directory = resolved.is_dir()
        payload: dict[str, Any] = {
            "projectId": project.project_id,
            "root": str(resolved),
            "exists": exists,
            "isDirectory": is_directory,
            "git": {
                "available": False,
                "isRepo": False,
                "branch": "",
                "summary": "",
                "changedCount": 0,
                "changedFiles": [],
                "truncated": False,
            },
            "tree": {
                "entries": [],
                "truncated": False,
                "limit": _WORKSPACE_TREE_LIMIT,
                "maxDepth": _WORKSPACE_TREE_DEPTH,
            },
        }
        if not exists:
            payload["error"] = "Project folder does not exist"
            return payload
        if not is_directory:
            payload["error"] = "Project root is not a directory"
            return payload

        payload["git"] = _parse_git_status(resolved)
        payload["tree"] = _workspace_tree(resolved)
        return payload

    def project_git_diff(self, params: dict[str, Any]) -> dict[str, Any]:
        project_id = self._required_text(params, "projectId")
        project, resolved = self._project_root_or_error(project_id)
        if not resolved.exists():
            raise JsonRpcError(-32032, "project folder does not exist")
        if not resolved.is_dir():
            raise JsonRpcError(-32033, "project root is not a directory")
        try:
            path = _safe_git_path(params.get("path"))
        except ValueError as exc:
            raise JsonRpcError(-32602, str(exc)) from exc
        payload = _project_git_diff(resolved, path=path)
        payload["projectId"] = project.project_id
        payload["projectName"] = project.name
        return payload

    def thread_move_project(self, params: dict[str, Any]) -> dict[str, Any]:
        session = self._session_or_rpc_error(params.get("threadId"))
        if self._is_active(session.session_id) or session.status in {
            AgentStatus.RUNNING,
            AgentStatus.WAITING_APPROVAL,
        }:
            raise JsonRpcError(-32027, "当前任务运行中，结束后才能移动这个对话到项目。")

        project_id = str(params.get("projectId") or "").strip()
        target_workspace: str | None = None
        if project_id:
            try:
                project = self.projects.get(project_id)
            except KeyError as exc:
                raise JsonRpcError(-32004, "project not found") from exc
            except ProjectStoreError as exc:
                raise JsonRpcError(-32028, f"could not read project registry: {exc}") from exc
            target_workspace = project.root

        old_workspace = session.workspace_dir
        workspace_changed = bool(target_workspace and target_workspace != old_workspace)
        if workspace_changed:
            session.workspace_dir = str(target_workspace)
            try:
                self.store.save(session)
            except (OSError, ValueError) as exc:
                session.workspace_dir = old_workspace
                raise JsonRpcError(-32029, f"could not move thread workspace: {exc}") from exc

        try:
            self.thread_library.write(session.session_id, {"projectId": project_id})
        except (OSError, ValueError) as exc:
            if workspace_changed:
                session.workspace_dir = old_workspace
                try:
                    self.store.save(session)
                except (OSError, ValueError):
                    pass
            raise JsonRpcError(-32030, f"could not save thread project: {exc}") from exc

        record = self._managed_record(session)
        self._notify("thread/updated", {"thread": record, "reason": "project_moved"})
        return {"thread": record}

    def project_remove(self, params: dict[str, Any]) -> dict[str, Any]:
        project_id = self._required_text(params, "projectId")
        sessions = self._list_session_objects()
        affected = [
            session
            for session in sessions
            if self._resolved_project_id(session) == project_id
        ]

        result = super().project_remove(params)

        # A removed project must stay removed. Explicitly mark every former
        # member as unfiled so project/list does not auto-adopt the same root on
        # the next refresh.
        for session in affected:
            try:
                self.thread_library.write(session.session_id, {"projectId": ""})
            except (OSError, ValueError):
                continue
            self._notify(
                "thread/updated",
                {"thread": self._managed_record(session), "reason": "project_removed"},
            )
        return result


class ProjectMovableLoomRpcController(ReasoningManagedLoomRpcController):
    def _initialize(self, params: dict[str, Any]) -> dict[str, Any]:
        result = super()._initialize(params)
        capabilities = dict(result.get("capabilities") or {})
        capabilities["projects"] = {
            "list": True,
            "create": True,
            "rename": True,
            "remove": True,
            "moveThread": True,
            "instructions": True,
            "workspaceStatus": True,
            "gitDiff": True,
        }
        capabilities["memory"] = {
            "status": True,
            "list": True,
            "search": True,
            "read": True,
            "forget": True,
            "settings": True,
        }
        result["capabilities"] = capabilities
        return result

    def _dispatch(self, method: str, params: dict[str, Any]) -> Any:
        if method == "thread/move_project":
            return self.service.thread_move_project(params)
        if method == "project/set_instructions":
            return self.service.project_set_instructions(params)
        if method == "project/workspace_status":
            return self.service.project_workspace_status(params)
        if method == "project/git_diff":
            return self.service.project_git_diff(params)
        if method == "memory/status":
            return self.service.memory_status(params)
        if method == "memory/list":
            return self.service.memory_list(params)
        if method == "memory/search":
            return self.service.memory_search(params)
        if method == "memory/read":
            return self.service.memory_read(params)
        if method == "memory/forget":
            return self.service.memory_forget(params)
        return super()._dispatch(method, params)


class ProjectMovableJsonRpcStdioServer(ReasoningManagedJsonRpcStdioServer):
    def __init__(self, service: ProjectMovableLoomAppServerService, **kwargs: Any) -> None:
        super().__init__(service, **kwargs)
        self.controller = ProjectMovableLoomRpcController(service)


def serve_project_managed_streaming_stdio(
    *,
    runtime: Any,
    store: Any,
    model: str,
    default_workspace: str | Path,
    default_permission_mode: PermissionMode | str,
    vision: bool = True,
    reader: TextIO | None = None,
    writer: TextIO | None = None,
) -> int:
    setattr(runtime, "supports_vision", bool(vision))
    service = ProjectMovableLoomAppServerService(
        runtime=runtime,
        store=store,
        model=model,
        default_workspace=default_workspace,
        default_permission_mode=default_permission_mode,
        vision=bool(vision),
    )
    server = ProjectMovableJsonRpcStdioServer(service)
    try:
        return server.serve(reader=reader, writer=writer)
    finally:
        close = getattr(runtime, "close", None)
        if callable(close):
            close()


__all__ = [
    "ProjectMovableLoomAppServerService",
    "ProjectMovableLoomRpcController",
    "ProjectMovableJsonRpcStdioServer",
    "serve_project_managed_streaming_stdio",
]
