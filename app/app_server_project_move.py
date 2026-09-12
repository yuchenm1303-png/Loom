from __future__ import annotations

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
        starts protects an already-running turn from mid-run settings changes.
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

    def turn_start(self, params: dict[str, Any]) -> dict[str, Any]:
        thread_id = str(params.get("threadId") or "").strip()
        if thread_id:
            self._snapshot_project_instruction_context(self._session_or_rpc_error(thread_id))
        return super().turn_start(params)

    def _on_runtime_event(self, event: Any) -> None:
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
