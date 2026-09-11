from __future__ import annotations

from pathlib import Path
from typing import Any, TextIO

from app.agent_runtime import AgentStatus, PermissionMode
from app.agent_runtime.storage import utc_now
from app.projects import ProjectStoreError

from .app_server import JsonRpcError
from .app_server_reasoning import (
    ReasoningManagedJsonRpcStdioServer,
    ReasoningManagedLoomAppServerService,
    ReasoningManagedLoomRpcController,
)


class ProjectMovableLoomAppServerService(ReasoningManagedLoomAppServerService):
    """Adds explicit per-thread project placement on top of workspace grouping.

    Historically a thread belonged to a project only when its workspace matched
    the project's root. That is useful as a migration fallback, but it makes an
    explicit "move to project" impossible and causes an unfiled thread to be
    auto-adopted again. A sidecar ``projectId`` now wins when it is present.
    """

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

    def thread_move_project(self, params: dict[str, Any]) -> dict[str, Any]:
        session = self._session_or_rpc_error(params.get("threadId"))
        if self._is_active(session.session_id) or session.status in {
            AgentStatus.RUNNING,
            AgentStatus.WAITING_APPROVAL,
        }:
            raise JsonRpcError(-32027, "cannot move a running thread between projects")

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
    def _dispatch(self, method: str, params: dict[str, Any]) -> Any:
        if method == "thread/move_project":
            return self.service.thread_move_project(params)
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
